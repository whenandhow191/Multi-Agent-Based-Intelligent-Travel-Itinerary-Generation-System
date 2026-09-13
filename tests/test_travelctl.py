"""Tests for the transport-thin travelctl command adapter."""

import asyncio
import json

from apps.api.settings import Settings
from apps.api.travelctl import build_parser, execute


def test_tools_list_discovers_registry(capsys: object) -> None:
    args = build_parser().parse_args(["tools", "list"])

    assert asyncio.run(execute(args, Settings())) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    names = {item["name"] for item in output}
    assert {"places.search", "routes.compute", "weather.forecast"} <= names
    assert {"flight.search", "rail.official_link", "lodging.search"} <= names


def test_tools_call_always_uses_gateway(capsys: object) -> None:
    args = build_parser().parse_args(
        ["tools", "call", "fixture.echo", "--arguments", '{"message":"hello","repeat":2}']
    )

    assert asyncio.run(execute(args, Settings())) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["tool_name"] == "fixture.echo"
    assert output["data"]["echoed"] == "hello hello"


def test_tools_call_rejects_non_object_arguments(capsys: object) -> None:
    args = build_parser().parse_args(
        ["tools", "call", "fixture.echo", "--arguments", '["not", "an", "object"]']
    )

    assert asyncio.run(execute(args, Settings())) == 2
    assert "JSON object" in capsys.readouterr().out  # type: ignore[attr-defined]
