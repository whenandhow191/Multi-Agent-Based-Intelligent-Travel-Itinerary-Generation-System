"""Operator CLI that reuses application schemas and the ToolGateway."""

import argparse
import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.api.config import inspect_configuration, render_doctor
from apps.api.settings import Settings, get_settings
from packages.harness.blackboard import PostgresBlackboard
from packages.harness.persistence.schema import events, runs, tasks
from packages.harness.tool_gateway import ToolContext, ToolGateway, ToolGatewayError
from packages.tools.registry import build_default_registry


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit command tree; no command is assembled as shell text."""

    parser = argparse.ArgumentParser(prog="travelctl", description="Travel Harness operator CLI")
    commands = parser.add_subparsers(dest="group", required=True)
    commands.add_parser("doctor", help="show redacted configuration readiness")

    tools_parser = commands.add_parser("tools", help="discover or invoke registered tools")
    tools_commands = tools_parser.add_subparsers(dest="tools_command", required=True)
    tools_commands.add_parser("list", help="list normalized tool schemas")
    call = tools_commands.add_parser("call", help="call one tool through ToolGateway")
    call.add_argument("tool_name")
    call.add_argument("--arguments", default="{}", help="JSON object validated by the tool schema")

    run_parser = commands.add_parser("run", help="inspect durable Harness runs")
    run_commands = run_parser.add_subparsers(dest="run_command", required=True)
    inspect = run_commands.add_parser("inspect", help="show a run, tasks and events")
    inspect.add_argument("run_id")

    blackboard_parser = commands.add_parser("blackboard", help="read immutable Artifacts")
    blackboard_commands = blackboard_parser.add_subparsers(dest="blackboard_command", required=True)
    get = blackboard_commands.add_parser("get", help="read one exact Artifact version")
    get.add_argument("--run-id", required=True)
    get.add_argument("--task-id", required=True)
    get.add_argument("--type", dest="artifact_type", required=True)
    get.add_argument("--version", required=True, type=int)
    return parser


def _database_url(settings: Settings) -> str:
    return settings.database_url.get_secret_value()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


async def _tools_command(args: argparse.Namespace, settings: Settings) -> int:
    async with httpx.AsyncClient() as client:
        registry = build_default_registry(settings, client)
        if args.tools_command == "list":
            print(_json([spec.model_dump(mode="json") for spec in registry.list_specs()]))
            return 0
        try:
            decoded = json.loads(args.arguments)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON arguments: {exc.msg}")
            return 2
        if not isinstance(decoded, dict):
            print("Tool arguments must be a JSON object.")
            return 2
        arguments: dict[str, JsonValue] = decoded
        gateway = ToolGateway(registry)
        try:
            result = await gateway.execute(
                call_id="cli_call",
                tool_name=args.tool_name,
                arguments=arguments,
                context=ToolContext(
                    run_id="cli_run",
                    task_id="cli_task",
                    agent_id="travelctl",
                    trace_id="cli_trace",
                ),
                allowlist=(args.tool_name,),
            )
        except ToolGatewayError as exc:
            print(_json({"error": exc.code, "message": str(exc)}))
            return 1
        print(result.model_dump_json(indent=2))
        return 0


async def _inspect_run(engine: AsyncEngine, run_id: str) -> int:
    async with engine.connect() as connection:
        run = (
            (await connection.execute(select(runs).where(runs.c.run_id == run_id)))
            .mappings()
            .first()
        )
        if run is None:
            return 1
        task_rows = await connection.execute(select(tasks).where(tasks.c.run_id == run_id))
        event_rows = await connection.execute(select(events).where(events.c.run_id == run_id))
        tasks_payload = [dict(row) for row in task_rows.mappings().all()]
        events_payload = [dict(row) for row in event_rows.mappings().all()]
    print(_json({"run": dict(run), "tasks": tasks_payload, "events": events_payload}))
    return 0


async def _database_command(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_async_engine(_database_url(settings), pool_pre_ping=True)
    try:
        if args.group == "run":
            return await _inspect_run(engine, args.run_id)
        board = PostgresBlackboard(engine)
        artifact = await board.get(
            run_id=args.run_id,
            task_id=args.task_id,
            artifact_type=args.artifact_type,
            version=args.version,
        )
        if artifact is None:
            return 1
        print(artifact.model_dump_json(indent=2))
        return 0
    finally:
        await engine.dispose()


async def execute(args: argparse.Namespace, settings: Settings) -> int:
    """Execute parsed commands with injectable settings for deterministic tests."""

    if args.group == "doctor":
        print(render_doctor(settings))
        checks = inspect_configuration(settings)
        return 0 if all(item.present for item in checks if item.required) else 1
    if args.group == "tools":
        return await _tools_command(args, settings)
    return await _database_command(args, settings)


def main(argv: Sequence[str] | None = None) -> int:
    """Load typed settings and execute without ``shell=True`` or string joining."""

    try:
        settings = get_settings()
    except ValidationError:
        print("Configuration invalid; check variable types and required fields.")
        return 2
    return asyncio.run(execute(build_parser().parse_args(argv), settings))


if __name__ == "__main__":
    raise SystemExit(main())
