"""Contract tests for the C12 provider-neutral model gateway."""

import asyncio

import pytest
from pydantic import ValidationError

from packages.evals import ScriptedModelGateway
from packages.harness import (
    FinishReason,
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPolicy,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
    ModelUsage,
    TraceContext,
)


def build_request() -> ModelRequest:
    return ModelRequest(
        messages=(ModelMessage(role=MessageRole.USER, content="返回结构化回声"),),
        output_schema={"type": "object"},
        policy=ModelPolicy(model_alias="test-model"),
        trace=TraceContext(
            trace_id="trace_model_fixture",
            run_id="run_model_fixture",
            task_id="task_model_fixture",
        ),
    )


def test_scripted_gateway_returns_a_structured_turn() -> None:
    turn = ModelTurn(
        output={"message": "固定响应"},
        usage=ModelUsage(input_tokens=8, output_tokens=3),
        finish_reason=FinishReason.STOP,
        model_id="fixture-model-v1",
    )
    gateway = ScriptedModelGateway((turn,))

    result = asyncio.run(gateway.generate(build_request()))

    assert isinstance(gateway, ModelGateway)
    assert result.output == {"message": "固定响应"}
    assert result.usage.total_tokens == 11
    assert gateway.requests == [build_request()]


def test_tool_call_turn_has_the_same_provider_neutral_shape() -> None:
    turn = ModelTurn(
        tool_calls=(
            ModelToolCall(
                call_id="call_model_fixture",
                name="fixture.echo",
                arguments={"message": "hello"},
            ),
        ),
        finish_reason=FinishReason.TOOL_CALLS,
        model_id="another-provider-model",
    )

    assert turn.model_dump(mode="json")["tool_calls"][0]["name"] == "fixture.echo"


def test_ambiguous_or_incomplete_model_turn_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires structured output"):
        ModelTurn(
            finish_reason=FinishReason.STOP,
            model_id="fixture-model-v1",
        )

    with pytest.raises(ValidationError, match="cannot contain tool calls"):
        ModelTurn(
            output={"message": "ambiguous"},
            tool_calls=(
                ModelToolCall(
                    call_id="call_model_fixture",
                    name="fixture.echo",
                ),
            ),
            finish_reason=FinishReason.TOOL_CALLS,
            model_id="fixture-model-v1",
        )
