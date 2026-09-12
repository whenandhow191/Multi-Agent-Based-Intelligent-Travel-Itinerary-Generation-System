"""Tool registry, schema boundary, allowlist enforcement, and execution gateway."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Annotated, cast

from pydantic import BaseModel, Field, JsonValue, ValidationError

from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.harness.model_gateway import ModelToolSpec, ToolName


class ToolGatewayError(RuntimeError):
    """Base exception whose code can be safely persisted and classified."""

    code = "tool_gateway_error"


class ToolNotFoundError(ToolGatewayError):
    """Raised for a tool name that is not registered."""

    code = "tool_not_found"


class ToolPermissionError(ToolGatewayError):
    """Raised when an Agent requests a tool outside its allowlist."""

    code = "tool_permission_denied"


class ToolInputValidationError(ToolGatewayError):
    """Raised when tool arguments do not satisfy the registered schema."""

    code = "tool_input_invalid"


class ToolOutputValidationError(ToolGatewayError):
    """Raised when an adapter violates its declared result schema."""

    code = "tool_output_invalid"


class ToolContext(DomainModel):
    """Auditable identifiers passed to a tool but not controlled by the model."""

    run_id: Identifier
    task_id: Identifier
    agent_id: Identifier
    trace_id: Identifier


class ToolExecutionResult(DomainModel):
    """Validated result returned to the Agent loop."""

    call_id: Identifier
    tool_name: ToolName
    data: dict[str, JsonValue]
    latency_ms: Annotated[int, Field(ge=0)]


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[BaseModel]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Runtime registration combining public schema and private handler."""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler

    def model_spec(self) -> ModelToolSpec:
        """Return the only tool metadata exposed to a model."""

        return ModelToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
        )


class ToolRegistry:
    """Explicit registry that rejects accidental tool replacement."""

    def __init__(self, definitions: Sequence[ToolDefinition] = ()) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        """Register one tool after validating its public metadata."""

        definition.model_spec()
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        """Resolve one definition without leaking mutable registry state."""

        try:
            return self._definitions[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"tool is not registered: {name}") from exc

    def list_specs(self, allowlist: Sequence[str] | None = None) -> tuple[ModelToolSpec, ...]:
        """List sorted tool schemas, optionally restricted to an allowlist."""

        allowed = set(allowlist) if allowlist is not None else None
        return tuple(
            definition.model_spec()
            for name, definition in sorted(self._definitions.items())
            if allowed is None or name in allowed
        )


class ToolGateway:
    """Only execution path for model-requested tools."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
        context: ToolContext,
        allowlist: Sequence[str],
    ) -> ToolExecutionResult:
        """Enforce permission, input schema, handler, and output schema in order."""

        if tool_name not in set(allowlist):
            raise ToolPermissionError(
                f"agent {context.agent_id} is not allowed to call {tool_name}"
            )
        definition = self.registry.get(tool_name)
        try:
            validated_input = definition.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise ToolInputValidationError(str(exc)) from exc

        started = monotonic()
        raw_output = await definition.handler(validated_input, context)
        try:
            validated_output = definition.output_model.model_validate(raw_output)
        except ValidationError as exc:
            raise ToolOutputValidationError(str(exc)) from exc
        latency_ms = max(0, round((monotonic() - started) * 1000))
        data = cast(dict[str, JsonValue], validated_output.model_dump(mode="json"))
        return ToolExecutionResult(
            call_id=call_id,
            tool_name=tool_name,
            data=data,
            latency_ms=latency_ms,
        )


class FixtureEchoInput(DomainModel):
    """Strict arguments for the built-in offline fixture tool."""

    message: NonEmptyText
    repeat: Annotated[int, Field(ge=1, le=3)] = 1


class FixtureEchoOutput(DomainModel):
    """Deterministic response from the built-in offline fixture tool."""

    echoed: NonEmptyText
    context_task_id: Identifier


async def fixture_echo_handler(arguments: BaseModel, context: ToolContext) -> BaseModel:
    """Return validated input without network, environment, or filesystem access."""

    parsed = FixtureEchoInput.model_validate(arguments)
    return FixtureEchoOutput(
        echoed=" ".join([parsed.message] * parsed.repeat),
        context_task_id=context.task_id,
    )


def fixture_echo_tool() -> ToolDefinition:
    """Build the registered fixture tool used by C13 and Agent tests."""

    return ToolDefinition(
        name="fixture.echo",
        description="Return a deterministic echo for offline Harness tests.",
        input_model=FixtureEchoInput,
        output_model=FixtureEchoOutput,
        handler=fixture_echo_handler,
    )
