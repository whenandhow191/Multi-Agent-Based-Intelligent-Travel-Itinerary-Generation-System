"""In-memory Fixture trip-run service behind the product API contract."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from secrets import token_hex

from apps.api.run_models import (
    ClarificationAnswer,
    PlanComparisonResponse,
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


@dataclass(slots=True)
class StoredTripRun:
    resource: TripRunResource
    result: TripRunResultResponse
    answers: list[ClarificationAnswer] = field(default_factory=list)


class InMemoryTripRunService:
    """Deterministic local backend used by OpenAPI, tests and the product UI."""

    def __init__(self) -> None:
        self._runs: dict[str, StoredTripRun] = {}

    def create(self, request: TripRequest) -> TripRunResource:
        self._validate_fixture_request(request)
        run_id = f"run_{token_hex(8)}"
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
            markdown=FinalAggregator().render_markdown(bundle),
        )
        self._runs[run_id] = StoredTripRun(resource=resource, result=result)
        return resource

    def get(self, run_id: str) -> TripRunResource:
        return self._stored(run_id).resource

    def cancel(self, run_id: str) -> TripRunResource:
        stored = self._stored(run_id)
        stored.resource = stored.resource.model_copy(
            update={
                "state": TripRunState.CANCELLED,
                "updated_at": datetime.now(UTC),
                "result_available": False,
            }
        )
        return stored.resource

    def clarify(self, run_id: str, answer: ClarificationAnswer) -> TripRunResource:
        stored = self._stored(run_id)
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

    def result(self, run_id: str) -> TripRunResultResponse:
        stored = self._stored(run_id)
        if not stored.resource.result_available:
            raise RunConflictError("run has no available result")
        return stored.result

    def comparison(self, run_id: str) -> PlanComparisonResponse:
        result = self.result(run_id)
        return PlanComparisonResponse(
            run_id=run_id,
            comparison=result.bundle.comparison,
            generated_at=result.bundle.generated_at,
        )

    def _stored(self, run_id: str) -> StoredTripRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFoundError(run_id) from exc

    @staticmethod
    def _validate_fixture_request(request: TripRequest) -> None:
        if request.destination != "北京" or request.duration_days != 1:
            raise RunConflictError("Fixture runner currently supports a one-day Beijing request")

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
                free_minutes=free_minutes,
                preference_coverage=plan.score_breakdown.preference_match,
                risk_count=len(plan.unresolved_risks),
                evidence_coverage=1,
            )
            for plan, (*_, free_minutes) in zip(plans, specifications, strict=True)
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
