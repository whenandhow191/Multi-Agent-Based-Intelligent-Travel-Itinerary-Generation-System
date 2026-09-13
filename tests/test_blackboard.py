"""C23 in-memory Blackboard version, lookup, hash, and lineage tests."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.harness import (
    ArtifactRecord,
    ArtifactVersionConflictError,
    MemoryBlackboard,
)
from packages.harness.blackboard_cli import build_parser

NOW = datetime(2026, 9, 13, 9, 0, tzinfo=UTC)


def artifact(
    artifact_id: str,
    *,
    task_id: str = "task_destination",
    artifact_type: str = "destination_intel",
    version: int = 1,
    parents: tuple[str, ...] = (),
    value: str = "fixture",
) -> ArtifactRecord:
    return ArtifactRecord.create(
        artifact_id=artifact_id,
        run_id="run_blackboard_fixture",
        task_id=task_id,
        artifact_type=artifact_type,
        version=version,
        producer_agent="destination_intelligence",
        parent_artifact_ids=parents,
        payload={"value": value},
        created_at=NOW,
    )


def test_blackboard_reads_exact_and_latest_versions_with_lineage() -> None:
    async def scenario() -> None:
        board = MemoryBlackboard()
        first = artifact("artifact_destination_v1")
        second = artifact("artifact_destination_v2", version=2, value="revised")
        await board.write(first, expected_latest_version=0)
        await board.write(second, expected_latest_version=1)
        child = artifact(
            "artifact_route_v1",
            task_id="task_route_matrix",
            artifact_type="route_matrix",
            parents=(second.artifact_id,),
        )
        reference = await board.write(child, expected_latest_version=0)

        exact = await board.get(
            run_id=first.run_id,
            task_id=first.task_id,
            artifact_type=first.artifact_type,
            version=1,
        )
        latest = await board.latest(
            run_id=first.run_id,
            task_id=first.task_id,
            artifact_type=first.artifact_type,
        )
        lineage = await board.lineage(child.artifact_id)

        assert exact == first
        assert latest == second
        assert reference.content_hash == child.content_hash
        assert [item.artifact_id for item in lineage] == [second.artifact_id, child.artifact_id]

    asyncio.run(scenario())


def test_concurrent_writers_cannot_publish_the_same_version() -> None:
    async def scenario() -> None:
        board = MemoryBlackboard()
        candidates = (
            artifact("artifact_concurrent_first"),
            artifact("artifact_concurrent_second"),
        )
        results = await asyncio.gather(
            *(board.write(item, expected_latest_version=0) for item in candidates),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        conflict = next(result for result in results if isinstance(result, Exception))
        assert isinstance(conflict, ArtifactVersionConflictError)

    asyncio.run(scenario())


def test_artifact_rejects_tampered_content_hash() -> None:
    valid = artifact("artifact_hash_fixture")
    with pytest.raises(ValidationError, match="content_hash does not match"):
        ArtifactRecord.model_validate({**valid.model_dump(), "payload": {"value": "tampered"}})


def test_blackboard_cli_accepts_full_logical_key() -> None:
    args = build_parser().parse_args(
        [
            "--database-url",
            "postgresql+asyncpg://user:pass@localhost/db",
            "get",
            "--run-id",
            "run_blackboard_fixture",
            "--task-id",
            "task_destination",
            "--type",
            "destination_intel",
            "--version",
            "2",
        ]
    )
    assert args.artifact_type == "destination_intel"
    assert args.version == 2
