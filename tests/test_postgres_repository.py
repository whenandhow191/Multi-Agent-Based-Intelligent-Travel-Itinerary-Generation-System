"""Live PostgreSQL lease/idempotency contract, enabled when TEST_DATABASE_URL is set."""

import asyncio
from datetime import UTC, datetime, timedelta
from os import environ

import pytest

from packages.domain import EventType, RunState
from packages.harness.persistence import (
    EventRecord,
    OutboxRecord,
    PostgresHarnessRepository,
    RunRecord,
    TaskRecord,
    runs,
)

DATABASE_URL = environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)


@pytest.mark.skipif(DATABASE_URL is None, reason="requires migrated TEST_DATABASE_URL")
def test_postgres_atomic_lease_and_event_idempotency() -> None:
    async def scenario() -> None:
        assert DATABASE_URL is not None
        repository = PostgresHarnessRepository.from_url(DATABASE_URL)
        try:
            async with repository.engine.begin() as connection:
                await connection.execute(
                    runs.delete().where(runs.c.run_id == "run_postgres_fixture")
                )
            await repository.create_run(
                RunRecord(
                    run_id="run_postgres_fixture",
                    state=RunState.CREATED,
                    trace_id="trace_postgres_fixture",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await repository.add_tasks(
                (
                    TaskRecord(
                        task_id="task_postgres_fixture",
                        run_id="run_postgres_fixture",
                        task_type="fixture_task",
                        recipient_agent="echo_agent",
                        available_at=NOW,
                        deadline=NOW + timedelta(minutes=5),
                        idempotency_key=f"sha256:{'1' * 64}",
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                )
            )

            leases = await asyncio.gather(
                repository.lease_next_task(
                    worker_id="worker_postgres_first", now=NOW, lease_seconds=30
                ),
                repository.lease_next_task(
                    worker_id="worker_postgres_second", now=NOW, lease_seconds=30
                ),
            )
            claimed_leases = [lease for lease in leases if lease is not None]
            assert len(claimed_leases) == 1

            event = EventRecord(
                event_id="event_postgres_fixture",
                run_id="run_postgres_fixture",
                task_id="task_postgres_fixture",
                event_type=EventType.TASK_STARTED,
                idempotency_key=f"sha256:{'2' * 64}",
                payload={"worker": "fixture"},
                created_at=NOW,
            )
            outbox = OutboxRecord(
                outbox_id="outbox_postgres_fixture",
                event_id=event.event_id,
                topic="run_events",
                payload={"event_id": event.event_id},
                available_at=NOW,
                created_at=NOW,
            )
            owner = claimed_leases[0].lease_owner
            assert owner is not None
            await repository.complete_task_with_event_outbox(
                "task_postgres_fixture", owner, NOW, event, outbox
            )
            assert await repository.append_event(event) is False
            outbox_leases = await asyncio.gather(
                repository.lease_outbox(
                    worker_id="publisher_postgres_first",
                    now=NOW,
                    lease_seconds=30,
                    limit=10,
                ),
                repository.lease_outbox(
                    worker_id="publisher_postgres_second",
                    now=NOW,
                    lease_seconds=30,
                    limit=10,
                ),
            )
            claimed_outbox = [item for batch in outbox_leases for item in batch]
            assert len(claimed_outbox) == 1
            assert claimed_outbox[0].event_id == event.event_id
        finally:
            async with repository.engine.begin() as connection:
                await connection.execute(
                    runs.delete().where(runs.c.run_id == "run_postgres_fixture")
                )
            await repository.close()

    asyncio.run(scenario())
