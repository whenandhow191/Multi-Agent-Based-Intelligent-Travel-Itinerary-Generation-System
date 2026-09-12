"""C20 acceptance tests for A2 mobility and lodging."""

import asyncio
from typing import cast

import pytest
from pydantic import BaseModel, JsonValue, ValidationError

from packages.agents import MobilityLodgingAgent, MobilityLodgingArtifact
from packages.domain.common import DomainModel, NonEmptyText
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


class LodgingSearchInput(DomainModel):
    destination: NonEmptyText


class LodgingSearchOutput(DomainModel):
    lodging_ids: tuple[str, ...]


async def lodging_fixture(arguments: BaseModel, context: ToolContext) -> BaseModel:
    parsed = LodgingSearchInput.model_validate(arguments)
    assert parsed.destination == "北京"
    assert context.agent_id == "mobility_lodging"
    return LodgingSearchOutput(lodging_ids=("lodging_center_fixture",))


def test_mobility_agent_degrades_reference_inventory_to_confirmation() -> None:
    scenario = build_synthetic_scenario()
    artifact = MobilityLodgingArtifact(
        artifact_id="artifact_mobility_fixture",
        run_id="run_beijing_fixture",
        producer_agent="mobility_lodging",
        transport_options=scenario.transport,
        lodging_candidates=scenario.lodging,
        connection_risks=(),
        evidence=scenario.evidence[4:6],
        unknowns=("合成车次仅供参考，出发前需在官方渠道确认。",),
        manual_confirmation_required=True,
        created_at=FIXTURE_NOW,
    )
    model = ScriptedModelGateway(
        (
            ModelTurn(
                tool_calls=(
                    ModelToolCall(
                        call_id="call_lodging_search",
                        name="lodging.search",
                        arguments={"destination": "北京"},
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
        name="lodging.search",
        description="Return deterministic synthetic lodging candidates.",
        input_model=LodgingSearchInput,
        output_model=LodgingSearchOutput,
        handler=lodging_fixture,
    )
    agent = MobilityLodgingAgent(model, ToolGateway(ToolRegistry((tool,))))
    context = AgentContext(
        run_id="run_beijing_fixture",
        task_id="mobility_lodging",
        trace_id="trace_mobility_fixture",
        messages=(ModelMessage(role=MessageRole.USER, content="研究北京交通和住宿"),),
    )

    result = asyncio.run(agent.run(context))

    assert result.output == artifact
    assert result.output.manual_confirmation_required is True
    assert result.output.targeted_routes == ()
    assert result.tool_calls == 1


def test_reference_inventory_cannot_be_presented_as_confirmed() -> None:
    scenario = build_synthetic_scenario()
    with pytest.raises(ValidationError, match="requires manual confirmation"):
        MobilityLodgingArtifact(
            artifact_id="artifact_mobility_fixture",
            run_id="run_beijing_fixture",
            producer_agent="mobility_lodging",
            transport_options=scenario.transport,
            lodging_candidates=scenario.lodging,
            evidence=scenario.evidence[4:6],
            created_at=FIXTURE_NOW,
        )
