"""Bounded Agent lifecycle built on the model and tool gateways."""

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, JsonValue, ValidationError

from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.harness.model_gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPolicy,
    ModelRequest,
    ModelUsage,
    TraceContext,
)
from packages.harness.tool_gateway import (
    ToolContext,
    ToolGateway,
    ToolGatewayError,
)


class AgentErrorCode(StrEnum):
    """Stable failure categories used by retry and persistence policies."""

    MODEL_ERROR = "model_error"
    TOOL_POLICY_ERROR = "tool_policy_error"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    OUTPUT_VALIDATION_ERROR = "output_validation_error"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    TOOL_CALL_LIMIT_EXCEEDED = "tool_call_limit_exceeded"
    TIMED_OUT = "timed_out"


class AgentExecutionError(RuntimeError):
    """Classified Agent failure without exposing private model reasoning."""

    def __init__(
        self,
        code: AgentErrorCode,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AgentContext(DomainModel):
    """Immutable messages and identifiers selected for one Agent task."""

    run_id: Identifier
    task_id: Identifier
    trace_id: Identifier
    messages: tuple[ModelMessage, ...]

    def append(self, *messages: ModelMessage) -> "AgentContext":
        """Return a new context; never mutate shared task history in place."""

        return self.model_copy(update={"messages": (*self.messages, *messages)})


@dataclass(frozen=True, slots=True)
class AgentSpec[OutputT: BaseModel]:
    """Deterministic policy and output boundary for one Agent implementation."""

    agent_id: str
    system_prompt: str
    output_model: type[OutputT]
    model_policy: ModelPolicy
    tool_allowlist: tuple[str, ...] = ()
    max_steps: int = 8
    timeout_seconds: float = 120

    def __post_init__(self) -> None:
        if self.max_steps < 1 or self.max_steps > 50:
            raise ValueError("agent max_steps must be between 1 and 50")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 1800:
            raise ValueError("agent timeout_seconds must be between 0 and 1800")


@dataclass(frozen=True, slots=True)
class AgentRunResult[OutputT: BaseModel]:
    """Successful structured output plus deterministic execution accounting."""

    output: OutputT
    usage: ModelUsage
    steps: int
    tool_calls: int


class BaseAgent[OutputT: BaseModel]:
    """Execute a finite model/tool loop with one typed final output."""

    def __init__(
        self,
        spec: AgentSpec[OutputT],
        model_gateway: ModelGateway,
        tool_gateway: ToolGateway,
    ) -> None:
        self.spec = spec
        self.model_gateway = model_gateway
        self.tool_gateway = tool_gateway

    async def run(self, context: AgentContext) -> AgentRunResult[OutputT]:
        """Run with a hard wall-clock deadline and classified timeout."""

        try:
            async with asyncio.timeout(self.spec.timeout_seconds):
                return await self._run_bounded(context)
        except TimeoutError as exc:
            raise AgentExecutionError(
                AgentErrorCode.TIMED_OUT,
                f"agent {self.spec.agent_id} exceeded its deadline",
                retryable=True,
            ) from exc

    async def _run_bounded(self, context: AgentContext) -> AgentRunResult[OutputT]:
        usage = ModelUsage()
        tool_call_count = 0
        current = context
        system_message = ModelMessage(
            role=MessageRole.SYSTEM,
            content=self.spec.system_prompt,
            name=self.spec.agent_id,
        )

        for step in range(1, self.spec.max_steps + 1):
            request = ModelRequest(
                messages=(system_message, *current.messages),
                tools=self.tool_gateway.registry.list_specs(self.spec.tool_allowlist),
                output_schema=cast(
                    dict[str, JsonValue], self.spec.output_model.model_json_schema()
                ),
                policy=self.spec.model_policy,
                trace=TraceContext(
                    trace_id=current.trace_id,
                    run_id=current.run_id,
                    task_id=current.task_id,
                ),
            )
            try:
                turn = await self.model_gateway.generate(request)
            except AgentExecutionError:
                raise
            except Exception as exc:
                raise AgentExecutionError(
                    AgentErrorCode.MODEL_ERROR,
                    "model gateway failed",
                    retryable=True,
                ) from exc
            usage = usage.add(turn.usage)

            if turn.tool_calls:
                tool_call_count += len(turn.tool_calls)
                if tool_call_count > self.spec.model_policy.max_tool_calls:
                    raise AgentExecutionError(
                        AgentErrorCode.TOOL_CALL_LIMIT_EXCEEDED,
                        "agent exceeded its tool-call budget",
                        retryable=False,
                    )
                current = current.append(
                    ModelMessage(
                        role=MessageRole.ASSISTANT,
                        content=f"requested {len(turn.tool_calls)} validated tool call(s)",
                        name=self.spec.agent_id,
                    )
                )
                for call in turn.tool_calls:
                    try:
                        result = await self.tool_gateway.execute(
                            call_id=call.call_id,
                            tool_name=call.name,
                            arguments=call.arguments,
                            context=ToolContext(
                                run_id=current.run_id,
                                task_id=current.task_id,
                                agent_id=self.spec.agent_id,
                                trace_id=current.trace_id,
                            ),
                            allowlist=self.spec.tool_allowlist,
                        )
                    except ToolGatewayError as exc:
                        raise AgentExecutionError(
                            AgentErrorCode.TOOL_POLICY_ERROR,
                            f"tool gateway rejected call: {exc.code}",
                            retryable=False,
                        ) from exc
                    except Exception as exc:
                        raise AgentExecutionError(
                            AgentErrorCode.TOOL_EXECUTION_ERROR,
                            "tool handler failed",
                            retryable=True,
                        ) from exc
                    current = current.append(
                        ModelMessage(
                            role=MessageRole.TOOL,
                            content=json.dumps(result.data, ensure_ascii=False, sort_keys=True),
                            name=self.spec.agent_id,
                            tool_call_id=call.call_id,
                        )
                    )
                continue

            if turn.output is None:
                raise AgentExecutionError(
                    AgentErrorCode.OUTPUT_VALIDATION_ERROR,
                    f"model ended with {turn.finish_reason} and no structured output",
                    retryable=True,
                )
            try:
                output = self.spec.output_model.model_validate(turn.output)
            except ValidationError as exc:
                raise AgentExecutionError(
                    AgentErrorCode.OUTPUT_VALIDATION_ERROR,
                    "model output failed the Agent output schema",
                    retryable=False,
                ) from exc
            return AgentRunResult(
                output=output,
                usage=usage,
                steps=step,
                tool_calls=tool_call_count,
            )

        raise AgentExecutionError(
            AgentErrorCode.MAX_STEPS_EXCEEDED,
            f"agent exceeded {self.spec.max_steps} steps",
            retryable=False,
        )


class EchoOutput(DomainModel):
    """Small structured output used to verify the base lifecycle."""

    message: NonEmptyText


class EchoAgent(BaseAgent[EchoOutput]):
    """Minimal concrete Agent used by offline lifecycle tests."""

    def __init__(
        self,
        model_gateway: ModelGateway,
        tool_gateway: ToolGateway,
        *,
        max_steps: int = 3,
        timeout_seconds: float = 1,
    ) -> None:
        super().__init__(
            AgentSpec(
                agent_id="echo_agent",
                system_prompt="Return a structured echo. Use only explicitly allowed tools.",
                output_model=EchoOutput,
                model_policy=ModelPolicy(model_alias="fixture-model", max_tool_calls=2),
                tool_allowlist=("fixture.echo",),
                max_steps=max_steps,
                timeout_seconds=timeout_seconds,
            ),
            model_gateway,
            tool_gateway,
        )
