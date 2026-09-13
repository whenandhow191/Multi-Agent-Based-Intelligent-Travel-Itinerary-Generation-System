"""Minimal C23 CLI for PostgreSQL Blackboard read/write verification."""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from packages.harness.blackboard import ArtifactRecord, PostgresBlackboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blackboard", description="Read/write Artifact versions")
    parser.add_argument("--database-url", required=True, help="PostgreSQL asyncpg URL")
    subcommands = parser.add_subparsers(dest="command", required=True)

    put = subcommands.add_parser("put", help="Write one ArtifactRecord JSON file")
    put.add_argument("--file", required=True, type=Path)
    put.add_argument("--expected-latest-version", required=True, type=int)

    get = subcommands.add_parser("get", help="Read one logical Artifact version")
    get.add_argument("--run-id", required=True)
    get.add_argument("--task-id", required=True)
    get.add_argument("--type", dest="artifact_type", required=True)
    get.add_argument("--version", required=True, type=int)
    return parser


async def execute(args: argparse.Namespace) -> int:
    engine = create_async_engine(args.database_url, pool_pre_ping=True)
    board = PostgresBlackboard(engine)
    try:
        if args.command == "put":
            artifact_to_write = ArtifactRecord.model_validate_json(
                args.file.read_text(encoding="utf-8")
            )
            reference = await board.write(
                artifact_to_write,
                expected_latest_version=args.expected_latest_version,
            )
            print(reference.model_dump_json(indent=2))
            return 0
        artifact_read = await board.get(
            run_id=args.run_id,
            task_id=args.task_id,
            artifact_type=args.artifact_type,
            version=args.version,
        )
        if artifact_read is None:
            return 1
        print(artifact_read.model_dump_json(indent=2))
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    """Parse arguments and run without shell command construction."""

    return asyncio.run(execute(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
