"""Secure in-memory Fixture trip-run service behind the product API contract."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest, digest
from json import dumps
from secrets import token_bytes, token_hex

from apps.api.run_models import (
    ClarificationAnswer,
    MapPoint,
    PlanComparisonResponse,
    ReplanRequest,
    RunProgressEvent,
    RunVersionDiff,
    RunVersionList,
    RunVersionSummary,
    TripRunCreated,
    TripRunResource,
    TripRunResultResponse,
    TripRunState,
)
from packages.domain import (
    CollaborationSummary,
    FinalPlanBundle,
    PlanComparison,
    PlanComparisonEntry,
    PlanStrategy,
    ScoreBreakdown,
    TripRequest,
)
from packages.evals.fixtures import build_synthetic_scenario
from packages.workflow import FinalAggregator


class RunNotFoundError(KeyError):
    """Requested run does not exist in the current service."""


class RunConflictError(ValueError):
    """Requested transition conflicts with the current lifecycle."""


class RunAccessError(PermissionError):
    """Anonymous token does not authorize access to the requested run."""


@dataclass(slots=True)
class StoredTripRun:
    resource: TripRunResource
    result: TripRunResultResponse
    token_hash: str
    events: tuple[RunProgressEvent, ...]
    answers: list[ClarificationAnswer] = field(default_factory=list)
    versions: dict[int, TripRunResultResponse] = field(default_factory=dict)
    version_summaries: list[RunVersionSummary] = field(default_factory=list)


class InMemoryTripRunService:
    """Deterministic local backend used by OpenAPI, tests and the product UI."""

    def __init__(self) -> None:
        self._runs: dict[str, StoredTripRun] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._token_secret = token_bytes(32)

    def create(self, request: TripRequest, idempotency_key: str) -> TripRunCreated:
        self._validate_fixture_request(request)
        request_digest = sha256(
            dumps(request.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        if existing := self._idempotency.get(idempotency_key):
            run_id, existing_digest = existing
            if existing_digest != request_digest:
                raise RunConflictError("idempotency key was already used for another request")
            return TripRunCreated(
                run=self._stored(run_id).resource,
                access_token=self._token(run_id),
            )

        run_id = f"run_{token_hex(8)}"
        access_token = self._token(run_id)
        now = datetime.now(UTC)
        bundle = self._fixture_bundle(run_id, request, now)
        resource = TripRunResource(
            run_id=run_id,
            state=TripRunState.SUCCEEDED,
            fixture_mode=True,
            request=request,
            created_at=now,
            updated_at=now,
            result_available=True,
        )
        result = TripRunResultResponse(
            run_id=run_id,
            version=1,
            bundle=bundle,
            map_points=tuple(
                MapPoint(
                    place_id=place.place_id,
                    name=place.name,
                    longitude=place.canonical_coordinate.longitude,
                    latitude=place.canonical_coordinate.latitude,
                    evidence_ids=place.evidence_ids,
                )
                for place in build_synthetic_scenario().places
            ),
            markdown=FinalAggregator().render_markdown(bundle),
        )
        initial_summary = RunVersionSummary(
            version=1,
            instruction="初始生成",
            created_at=now,
            changed_task_ids=(
                "destination_intelligence",
                "mobility_lodging",
                "build_route_matrix",
                "itinerary_planning",
                "critic_review",
                "final_aggregation",
            ),
            invalidated_artifact_ids=(),
            current=True,
        )
        self._runs[run_id] = StoredTripRun(
            resource=resource,
            result=result,
            token_hash=self._hash_token(access_token),
            events=self._fixture_events(now),
            versions={1: result},
            version_summaries=[initial_summary],
        )
        self._idempotency[idempotency_key] = (run_id, request_digest)
        return TripRunCreated(run=resource, access_token=access_token)

    def get(self, run_id: str, access_token: str) -> TripRunResource:
        return self._authorized(run_id, access_token).resource

    def cancel(self, run_id: str, access_token: str) -> TripRunResource:
        stored = self._authorized(run_id, access_token)
        stored.resource = stored.resource.model_copy(
            update={
                "state": TripRunState.CANCELLED,
                "updated_at": datetime.now(UTC),
                "result_available": False,
            }
        )
        return stored.resource

    def clarify(
        self, run_id: str, access_token: str, answer: ClarificationAnswer
    ) -> TripRunResource:
        stored = self._authorized(run_id, access_token)
        if stored.resource.state is TripRunState.CANCELLED:
            raise RunConflictError("cancelled runs cannot accept clarification")
        stored.answers.append(answer)
        stored.resource = stored.resource.model_copy(
            update={
                "clarification_answers": tuple(stored.answers),
                "updated_at": datetime.now(UTC),
            }
        )
        return stored.resource

    def result(self, run_id: str, access_token: str) -> TripRunResultResponse:
        stored = self._authorized(run_id, access_token)
        if not stored.resource.result_available:
            raise RunConflictError("run has no available result")
        return stored.result

    def comparison(self, run_id: str, access_token: str) -> PlanComparisonResponse:
        result = self.result(run_id, access_token)
        return PlanComparisonResponse(
            run_id=run_id,
            comparison=result.bundle.comparison,
            generated_at=result.bundle.generated_at,
        )

    def events(self, run_id: str, access_token: str) -> tuple[RunProgressEvent, ...]:
        return self._authorized(run_id, access_token).events

    def delete(self, run_id: str, access_token: str) -> None:
        self._authorized(run_id, access_token)
        del self._runs[run_id]
        self._idempotency = {
            key: value for key, value in self._idempotency.items() if value[0] != run_id
        }

    def replan(
        self, run_id: str, access_token: str, request: ReplanRequest
    ) -> TripRunResultResponse:
        stored = self._authorized(run_id, access_token)
        if stored.resource.state is TripRunState.CANCELLED:
            raise RunConflictError("cancelled runs cannot be replanned")
        if len(stored.versions) >= 3:
            raise RunConflictError("at most two targeted replanning rounds are allowed")
        if (
            request.base_version is not None
            and request.base_version != stored.resource.current_version
        ):
            raise RunConflictError("base_version is not the current run version")

        next_version = max(stored.versions) + 1
        now = datetime.now(UTC)
        source = stored.result
        bundle = self._apply_instruction(source.bundle, request.instruction, now)
        result = source.model_copy(
            update={
                "version": next_version,
                "bundle": bundle,
                "markdown": FinalAggregator().render_markdown(bundle),
            }
        )
        stored.versions[next_version] = result
        stored.result = result
        stored.version_summaries = [
            item.model_copy(update={"current": False}) for item in stored.version_summaries
        ]
        stored.version_summaries.append(
            RunVersionSummary(
                version=next_version,
                instruction=request.instruction,
                created_at=now,
                changed_task_ids=("itinerary_planning", "critic_review", "final_aggregation"),
                invalidated_artifact_ids=(
                    "artifact_candidates_fixture",
                    "artifact_review_fixture",
                    "artifact_final_bundle_fixture",
                ),
                current=True,
            )
        )
        stored.resource = stored.resource.model_copy(
            update={"current_version": next_version, "updated_at": now}
        )
        return result

    def versions(self, run_id: str, access_token: str) -> RunVersionList:
        stored = self._authorized(run_id, access_token)
        return RunVersionList(run_id=run_id, versions=tuple(stored.version_summaries))

    def diff(
        self, run_id: str, access_token: str, from_version: int, to_version: int
    ) -> RunVersionDiff:
        stored = self._authorized(run_id, access_token)
        if from_version not in stored.versions or to_version not in stored.versions:
            raise RunNotFoundError(f"version {from_version} or {to_version}")
        before = stored.versions[from_version].bundle.model_dump(mode="json")
        after = stored.versions[to_version].bundle.model_dump(mode="json")
        changed_paths = tuple(self._changed_paths(before, after))
        return RunVersionDiff(
            run_id=run_id,
            from_version=from_version,
            to_version=to_version,
            changed_paths=changed_paths,
            summary=f"版本 {from_version} → {to_version} 共改变 {len(changed_paths)} 个结构化路径",
        )

    def restore(self, run_id: str, access_token: str, version: int) -> TripRunResultResponse:
        stored = self._authorized(run_id, access_token)
        try:
            restored = stored.versions[version]
        except KeyError as exc:
            raise RunNotFoundError(f"version {version}") from exc
        stored.result = restored
        stored.resource = stored.resource.model_copy(
            update={"current_version": version, "updated_at": datetime.now(UTC)}
        )
        stored.version_summaries = [
            item.model_copy(update={"current": item.version == version})
            for item in stored.version_summaries
        ]
        return restored

    def export(self, run_id: str, access_token: str, format_name: str) -> tuple[str, str]:
        result = self.result(run_id, access_token)
        if format_name == "markdown":
            return result.markdown, "text/markdown; charset=utf-8"
        if format_name == "json":
            return result.bundle.model_dump_json(indent=2), "application/json"
        raise RunConflictError("export format must be markdown or json")

    @staticmethod
    def _apply_instruction(
        bundle: FinalPlanBundle, instruction: str, generated_at: datetime
    ) -> FinalPlanBundle:
        plans = list(bundle.plans)
        entries = list(bundle.comparison.entries)
        if "轻松" in instruction:
            index = next(
                (
                    position
                    for position, plan in enumerate(plans)
                    if plan.strategy is PlanStrategy.RELAXED
                ),
                0,
            )
            plan = plans[index]
            day = plan.days[0]
            items = day.items[:1]
            daily_cost = items[0].estimated_cost
            plans[index] = plan.model_copy(
                update={
                    "days": (day.model_copy(update={"items": items, "daily_cost": daily_cost}),),
                    "total_cost": daily_cost,
                }
            )
            entry_index = next(
                position for position, entry in enumerate(entries) if entry.plan_id == plan.plan_id
            )
            entries[entry_index] = entries[entry_index].model_copy(
                update={
                    "total_cost": daily_cost,
                    "activity_count": 1,
                    "commute_minutes": 0,
                    "free_minutes": 480,
                }
            )
        return bundle.model_copy(
            update={
                "plans": tuple(plans),
                "comparison": PlanComparison(entries=tuple(entries)),
                "assumptions": (*bundle.assumptions, f"用户修改：{instruction}"),
                "collaboration_summary": bundle.collaboration_summary.model_copy(
                    update={"revision_rounds": bundle.collaboration_summary.revision_rounds + 1}
                ),
                "generated_at": generated_at,
            }
        )

    @classmethod
    def _changed_paths(cls, before: object, after: object, prefix: str = "$") -> list[str]:
        if type(before) is not type(after):
            return [prefix]
        if isinstance(before, dict) and isinstance(after, dict):
            paths: list[str] = []
            for key in sorted(set(before) | set(after)):
                if key not in before or key not in after:
                    paths.append(f"{prefix}.{key}")
                else:
                    paths.extend(cls._changed_paths(before[key], after[key], f"{prefix}.{key}"))
            return paths
        if isinstance(before, list) and isinstance(after, list):
            paths = []
            for index in range(max(len(before), len(after))):
                if index >= len(before) or index >= len(after):
                    paths.append(f"{prefix}[{index}]")
                else:
                    paths.extend(
                        cls._changed_paths(before[index], after[index], f"{prefix}[{index}]")
                    )
            return paths
        return [] if before == after else [prefix]

    def _stored(self, run_id: str) -> StoredTripRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFoundError(run_id) from exc

    def _authorized(self, run_id: str, access_token: str) -> StoredTripRun:
        stored = self._stored(run_id)
        if not compare_digest(stored.token_hash, self._hash_token(access_token)):
            raise RunAccessError(run_id)
        return stored

    def _token(self, run_id: str) -> str:
        return digest(self._token_secret, run_id.encode(), "sha256").hex()

    @staticmethod
    def _hash_token(access_token: str) -> str:
        return sha256(access_token.encode()).hexdigest()

    @staticmethod
    def _validate_fixture_request(request: TripRequest) -> None:
        if request.destination != "北京" or request.duration_days != 1:
            raise RunConflictError("Fixture runner currently supports a one-day Beijing request")

    @staticmethod
    def _fixture_events(now: datetime) -> tuple[RunProgressEvent, ...]:
        stages = (
            ("run.started", None, "coordinator", "running", "总控已创建固定任务图", (), (), 40),
            (
                "task.completed",
                "destination_intelligence",
                "destination_intelligence",
                "succeeded",
                "目的地情报与证据已发布",
                ("artifact_destination_fixture",),
                ("places.search", "weather.forecast"),
                90,
            ),
            (
                "task.completed",
                "mobility_lodging",
                "mobility_lodging",
                "succeeded",
                "交通住宿候选已发布",
                ("artifact_mobility_fixture",),
                ("transport.search", "lodging.search"),
                80,
            ),
            (
                "task.completed",
                "build_route_matrix",
                "harness",
                "succeeded",
                "受控路线矩阵已生成",
                ("artifact_route_matrix_fixture",),
                ("routes.compute",),
                0,
            ),
            (
                "task.completed",
                "itinerary_planning",
                "itinerary_planner",
                "succeeded",
                "三个候选方案已完成",
                ("artifact_candidates_fixture",),
                ("optimizer.solve", "budget.calculate"),
                130,
            ),
            (
                "task.completed",
                "critic_review",
                "critic",
                "succeeded",
                "确定性审校通过",
                ("artifact_review_fixture",),
                ("schedule.validate", "budget.calculate"),
                70,
            ),
            (
                "run.completed",
                None,
                "coordinator",
                "succeeded",
                "最终方案已验证并可导出",
                ("artifact_final_bundle_fixture",),
                (),
                20,
            ),
        )
        return tuple(
            RunProgressEvent(
                sequence=index,
                event_type=event_type,
                task_id=task_id,
                agent_id=agent_id,
                state=state,
                message=message,
                occurred_at=now,
                artifact_ids=artifacts,
                tool_calls=tools,
                estimated_cost_microunits=cost,
            )
            for index, (
                event_type,
                task_id,
                agent_id,
                state,
                message,
                artifacts,
                tools,
                cost,
            ) in enumerate(stages, start=1)
        )

    @staticmethod
    def _fixture_bundle(
        run_id: str, request: TripRequest, generated_at: datetime
    ) -> FinalPlanBundle:
        scenario = build_synthetic_scenario()
        base_plan = scenario.final_bundle.plans[0]
        specifications = (
            (
                "plan_balanced_fixture",
                "北京一日均衡方案",
                PlanStrategy.BALANCED,
                ScoreBreakdown(
                    feasibility=1,
                    preference_match=1,
                    budget_fit=1,
                    risk_resilience=0.8,
                    evidence_quality=1,
                ),
                240,
            ),
            (
                "plan_economy_fixture",
                "北京一日预算优先方案",
                PlanStrategy.ECONOMY,
                ScoreBreakdown(
                    feasibility=1,
                    preference_match=0.75,
                    budget_fit=1,
                    risk_resilience=0.85,
                    evidence_quality=1,
                ),
                300,
            ),
            (
                "plan_relaxed_fixture",
                "北京一日轻松节奏方案",
                PlanStrategy.RELAXED,
                ScoreBreakdown(
                    feasibility=1,
                    preference_match=0.8,
                    budget_fit=0.95,
                    risk_resilience=1,
                    evidence_quality=1,
                ),
                360,
            ),
        )
        plans = tuple(
            base_plan.model_copy(
                update={
                    "plan_id": plan_id,
                    "title": title,
                    "strategy": strategy,
                    "score_breakdown": score,
                }
            )
            for plan_id, title, strategy, score, _ in specifications
        )
        entries = tuple(
            PlanComparisonEntry(
                plan_id=plan.plan_id,
                total_cost=plan.total_cost,
                activity_count=sum(len(day.items) for day in plan.days),
                commute_minutes=30,
                free_minutes=specifications[index][4],
                preference_coverage=plan.score_breakdown.preference_match,
                risk_count=len(plan.unresolved_risks),
                evidence_coverage=1,
            )
            for index, plan in enumerate(plans)
        )
        return scenario.final_bundle.model_copy(
            update={
                "run_id": run_id,
                "request_summary": request,
                "plans": plans,
                "comparison": PlanComparison(entries=entries),
                "collaboration_summary": CollaborationSummary(
                    participating_agents=(
                        "coordinator",
                        "destination_intelligence",
                        "mobility_lodging",
                        "itinerary_planner",
                        "critic",
                    ),
                    completed_task_count=7,
                    revision_rounds=0,
                    notes=("Fixture 模式使用确定性合成数据，不代表实时库存或价格。",),
                ),
                "generated_at": generated_at,
            }
        )
