"""C13 registry, schema, allowlist, and fixture tool tests."""

import asyncio

import pytest
from pydantic import JsonValue

from packages.harness import (
    ToolContext,
    ToolGateway,
    ToolInputValidationError,
    ToolPermissionError,
    ToolRegistry,
    fixture_echo_tool,
)


def build_gateway() -> ToolGateway:
    return ToolGateway(ToolRegistry((fixture_echo_tool(),)))


def build_context() -> ToolContext:
    return ToolContext(
        run_id="run_tool_fixture",
        task_id="task_tool_fixture",
        agent_id="echo_agent",
        trace_id="trace_tool_fixture",
    )


def test_registry_lists_json_schema_and_fixture_tool_executes() -> None:
    gateway = build_gateway()

    schemas = gateway.registry.list_specs(("fixture.echo",))
    result = asyncio.run(
        gateway.execute(
            call_id="call_tool_fixture",
            tool_name="fixture.echo",
            arguments={"message": "hello", "repeat": 2},
            context=build_context(),
            allowlist=("fixture.echo",),
        )
    )

    assert [schema.name for schema in schemas] == ["fixture.echo"]
    assert schemas[0].input_schema["additionalProperties"] is False
    assert result.data == {"echoed": "hello hello", "context_task_id": "task_tool_fixture"}


def test_tool_outside_allowlist_is_rejected_before_execution() -> None:
    with pytest.raises(ToolPermissionError, match="not allowed"):
        asyncio.run(
            build_gateway().execute(
                call_id="call_denied_fixture",
                tool_name="fixture.echo",
                arguments={"message": "must not run"},
                context=build_context(),
                allowlist=(),
            )
        )


@pytest.mark.parametrize(
    "arguments",
    (
        {},
        {"message": "hello", "repeat": 10},
        {"message": "hello", "unexpected": "field"},
    ),
)
def test_invalid_tool_arguments_are_rejected(arguments: dict[str, JsonValue]) -> None:
    with pytest.raises(ToolInputValidationError):
        asyncio.run(
            build_gateway().execute(
                call_id="call_invalid_fixture",
                tool_name="fixture.echo",
                arguments=arguments,
                context=build_context(),
                allowlist=("fixture.echo",),
            )
        )


def test_duplicate_tool_registration_is_rejected() -> None:
    registry = ToolRegistry((fixture_echo_tool(),))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(fixture_echo_tool())
