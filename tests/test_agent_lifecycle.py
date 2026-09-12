"""C14 bounded Agent lifecycle tests."""

import asyncio

import pytest

from packages.evals import ScriptedModelGateway
from packages.harness import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    EchoAgent,
    FinishReason,
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
    ModelUsage,
    ToolGateway,
    ToolRegistry,
    fixture_echo_tool,
)


def build_context() -> AgentContext:
    return AgentContext(
        run_id="run_agent_fixture",
        task_id="task_agent_fixture",
        trace_id="trace_agent_fixture",
        messages=(ModelMessage(role=MessageRole.USER, content="echo hello"),),
    )


def build_tools() -> ToolGateway:
    return ToolGateway(ToolRegistry((fixture_echo_tool(),)))


def test_echo_agent_completes_with_structured_output() -> None:
    gateway = ScriptedModelGateway(
        (
            ModelTurn(
                output={"message": "hello"},
                usage=ModelUsage(input_tokens=5, output_tokens=2),
                finish_reason=FinishReason.STOP,
                model_id="fixture-model",
            ),
        )
    )

    result = asyncio.run(EchoAgent(gateway, build_tools()).run(build_context()))

    assert result.output.message == "hello"
    assert result.steps == 1
    assert result.usage.total_tokens == 7


def test_echo_agent_executes_allowed_tool_then_completes() -> None:
    gateway = ScriptedModelGateway(
        (
            ModelTurn(
                tool_calls=(
                    ModelToolCall(
                        call_id="call_agent_fixture",
                        name="fixture.echo",
                        arguments={"message": "hello", "repeat": 2},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
                model_id="fixture-model",
            ),
            ModelTurn(
                output={"message": "hello hello"},
                finish_reason=FinishReason.STOP,
                model_id="fixture-model",
            ),
        )
    )

    result = asyncio.run(EchoAgent(gateway, build_tools()).run(build_context()))

    assert result.output.message == "hello hello"
    assert result.steps == 2
    assert result.tool_calls == 1
    assert gateway.requests[1].messages[-1].role is MessageRole.TOOL
    assert "hello hello" in gateway.requests[1].messages[-1].content


def test_invalid_structured_output_is_classified() -> None:
    gateway = ScriptedModelGateway(
        (
            ModelTurn(
                output={"wrong": "field"},
                finish_reason=FinishReason.STOP,
                model_id="fixture-model",
            ),
        )
    )

    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(EchoAgent(gateway, build_tools()).run(build_context()))

    assert raised.value.code is AgentErrorCode.OUTPUT_VALIDATION_ERROR
    assert raised.value.retryable is False


def test_step_limit_is_classified() -> None:
    call_turn = ModelTurn(
        tool_calls=(
            ModelToolCall(
                call_id="call_loop_fixture",
                name="fixture.echo",
                arguments={"message": "again"},
            ),
        ),
        finish_reason=FinishReason.TOOL_CALLS,
        model_id="fixture-model",
    )
    gateway = ScriptedModelGateway((call_turn, call_turn))

    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(EchoAgent(gateway, build_tools(), max_steps=2).run(build_context()))

    assert raised.value.code is AgentErrorCode.MAX_STEPS_EXCEEDED


class SlowGateway:
    async def generate(self, request: ModelRequest) -> ModelTurn:
        del request
        await asyncio.sleep(0.1)
        return ModelTurn(
            output={"message": "too late"},
            finish_reason=FinishReason.STOP,
            model_id="slow-fixture-model",
        )


def test_agent_timeout_is_classified() -> None:
    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(
            EchoAgent(SlowGateway(), build_tools(), timeout_seconds=0.01).run(build_context())
        )

    assert raised.value.code is AgentErrorCode.TIMED_OUT
    assert raised.value.retryable is True
