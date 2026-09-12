"""C19 acceptance tests for A1 destination intelligence."""

import asyncio
from typing import cast

import pytest
from pydantic import BaseModel, JsonValue, ValidationError

from packages.agents import DestinationIntelArtifact, DestinationIntelligenceAgent
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


class PlaceSearchInput(DomainModel):
    query: NonEmptyText


class PlaceSearchOutput(DomainModel):
    candidate_ids: tuple[str, ...]


async def search_fixture(arguments: BaseModel, context: ToolContext) -> BaseModel:
    parsed = PlaceSearchInput.model_validate(arguments)
    assert parsed.query == "北京 历史 公园"
    assert context.agent_id == "destination_intelligence"
    return PlaceSearchOutput(candidate_ids=("place_palace_fixture", "place_park_fixture"))


def build_agent(
    output: dict[str, JsonValue],
) -> tuple[DestinationIntelligenceAgent, ScriptedModelGateway]:
    model = ScriptedModelGateway(
        (
            ModelTurn(
                tool_calls=(
                    ModelToolCall(
                        call_id="call_destination_search",
                        name="places.search",
                        arguments={"query": "北京 历史 公园"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
                model_id="fixture-model",
            ),
            ModelTurn(
                output=output,
                finish_reason=FinishReason.STOP,
                model_id="fixture-model",
            ),
        )
    )
    tools = ToolGateway(
        ToolRegistry(
            (
                ToolDefinition(
                    name="places.search",
                    description="Return deterministic synthetic destination candidates.",
                    input_model=PlaceSearchInput,
                    output_model=PlaceSearchOutput,
                    handler=search_fixture,
                ),
            )
        )
    )
    return DestinationIntelligenceAgent(model, tools), model


def test_destination_agent_uses_fixture_tool_and_returns_typed_artifact() -> None:
    scenario = build_synthetic_scenario()
    artifact = DestinationIntelArtifact(
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
    agent, model = build_agent(cast(dict[str, JsonValue], artifact.model_dump(mode="json")))
    context = AgentContext(
        run_id="run_beijing_fixture",
        task_id="destination_intelligence",
        trace_id="trace_destination_fixture",
        messages=(ModelMessage(role=MessageRole.USER, content="研究北京的历史和公园候选点"),),
    )

    result = asyncio.run(agent.run(context))

    assert result.output == artifact
    assert result.tool_calls == 1
    assert model.requests[0].tools[0].name == "places.search"
    assert model.requests[1].messages[-1].role is MessageRole.TOOL


def test_destination_artifact_rejects_source_less_fact() -> None:
    scenario = build_synthetic_scenario()
    with pytest.raises(ValidationError, match="references missing evidence"):
        DestinationIntelArtifact(
            artifact_id="artifact_destination_fixture",
            run_id="run_beijing_fixture",
            producer_agent="destination_intelligence",
            destination="北京",
            places=scenario.places,
            weather=scenario.weather,
            claims=scenario.claims,
            evidence=scenario.evidence[1:4],
            created_at=FIXTURE_NOW,
        )
