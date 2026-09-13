"""Live PostgreSQL Blackboard contract, enabled when TEST_DATABASE_URL is set."""

import asyncio
from datetime import UTC, datetime, timedelta
from os import environ

import pytest

from packages.domain import RunState
from packages.harness import ArtifactRecord, ArtifactVersionConflictError, PostgresBlackboard
from packages.harness.persistence import PostgresHarnessRepository, RunRecord, TaskRecord, runs

DATABASE_URL = environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)


@pytest.mark.skipif(DATABASE_URL is None, reason="requires migrated TEST_DATABASE_URL")
def test_postgres_blackboard_serializes_concurrent_versions() -> None:
    async def scenario() -> None:
        assert DATABASE_URL is not None
        repository = PostgresHarnessRepository.from_url(DATABASE_URL)
        board = PostgresBlackboard(repository.engine)
        try:
            async with repository.engine.begin() as connection:
                await connection.execute(runs.delete().where(runs.c.run_id == "run_board_pg"))
            await repository.create_run(
                RunRecord(
                    run_id="run_board_pg",
                    state=RunState.CREATED,
                    trace_id="trace_board_pg",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await repository.add_tasks(
                (
                    TaskRecord(
                        task_id="task_board_pg",
                        run_id="run_board_pg",
                        task_type="destination_intelligence",
                        recipient_agent="destination_intelligence",
                        available_at=NOW,
                        deadline=NOW + timedelta(minutes=5),
                        idempotency_key=f"sha256:{'9' * 64}",
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                )
            )
            records = tuple(
                ArtifactRecord.create(
                    artifact_id=f"artifact_board_pg_{suffix}",
                    run_id="run_board_pg",
                    task_id="task_board_pg",
                    artifact_type="destination_intel",
                    version=1,
                    producer_agent="destination_intelligence",
                    payload={"winner": suffix},
                    created_at=NOW,
                )
                for suffix in ("first", "second")
            )
            results = await asyncio.gather(
                *(board.write(item, expected_latest_version=0) for item in records),
                return_exceptions=True,
            )
            assert sum(not isinstance(result, Exception) for result in results) == 1
            assert any(isinstance(result, ArtifactVersionConflictError) for result in results)
            latest = await board.latest(
                run_id="run_board_pg",
                task_id="task_board_pg",
                artifact_type="destination_intel",
            )
            assert latest is not None
            assert latest.version == 1
        finally:
            async with repository.engine.begin() as connection:
                await connection.execute(runs.delete().where(runs.c.run_id == "run_board_pg"))
            await repository.close()

    asyncio.run(scenario())
