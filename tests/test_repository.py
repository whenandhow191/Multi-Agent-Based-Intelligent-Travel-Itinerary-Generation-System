"""C16 Repository, atomic lease, dependency, and event idempotency tests."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects import postgresql

from packages.domain import EventType, RunState
from packages.harness.persistence import (
    EventRecord,
    MemoryHarnessRepository,
    RunRecord,
    TaskRecord,
    build_lease_statement,
    metadata,
)

NOW = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)


def digest(character: str) -> str:
    return f"sha256:{character * 64}"


def run_record() -> RunRecord:
    return RunRecord(
        run_id="run_repository_fixture",
        state=RunState.CREATED,
        trace_id="trace_repository_fixture",
        created_at=NOW,
        updated_at=NOW,
    )


def task_record(
    task_id: str,
    *,
    dependencies: tuple[str, ...] = (),
    key_character: str = "a",
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        run_id="run_repository_fixture",
        task_type="fixture_task",
        recipient_agent="echo_agent",
        dependency_ids=dependencies,
        available_at=NOW,
        deadline=NOW + timedelta(minutes=5),
        idempotency_key=digest(key_character),
        created_at=NOW,
        updated_at=NOW,
    )


def test_two_workers_cannot_lease_the_same_task() -> None:
    async def scenario() -> None:
        repository = MemoryHarnessRepository()
        await repository.create_run(run_record())
        await repository.add_tasks((task_record("task_atomic_fixture"),))

        leases = await asyncio.gather(
            repository.lease_next_task(worker_id="worker_first_fixture", now=NOW, lease_seconds=30),
            repository.lease_next_task(
                worker_id="worker_second_fixture", now=NOW, lease_seconds=30
            ),
        )

        claimed = [lease for lease in leases if lease is not None]
        assert len(claimed) == 1
        assert claimed[0].task_id == "task_atomic_fixture"
        assert claimed[0].attempt == 1

    asyncio.run(scenario())


def test_dependencies_block_leasing_until_parent_succeeds() -> None:
    async def scenario() -> None:
        repository = MemoryHarnessRepository()
        await repository.create_run(run_record())
        await repository.add_tasks(
            (
                task_record("task_parent_fixture", key_character="b"),
                task_record(
                    "task_child_fixture",
                    dependencies=("task_parent_fixture",),
                    key_character="c",
                ),
            )
        )

        parent = await repository.lease_next_task(
            worker_id="worker_repository_fixture", now=NOW, lease_seconds=30
        )
        assert parent is not None
        assert parent.task_id == "task_parent_fixture"
        assert (
            await repository.lease_next_task(
                worker_id="worker_other_fixture", now=NOW, lease_seconds=30
            )
            is None
        )

        await repository.complete_task("task_parent_fixture", "worker_repository_fixture", NOW)
        child = await repository.lease_next_task(
            worker_id="worker_other_fixture", now=NOW, lease_seconds=30
        )
        assert child is not None
        assert child.task_id == "task_child_fixture"

    asyncio.run(scenario())


def test_event_idempotency_key_prevents_duplicate_append() -> None:
    async def scenario() -> None:
        repository = MemoryHarnessRepository()
        await repository.create_run(run_record())
        event = EventRecord(
            event_id="event_repository_fixture",
            run_id="run_repository_fixture",
            event_type=EventType.RUN_CREATED,
            idempotency_key=digest("d"),
            payload={"state": RunState.CREATED.value},
            created_at=NOW,
        )

        assert await repository.append_event(event) is True
        assert await repository.append_event(event) is False
        assert await repository.list_events(event.run_id) == (event,)

    asyncio.run(scenario())


def test_postgresql_lease_statement_uses_skip_locked_update_returning() -> None:
    statement = build_lease_statement(
        worker_id="worker_sql_fixture",
        now=NOW,
        lease_until=NOW + timedelta(seconds=30),
    )

    sql = str(
        statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    ).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "UPDATE TASKS" in sql
    assert "RETURNING" in sql
    assert "TASK_DEPENDENCIES" in sql


def test_core_migration_tables_are_registered() -> None:
    assert set(metadata.tables) == {"runs", "tasks", "task_dependencies", "events"}
    assert "ck_tasks_state" in {
        constraint.name for constraint in metadata.tables["tasks"].constraints
    }
