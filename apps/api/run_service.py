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
    RunProgressEvent,
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
        self._runs[run_id] = StoredTripRun(
            resource=resource,
            result=result,
            token_hash=self._hash_token(access_token),
            events=self._fixture_events(now),
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
