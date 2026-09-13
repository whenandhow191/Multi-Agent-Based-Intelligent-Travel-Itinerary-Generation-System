"""C22 acceptance tests for deterministic critic and risk review."""

import asyncio
from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel, JsonValue

from packages.agents import (
    CriticRiskAgent,
    DestinationIntelArtifact,
    DeterministicCritic,
    MobilityLodgingArtifact,
)
from packages.domain import BudgetPolicy, PlanCandidatesArtifact, ReviewVerdict, TripBudget
from packages.domain.common import DomainModel, Identifier
from packages.evals import ScriptedModelGateway
from packages.evals.fixtures import FIXTURE_NOW, TRIP_DATE, build_synthetic_scenario
from packages.harness import (
    AgentContext,
    FinishReason,
    MessageRole,
    ModelMessage,
    ModelToolCall,
    ModelTurn,
    ToolContext,
    ToolDefinition,
    ToolGateway,
    ToolRegistry,
)


class ValidateInput(DomainModel):
    plan_id: Identifier


class ValidateOutput(DomainModel):
    passed: bool


async def validation_fixture(arguments: BaseModel, context: ToolContext) -> BaseModel:
    parsed = ValidateInput.model_validate(arguments)
    assert parsed.plan_id == "plan_balanced_fixture"
    assert context.agent_id == "critic"
    return ValidateOutput(passed=True)


def build_inputs() -> tuple[Any, DestinationIntelArtifact, MobilityLodgingArtifact]:
    scenario = build_synthetic_scenario()
    destination = DestinationIntelArtifact(
        artifact_id="artifact_destination_fixture",
        run_id="run_beijing_fixture",
        producer_agent="destination_intelligence",
        destination="北京",
        places=scenario.places,
        weather=scenario.weather,
        claims=scenario.claims,
        evidence=scenario.evidence[:4],
        created_at=FIXTURE_NOW,
    )
    mobility = MobilityLodgingArtifact(
        artifact_id="artifact_mobility_fixture",
        run_id="run_beijing_fixture",
        producer_agent="mobility_lodging",
        transport_options=scenario.transport,
        lodging_candidates=scenario.lodging,
        evidence=scenario.evidence[4:6],
        unknowns=("参考车次需要人工确认。",),
        manual_confirmation_required=True,
        created_at=FIXTURE_NOW,
    )
    return scenario, destination, mobility


def test_valid_candidate_passes_deterministic_critic() -> None:
    scenario, destination, mobility = build_inputs()

    review = DeterministicCritic().review(
        request=scenario.request,
        candidates=scenario.candidates,
        destination=destination,
        mobility=mobility,
        route_matrix=scenario.routes,
        created_at=FIXTURE_NOW,
    )

    assert review.verdict is ReviewVerdict.PASS
    assert review.hard_violations == ()
    assert review.patch_requests == ()
    assert review.plan_scores["plan_balanced_fixture"].risk_resilience == 0.4


def test_critic_detects_closure_budget_backtracking_and_missing_evidence() -> None:
    scenario, destination, mobility = build_inputs()
    closed_schedule = scenario.places[0].opening_hours.model_copy(
        update={"special_closures": (TRIP_DATE,)}
    )
    closed_place = scenario.places[0].model_copy(update={"opening_hours": closed_schedule})
    destination = destination.model_copy(update={"places": (closed_place, scenario.places[1])})
    first, second = scenario.candidates.plans[0].days[0].items
    repeated = second.model_copy(
        update={
            "place_id": first.place_id,
            "route_from_previous_id": None,
            "travel_mode_from_previous": None,
            "travel_minutes_from_previous": None,
            "evidence_ids": ("evidence_missing_fixture",),
        }
    )
    day = scenario.candidates.plans[0].days[0].model_copy(update={"items": (first, repeated)})
    plan = scenario.candidates.plans[0].model_copy(update={"days": (day,)})
    candidates = PlanCandidatesArtifact(
        artifact_id="artifact_risky_candidates",
        run_id="run_beijing_fixture",
        producer_agent="itinerary_planner",
        plans=(plan,),
        created_at=FIXTURE_NOW,
    )
    strict_request = scenario.request.model_copy(
        update={
            "budget": TripBudget(
                total=Decimal("100.00"),
                policy=BudgetPolicy.HARD,
                flexibility_percent=0,
            )
        }
    )

    review = DeterministicCritic().review(
        request=strict_request,
        candidates=candidates,
        destination=destination,
        mobility=mobility,
        route_matrix=scenario.routes,
        created_at=FIXTURE_NOW,
    )

    codes = {violation.code for violation in review.hard_violations}
    targets = {patch.target_agent for patch in review.patch_requests}
    assert review.verdict is ReviewVerdict.REVISE
    assert "budget_exceeded" in codes
    assert "opening_hours_conflict" in codes
    assert "missing_evidence_reference" in codes
    assert "backtracking_detected" in codes
    assert {"destination_intelligence", "itinerary_planner"} <= targets


def test_critic_agent_runs_with_scripted_model_and_fixture_rule_tool() -> None:
    scenario, destination, mobility = build_inputs()
    review = DeterministicCritic().review(
        request=scenario.request,
        candidates=scenario.candidates,
        destination=destination,
        mobility=mobility,
        route_matrix=scenario.routes,
        created_at=FIXTURE_NOW,
    )
    model = ScriptedModelGateway(
        (
            ModelTurn(
                tool_calls=(
                    ModelToolCall(
                        call_id="call_schedule_validation",
                        name="schedule.validate",
                        arguments={"plan_id": "plan_balanced_fixture"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
                model_id="fixture-model",
            ),
            ModelTurn(
                output=cast(dict[str, JsonValue], review.model_dump(mode="json")),
                finish_reason=FinishReason.STOP,
                model_id="fixture-model",
            ),
        )
    )
    tool = ToolDefinition(
        name="schedule.validate",
        description="Return deterministic validation status.",
        input_model=ValidateInput,
        output_model=ValidateOutput,
        handler=validation_fixture,
    )
    agent = CriticRiskAgent(model, ToolGateway(ToolRegistry((tool,))))
    context = AgentContext(
        run_id="run_beijing_fixture",
        task_id="critic_review",
        trace_id="trace_critic_fixture",
        messages=(ModelMessage(role=MessageRole.USER, content="审校候选方案"),),
    )

    result = asyncio.run(agent.run(context))

    assert result.output == review
    assert result.tool_calls == 1
