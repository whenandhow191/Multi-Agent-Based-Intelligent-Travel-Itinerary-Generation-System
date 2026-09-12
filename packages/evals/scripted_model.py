"""A deterministic, network-free model double for orchestration tests."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue, model_validator

from packages.domain.common import DomainModel, Identifier, NonEmptyText


class FinishReason(StrEnum):
    """Normalized reasons why a scripted turn stopped."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"


class ScriptedUsage(DomainModel):
    """Token-shaped counters used only for deterministic accounting tests."""

    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0


class ScriptedToolCall(DomainModel):
    """One fixed tool invocation emitted by a scripted turn."""

    call_id: Identifier
    tool_name: NonEmptyText
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ScriptedTurn(DomainModel):
    """One response returned verbatim by :class:`ScriptedModel`."""

    output: dict[str, JsonValue] | None = None
    tool_calls: tuple[ScriptedToolCall, ...] = ()
    usage: ScriptedUsage = ScriptedUsage()
    finish_reason: FinishReason

    @model_validator(mode="after")
    def response_shape_matches_finish_reason(self) -> "ScriptedTurn":
        """Keep stop and tool-call turns unambiguous."""

        if self.finish_reason is FinishReason.TOOL_CALLS and not self.tool_calls:
            raise ValueError("tool_calls finish reason requires at least one tool call")
        if self.finish_reason is FinishReason.STOP and self.output is None:
            raise ValueError("stop finish reason requires structured output")
        return self


class ScriptedCall(DomainModel):
    """Observable metadata captured for each generate call."""

    message_count: Annotated[int, Field(ge=0)]
    tool_count: Annotated[int, Field(ge=0)]
    schema_requested: bool


class ScriptExhaustedError(RuntimeError):
    """Raised when a test requests more turns than the script contains."""


class ScriptedModel:
    """Return prevalidated turns in order without credentials or network access."""

    def __init__(self, turns: Sequence[ScriptedTurn]) -> None:
        if not turns:
            raise ValueError("ScriptedModel requires at least one turn")
        self._turns = tuple(turns)
        self._cursor = 0
        self.calls: list[ScriptedCall] = []

    @property
    def remaining_turns(self) -> int:
        """Return how many scripted responses have not yet been consumed."""

        return len(self._turns) - self._cursor

    async def generate(
        self,
        *,
        messages: Sequence[Mapping[str, JsonValue]],
        tools: Sequence[Mapping[str, JsonValue]] = (),
        output_schema: Mapping[str, JsonValue] | None = None,
    ) -> ScriptedTurn:
        """Capture request shape and return the next immutable scripted turn."""

        self.calls.append(
            ScriptedCall(
                message_count=len(messages),
                tool_count=len(tools),
                schema_requested=output_schema is not None,
            )
        )
        if self._cursor >= len(self._turns):
            raise ScriptExhaustedError("scripted model has no remaining turns")
        turn = self._turns[self._cursor]
        self._cursor += 1
        return turn
