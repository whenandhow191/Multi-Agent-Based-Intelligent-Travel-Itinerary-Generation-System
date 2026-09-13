"""C26 verified final aggregation with deterministic JSON and Markdown rendering."""

import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from packages.agents import DestinationIntelArtifact, MobilityLodgingArtifact
from packages.domain import (
    CollaborationSummary,
    CostEstimate,
    CostKind,
    DataFreshnessSummary,
    Evidence,
    FinalPlanBundle,
    Freshness,
    ItineraryVersion,
    PlanCandidatesArtifact,
    PlanComparison,
    PlanComparisonEntry,
    ReviewArtifact,
    ReviewVerdict,
    Route,
    TripRequest,
    validate_itinerary,
)


class FinalAggregationError(ValueError):
    """Stable fail-closed finalization error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FinalAggregationResult(BaseModel):
    """One validated source bundle and its deterministic serializations."""

    model_config = ConfigDict(frozen=True)

    bundle: FinalPlanBundle
    json_text: str
    markdown: str


class FinalAggregator:
    """Fail closed on review/reference errors, then render without a language model."""

    def aggregate(
        self,
        *,
        request: TripRequest,
        candidates: PlanCandidatesArtifact,
        review: ReviewArtifact,
        destination: DestinationIntelArtifact,
        mobility: MobilityLodgingArtifact,
        routes: Sequence[Route],
        generated_at: datetime,
        revision_rounds: int = 0,
    ) -> FinalAggregationResult:
        run_ids = {candidates.run_id, review.run_id, destination.run_id, mobility.run_id}
        if len(run_ids) != 1:
            raise FinalAggregationError("run_mismatch", "final inputs belong to different runs")
        if review.verdict is not ReviewVerdict.PASS:
            raise FinalAggregationError(
                "review_not_passed", "only a passing review can be finalized"
            )

        plan_ids = tuple(plan.plan_id for plan in candidates.plans)
        if set(review.reviewed_plan_ids) != set(plan_ids):
            raise FinalAggregationError(
                "review_coverage_mismatch", "review must cover every finalized candidate"
            )
        expected_dates = tuple(
            request.start_date + timedelta(days=offset) for offset in range(request.duration_days)
        )
        evidence = self._merge_evidence(destination.evidence, mobility.evidence)
        verified_plans = []
        for plan in candidates.plans:
            if tuple(day.date for day in plan.days) != expected_dates:
                raise FinalAggregationError(
                    "incomplete_trip_dates", f"plan {plan.plan_id} does not cover every trip date"
                )
            report = validate_itinerary(
                plan,
                request,
                places=destination.places,
                routes=routes,
                claims=destination.claims,
                evidence=evidence,
            )
            if not report.passed:
                codes = ", ".join(issue.code.value for issue in report.errors)
                raise FinalAggregationError(
                    "deterministic_validation_failed",
                    f"plan {plan.plan_id} failed final validation: {codes}",
                )
            verified_plans.append(
                plan.model_copy(update={"score_breakdown": review.plan_scores[plan.plan_id]})
            )

        plans = tuple(verified_plans)
        comparison = self._build_comparison(request, plans, evidence, mobility)
        assumptions = tuple(dict.fromkeys((*destination.unknowns, *mobility.unknowns)))
        bundle = FinalPlanBundle(
            run_id=candidates.run_id,
            request_summary=request,
            assumptions=assumptions,
            plans=plans,
            comparison=comparison,
            evidence=evidence,
            collaboration_summary=CollaborationSummary(
                participating_agents=(
                    "coordinator",
                    "destination_intelligence",
                    "mobility_lodging",
                    "itinerary_planner",
                    "critic",
                ),
                completed_task_count=7 + revision_rounds * 2,
                revision_rounds=revision_rounds,
                notes=("最终输出由确定性模板生成，未调用润色模型。",),
            ),
            generated_at=generated_at,
            data_freshness=self._freshness(evidence),
        )
        json_text = json.dumps(
            bundle.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        markdown = self.render_markdown(bundle)
        return FinalAggregationResult(bundle=bundle, json_text=json_text, markdown=markdown)

    @staticmethod
    def _merge_evidence(*groups: Sequence[Evidence]) -> tuple[Evidence, ...]:
        by_id: dict[str, Evidence] = {}
        for evidence in (item for group in groups for item in group):
            existing = by_id.get(evidence.evidence_id)
            if existing is not None and existing != evidence:
                raise FinalAggregationError(
                    "evidence_conflict",
                    f"evidence {evidence.evidence_id} has conflicting definitions",
                )
            by_id[evidence.evidence_id] = evidence
        if not by_id:
            raise FinalAggregationError("missing_evidence", "final output requires evidence")
        return tuple(by_id[evidence_id] for evidence_id in sorted(by_id))

    @staticmethod
    def _build_comparison(
        request: TripRequest,
        plans: Sequence[ItineraryVersion],
        evidence: Sequence[Evidence],
        mobility: MobilityLodgingArtifact,
    ) -> PlanComparison:
        known_evidence = {item.evidence_id for item in evidence}
        window = request.hard_constraints.daily_time_window
        window_minutes = (
            datetime.combine(date.min, window.end) - datetime.combine(date.min, window.start)
        ).seconds // 60
        entries = []
        for plan in plans:
            items = [item for day in plan.days for item in day.items]
            commute = sum(item.travel_minutes_from_previous or 0 for item in items)
            activity = sum(
                int((item.end_at - item.start_at).total_seconds() // 60) for item in items
            )
            used_evidence = [evidence_id for item in items for evidence_id in item.evidence_ids]
            coverage = (
                sum(evidence_id in known_evidence for evidence_id in used_evidence)
                / len(used_evidence)
                if used_evidence
                else 0
            )
            entries.append(
                PlanComparisonEntry(
                    plan_id=plan.plan_id,
                    total_cost=plan.total_cost,
                    activity_count=len(items),
                    commute_minutes=commute,
                    free_minutes=max(0, window_minutes * len(plan.days) - activity - commute),
                    preference_coverage=plan.score_breakdown.preference_match,
                    risk_count=len(plan.unresolved_risks) + len(mobility.connection_risks),
                    evidence_coverage=round(coverage, 4),
                )
            )
        return PlanComparison(entries=tuple(entries))

    @staticmethod
    def _freshness(evidence: Sequence[Evidence]) -> DataFreshnessSummary:
        values = {item.freshness for item in evidence}
        if Freshness.UNKNOWN in values:
            overall = Freshness.UNKNOWN
        elif Freshness.STALE in values:
            overall = Freshness.STALE
        elif values == {Freshness.LIVE}:
            overall = Freshness.LIVE
        else:
            overall = Freshness.FRESH
        return DataFreshnessSummary(
            overall=overall,
            oldest_retrieved_at=min(item.retrieved_at for item in evidence),
            stale_evidence_count=sum(item.freshness is Freshness.STALE for item in evidence),
            unknown_evidence_count=sum(item.freshness is Freshness.UNKNOWN for item in evidence),
        )

    def render_markdown(self, bundle: FinalPlanBundle) -> str:
        """Render stable human-readable output from the validated bundle only."""

        lines = [
            f"# {bundle.request_summary.destination} 行程方案",
            "",
            (
                f"行程：{bundle.request_summary.start_date.isoformat()} 至 "
                f"{bundle.request_summary.resolved_end_date.isoformat()}；"
                f"人数：{bundle.request_summary.party.size}；"
                f"预算：{bundle.request_summary.budget.total} "
                f"{bundle.request_summary.budget.currency}。"
            ),
            "",
        ]
        if bundle.assumptions:
            lines.extend(("## 执行前确认", ""))
            lines.extend(f"- {item}" for item in bundle.assumptions)
            lines.append("")
        lines.extend(
            (
                "## 方案比较",
                "",
                "| 方案 | 活动 | 通勤 | 空闲 | 费用 | 总分 |",
                "|---|---:|---:|---:|---:|---:|",
            )
        )
        comparison = {entry.plan_id: entry for entry in bundle.comparison.entries}
        for plan in bundle.plans:
            entry = comparison[plan.plan_id]
            lines.append(
                f"| {plan.title} | {entry.activity_count} | {entry.commute_minutes} 分钟 | "
                f"{entry.free_minutes} 分钟 | {self._format_cost(entry.total_cost)} | "
                f"{plan.score_breakdown.weighted_total:.4f} |"
            )
        lines.append("")
        for plan in bundle.plans:
            lines.extend((f"## {plan.title}", ""))
            for day in plan.days:
                lines.extend(
                    (
                        f"### {day.date.isoformat()}",
                        "",
                        "| 时间 | 活动 | 交通 | 费用 |",
                        "|---|---|---|---:|",
                    )
                )
                for item in day.items:
                    if item.route_from_previous_id is None:
                        travel = "—"
                    else:
                        assert item.travel_mode_from_previous is not None
                        assert item.travel_minutes_from_previous is not None
                        travel = (
                            f"{item.travel_mode_from_previous.value} "
                            f"{item.travel_minutes_from_previous} 分钟"
                        )
                    lines.append(
                        f"| {item.start_at:%H:%M}–{item.end_at:%H:%M} | {item.activity} | "
                        f"{travel} | {self._format_cost(item.estimated_cost)} |"
                    )
                lines.append("")
        lines.extend(
            ("## 数据来源", "", "| Evidence | 来源 | 获取时间 | 新鲜度 |", "|---|---|---|---|")
        )
        for evidence in bundle.evidence:
            lines.append(
                f"| {evidence.evidence_id} | {evidence.source_name} | "
                f"{evidence.retrieved_at.isoformat()} | {evidence.freshness.value} |"
            )
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _format_cost(cost: CostEstimate) -> str:
        if cost.kind is CostKind.UNKNOWN:
            return f"未知 {cost.currency}"
        assert cost.lower is not None and cost.upper is not None
        if cost.lower == cost.upper:
            return f"{cost.lower} {cost.currency}"
        return f"{cost.lower}–{cost.upper} {cost.currency}"
