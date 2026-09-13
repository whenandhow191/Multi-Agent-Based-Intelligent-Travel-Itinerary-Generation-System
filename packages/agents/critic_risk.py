"""A4 deterministic critic, risk review, and repair routing."""

from datetime import datetime

from packages.agents.destination_intelligence import DestinationIntelArtifact
from packages.agents.mobility_lodging import MobilityLodgingArtifact
from packages.domain import (
    ClaimStatus,
    HardViolation,
    IssueSeverity,
    PatchRequest,
    PlanCandidatesArtifact,
    ReviewArtifact,
    ReviewVerdict,
    Route,
    ScoreBreakdown,
    TripRequest,
    ValidationCode,
    validate_itinerary,
)
from packages.harness.agent import AgentSpec, BaseAgent
from packages.harness.model_gateway import ModelGateway, ModelPolicy
from packages.harness.tool_gateway import ToolGateway

CRITIC_TOOL_ALLOWLIST = (
    "budget.calculate",
    "schedule.validate",
)

_DESTINATION_CODES = {
    ValidationCode.OPENING_HOURS_UNKNOWN.value,
    ValidationCode.MISSING_CLAIM_REFERENCE.value,
    ValidationCode.MISSING_EVIDENCE_REFERENCE.value,
    ValidationCode.UNKNOWN_HARD_CONSTRAINT.value,
    "contradicted_hard_claim",
}
_MOBILITY_CODES = {
    ValidationCode.COORDINATE_SYSTEM_MISMATCH.value,
    ValidationCode.MISSING_ROUTE_REFERENCE.value,
    ValidationCode.ROUTE_ENDPOINT_MISMATCH.value,
}


def _repair_target(code: str) -> tuple[str, str]:
    if code in _DESTINATION_CODES:
        return "destination_intelligence", "refresh_or_complete_evidence"
    if code in _MOBILITY_CODES:
        return "mobility_lodging", "repair_route_or_transport_fact"
    return "itinerary_planner", "replan_with_hard_constraints"


class DeterministicCritic:
    """Revalidate candidates, detect semantic risks, and emit bounded patches."""

    def review(
        self,
        *,
        request: TripRequest,
        candidates: PlanCandidatesArtifact,
        destination: DestinationIntelArtifact,
        mobility: MobilityLodgingArtifact,
        route_matrix: tuple[Route, ...],
        created_at: datetime,
    ) -> ReviewArtifact:
        if len({candidates.run_id, destination.run_id, mobility.run_id}) != 1:
            raise ValueError("critic inputs must belong to the same run")

        violations: list[HardViolation] = []
        patches: list[PatchRequest] = []
        scores: dict[str, ScoreBreakdown] = {}
        known_evidence = {item.evidence_id for item in destination.evidence}
        known_evidence.update(item.evidence_id for item in mobility.evidence)

        for plan in candidates.plans:
            report = validate_itinerary(
                plan,
                request,
                places=destination.places,
                routes=route_matrix,
                claims=destination.claims,
                evidence=(*destination.evidence, *mobility.evidence),
            )
            plan_codes: list[str] = []
            for issue in report.errors:
                code = issue.code.value
                plan_codes.append(code)
                item_id = next(
                    (related for related in issue.related_ids if related.startswith("item_")),
                    None,
                )
                violations.append(
                    HardViolation(
                        code=code,
                        severity=IssueSeverity.ERROR,
                        plan_id=plan.plan_id,
                        plan_item_id=item_id,
                        evidence_ids=(),
                        message=issue.message,
                    )
                )

            used_claim_ids = {
                claim_id for day in plan.days for item in day.items for claim_id in item.claim_ids
            }
            contradicted = tuple(
                claim
                for claim in destination.claims
                if claim.claim_id in used_claim_ids
                and claim.required_hard_constraint
                and claim.status is ClaimStatus.CONTRADICTED
            )
            if contradicted:
                plan_codes.append("contradicted_hard_claim")
                violations.append(
                    HardViolation(
                        code="contradicted_hard_claim",
                        severity=IssueSeverity.CRITICAL,
                        plan_id=plan.plan_id,
                        evidence_ids=tuple(
                            dict.fromkeys(
                                evidence_id
                                for claim in contradicted
                                for evidence_id in claim.evidence_ids
                            )
                        ),
                        message="方案使用了已被证据否定的硬约束事实。",
                    )
                )

            for day in plan.days:
                place_ids = [item.place_id for item in day.items]
                if len(place_ids) != len(set(place_ids)):
                    plan_codes.append("backtracking_detected")
                    violations.append(
                        HardViolation(
                            code="backtracking_detected",
                            severity=IssueSeverity.ERROR,
                            plan_id=plan.plan_id,
                            message=f"{day.date} 重复访问同一地点，存在折返风险。",
                        )
                    )

            seen_codes: set[str] = set()
            for code in plan_codes:
                if code in seen_codes:
                    continue
                seen_codes.add(code)
                target, action = _repair_target(code)
                patches.append(
                    PatchRequest(
                        target_agent=target,
                        action=action,
                        plan_id=plan.plan_id,
                        constraints={"violation_code": code},
                    )
                )

            item_evidence = [
                evidence_id
                for day in plan.days
                for item in day.items
                for evidence_id in item.evidence_ids
            ]
            coverage = (
                sum(evidence_id in known_evidence for evidence_id in item_evidence)
                / len(item_evidence)
                if item_evidence
                else 0
            )
            has_budget_issue = any(code.startswith("budget_") for code in plan_codes)
            scores[plan.plan_id] = ScoreBreakdown(
                feasibility=0 if plan_codes else plan.score_breakdown.feasibility,
                preference_match=plan.score_breakdown.preference_match,
                budget_fit=0 if has_budget_issue else plan.score_breakdown.budget_fit,
                risk_resilience=(
                    0.4
                    if mobility.manual_confirmation_required
                    else plan.score_breakdown.risk_resilience
                ),
                evidence_quality=round(coverage, 4),
            )

        verdict = ReviewVerdict.REVISE if patches else ReviewVerdict.PASS
        return ReviewArtifact(
            artifact_id="artifact_critic_review",
            run_id=candidates.run_id,
            producer_agent="critic",
            reviewed_plan_ids=tuple(plan.plan_id for plan in candidates.plans),
            verdict=verdict,
            plan_scores=scores,
            hard_violations=tuple(violations),
            patch_requests=tuple(patches),
            created_at=created_at,
        )


class CriticRiskAgent(BaseAgent[ReviewArtifact]):
    """Run semantic review over deterministic rule results with no open-web tools."""

    def __init__(self, model_gateway: ModelGateway, tool_gateway: ToolGateway) -> None:
        super().__init__(
            AgentSpec(
                agent_id="critic",
                system_prompt=(
                    "Review only validated itinerary artifacts and deterministic rule results. "
                    "Identify evidence gaps, contradictions, pace and execution risks; emit only "
                    "machine-actionable ReviewArtifact patch requests. Never repair facts directly."
                ),
                output_model=ReviewArtifact,
                model_policy=ModelPolicy(
                    model_alias="critic-risk",
                    temperature=0,
                    max_tool_calls=4,
                ),
                tool_allowlist=CRITIC_TOOL_ALLOWLIST,
                max_steps=6,
                timeout_seconds=120,
            ),
            model_gateway,
            tool_gateway,
        )
