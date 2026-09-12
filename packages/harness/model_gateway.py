"""Provider-neutral model request, response, usage, and gateway contracts."""

from enum import StrEnum
from typing import Annotated, Protocol, runtime_checkable

from pydantic import Field, JsonValue, StringConstraints, model_validator

from packages.domain.common import DomainModel, Identifier, NonEmptyText, ShortText

ToolName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$", max_length=120),
]


class MessageRole(StrEnum):
    """Roles understood by every model adapter."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    """Provider-neutral reasons for ending a model turn."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


class ModelMessage(DomainModel):
    """One portable message sent to a model adapter."""

    role: MessageRole
    content: NonEmptyText
    name: Identifier | None = None
    tool_call_id: Identifier | None = None

    @model_validator(mode="after")
    def tool_messages_identify_the_call(self) -> "ModelMessage":
        """Require a tool call ID only for tool-result messages."""

        if self.role is MessageRole.TOOL and self.tool_call_id is None:
            raise ValueError("tool message requires tool_call_id")
        if self.role is not MessageRole.TOOL and self.tool_call_id is not None:
            raise ValueError("tool_call_id is only valid for tool messages")
        return self


class ModelToolSpec(DomainModel):
    """JSON-schema tool description visible to a model."""

    name: ToolName
    description: NonEmptyText
    input_schema: dict[str, JsonValue]


class ModelPolicy(DomainModel):
    """Portable generation limits selected by task policy."""

    model_alias: ShortText
    temperature: Annotated[float, Field(ge=0, le=2)] = 0
    max_output_tokens: Annotated[int, Field(ge=1, le=128_000)] = 4096
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 60
    max_tool_calls: Annotated[int, Field(ge=0, le=100)] = 12


class TraceContext(DomainModel):
    """Correlation identifiers propagated without provider-specific tracing types."""

    trace_id: Identifier
    run_id: Identifier
    task_id: Identifier


class ModelUsage(DomainModel):
    """Normalized usage counters and optional calculated cost."""

    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0
    cached_input_tokens: Annotated[int, Field(ge=0)] = 0
    reasoning_tokens: Annotated[int, Field(ge=0)] = 0
    estimated_cost_microunits: Annotated[int, Field(ge=0)] = 0

    @property
    def total_tokens(self) -> int:
        """Return the provider-neutral input plus output count."""

        return self.input_tokens + self.output_tokens

    def add(self, other: "ModelUsage") -> "ModelUsage":
        """Combine immutable counters across an Agent execution."""

        return ModelUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            estimated_cost_microunits=(
                self.estimated_cost_microunits + other.estimated_cost_microunits
            ),
        )


class ModelToolCall(DomainModel):
    """Validated request for the Harness to invoke one tool."""

    call_id: Identifier
    name: ToolName
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ModelTurn(DomainModel):
    """One normalized provider response."""

    output: dict[str, JsonValue] | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()
    usage: ModelUsage = ModelUsage()
    finish_reason: FinishReason
    model_id: ShortText

    @model_validator(mode="after")
    def finish_reason_matches_payload(self) -> "ModelTurn":
        """Reject ambiguous turns before they enter an Agent loop."""

        if self.finish_reason is FinishReason.TOOL_CALLS and not self.tool_calls:
            raise ValueError("tool_calls finish reason requires tool calls")
        if self.finish_reason is FinishReason.STOP and self.output is None:
            raise ValueError("stop finish reason requires structured output")
        if self.tool_calls and self.output is not None:
            raise ValueError("a turn cannot contain tool calls and final output together")
        return self


class ModelRequest(DomainModel):
    """Complete provider-neutral input for one generation."""

    messages: Annotated[tuple[ModelMessage, ...], Field(min_length=1)]
    tools: tuple[ModelToolSpec, ...] = ()
    output_schema: dict[str, JsonValue] | None = None
    policy: ModelPolicy
    trace: TraceContext

    @model_validator(mode="after")
    def tool_names_are_unique(self) -> "ModelRequest":
        """Prevent adapter-dependent behavior from duplicate tool names."""

        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("model request tool names must be unique")
        return self


@runtime_checkable
class ModelGateway(Protocol):
    """Interface implemented by real providers and deterministic test doubles."""

    async def generate(self, request: ModelRequest) -> ModelTurn:
        """Generate one validated model turn."""

        ...
