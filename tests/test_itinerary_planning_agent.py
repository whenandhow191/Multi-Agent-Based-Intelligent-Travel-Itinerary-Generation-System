"""C21 acceptance tests for deterministic A3 planning."""

import asyncio
from datetime import datetime, time
from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import BaseModel, JsonValue

from packages.agents import (
    DestinationIntelArtifact,
    DeterministicItineraryOptimizer,
    ItineraryPlanningAgent,
    MobilityLodgingArtifact,
    PlanningInfeasibleError,
)
from packages.domain import (
    BudgetPolicy,
    FixedAppointment,
    HardConstraints,
    TripBudget,
    validate_itinerary,
)
from packages.domain.common import DomainModel, Identifier
from packages.evals import ScriptedModelGateway
from packages.evals.fixtures import FIXTURE_NOW, build_synthetic_scenario
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


class OptimizerInput(DomainModel):
    request_id: Identifier


class OptimizerOutput(DomainModel):
    candidate_count: int


async def optimizer_fixture(arguments: BaseModel, context: ToolContext) -> BaseModel:
    parsed = OptimizerInput.model_validate(arguments)
    assert parsed.request_id == "request_beijing_fixture"
    assert context.agent_id == "itinerary_planner"
    return OptimizerOutput(candidate_count=3)


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


def test_optimizer_builds_three_valid_offline_variants() -> None:
    scenario, destination, mobility = build_inputs()

    artifact = DeterministicItineraryOptimizer().solve(
        request=scenario.request,
        destination=destination,
        mobility=mobility,
        route_matrix=scenario.routes,
        run_id="run_beijing_fixture",
        created_at=datetime.fromisoformat("2026-09-12T08:10:00+08:00"),
    )

    assert [plan.strategy.value for plan in artifact.plans] == [
        "balanced",
        "economy",
        "relaxed",
    ]
    assert len(artifact.plans[0].days[0].items) == 2
    assert len(artifact.plans[1].days[0].items) == 1
    for plan in artifact.plans:
        report = validate_itinerary(
            plan,
            scenario.request,
            places=scenario.places,
            routes=scenario.routes,
            claims=scenario.claims,
            evidence=scenario.evidence,
        )
        assert report.passed


def test_planning_agent_uses_only_fixture_optimizer_tool() -> None:
    scenario, destination, mobility = build_inputs()
    artifact = DeterministicItineraryOptimizer().solve(
        request=scenario.request,
        destination=destination,
        mobility=mobility,
        route_matrix=scenario.routes,
        run_id="run_beijing_fixture",
        created_at=FIXTURE_NOW,
    )
    model = ScriptedModelGateway(
        (
            ModelTurn(
                tool_calls=(
                    ModelToolCall(
                        call_id="call_optimizer_fixture",
                        name="optimizer.solve",
                        arguments={"request_id": "request_beijing_fixture"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
                model_id="fixture-model",
            ),
            ModelTurn(
                output=cast(dict[str, JsonValue], artifact.model_dump(mode="json")),
                finish_reason=FinishReason.STOP,
                model_id="fixture-model",
            ),
        )
    )
    tool = ToolDefinition(
        name="optimizer.solve",
        description="Run the deterministic synthetic optimizer.",
        input_model=OptimizerInput,
        output_model=OptimizerOutput,
        handler=optimizer_fixture,
    )
    agent = ItineraryPlanningAgent(model, ToolGateway(ToolRegistry((tool,))))
    context = AgentContext(
        run_id="run_beijing_fixture",
        task_id="itinerary_planning",
        trace_id="trace_planning_fixture",
        messages=(ModelMessage(role=MessageRole.USER, content="生成三个离线候选方案"),),
    )

    result = asyncio.run(agent.run(context))

    assert result.output == artifact
    assert result.tool_calls == 1


def test_hard_budget_failure_is_explicit() -> None:
    scenario, destination, mobility = build_inputs()
    impossible = scenario.request.model_copy(
        update={
            "budget": TripBudget(
                total=Decimal("10.00"),
                policy=BudgetPolicy.HARD,
                flexibility_percent=0,
            )
        }
    )

    with pytest.raises(PlanningInfeasibleError) as raised:
        DeterministicItineraryOptimizer().solve(
            request=impossible,
            destination=destination,
            mobility=mobility,
            route_matrix=scenario.routes,
            run_id="run_beijing_fixture",
            created_at=FIXTURE_NOW,
        )

    assert raised.value.code == "hard_constraints_infeasible"


def test_fixed_appointment_is_kept_at_exact_time() -> None:
    scenario, destination, mobility = build_inputs()
    request = scenario.request.model_copy(
        update={
            "hard_constraints": HardConstraints(
                must_visit=("故宫博物院",),
                fixed_appointments=(
                    FixedAppointment(
                        appointment_id="appointment_palace",
                        title="已预约故宫时段",
                        date=scenario.request.start_date,
                        start=time(10, 0),
                        end=time(11, 0),
                        place_name="故宫博物院",
                    ),
                ),
            )
        }
    )

    artifact = DeterministicItineraryOptimizer().solve(
        request=request,
        destination=destination,
        mobility=mobility,
        route_matrix=scenario.routes,
        run_id="run_beijing_fixture",
        created_at=FIXTURE_NOW,
    )

    for plan in artifact.plans:
        palace = next(
            item for item in plan.days[0].items if item.place_id == "place_palace_fixture"
        )
        assert palace.start_at.time() == time(10, 0)
        assert palace.end_at.time() == time(11, 0)
