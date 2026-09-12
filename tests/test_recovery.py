"""C17 heartbeat, checkpoint, restart recovery, and Outbox tests."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from packages.domain import EventType, RunState, TaskState
from packages.harness.persistence import (
    EventRecord,
    MemoryHarnessRepository,
    OutboxRecord,
    RunRecord,
    TaskRecord,
    metadata,
)
from packages.harness.recovery import (
    CheckpointManager,
    HeartbeatService,
    LeaseLostError,
    OutboxPublisher,
    RecoveryCoordinator,
)

NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def digest(character: str) -> str:
    return f"sha256:{character * 64}"


async def repository_with_leased_task() -> MemoryHarnessRepository:
    repository = MemoryHarnessRepository()
    await repository.create_run(
        RunRecord(
            run_id="run_recovery_fixture",
            state=RunState.RESEARCHING,
            trace_id="trace_recovery_fixture",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await repository.add_tasks(
        (
            TaskRecord(
                task_id="task_recovery_fixture",
                run_id="run_recovery_fixture",
                task_type="fixture_task",
                recipient_agent="echo_agent",
                available_at=NOW,
                deadline=NOW + timedelta(minutes=5),
                idempotency_key=digest("e"),
                created_at=NOW,
                updated_at=NOW,
            ),
        )
    )
    lease = await repository.lease_next_task(
        worker_id="worker_before_restart", now=NOW, lease_seconds=30
    )
    assert lease is not None
    return repository


def test_heartbeat_extends_lease_and_wrong_owner_fails_closed() -> None:
    async def scenario() -> None:
        repository = await repository_with_leased_task()
        heartbeat = HeartbeatService(repository, lease_seconds=30)

        await heartbeat.pulse(
            "task_recovery_fixture",
            "worker_before_restart",
            NOW + timedelta(seconds=20),
        )
        assert await RecoveryCoordinator(repository).recover(NOW + timedelta(seconds=31)) == 0

        with pytest.raises(LeaseLostError):
            await heartbeat.pulse(
                "task_recovery_fixture",
                "worker_without_lease",
                NOW + timedelta(seconds=25),
            )

    asyncio.run(scenario())


def test_expired_task_resumes_from_latest_checkpoint_after_restart() -> None:
    async def scenario() -> None:
        repository = await repository_with_leased_task()
        checkpoints = CheckpointManager(repository)
        await checkpoints.save(
            run_id="run_recovery_fixture",
            task_id="task_recovery_fixture",
            step=0,
            context={"cursor": 0, "messages": ["initial"]},
            now=NOW + timedelta(seconds=2),
        )
        await checkpoints.save(
            run_id="run_recovery_fixture",
            task_id="task_recovery_fixture",
            step=1,
            context={"cursor": 1, "messages": ["initial", "tool result"]},
            now=NOW + timedelta(seconds=10),
        )

        restarted = RecoveryCoordinator(repository)
        assert await restarted.recover(NOW + timedelta(seconds=31)) == 1
        stored = await repository.get_task("task_recovery_fixture")
        assert stored is not None
        assert stored.state is TaskState.WAITING_RETRY

        resumed_lease = await repository.lease_next_task(
            worker_id="worker_after_restart",
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
        )
        checkpoint = await restarted.resume_checkpoint("task_recovery_fixture")

        assert resumed_lease is not None
        assert resumed_lease.attempt == 2
        assert checkpoint is not None
        assert checkpoint.step == 1
        assert checkpoint.context["cursor"] == 1

    asyncio.run(scenario())


def test_transactional_outbox_is_claimed_and_published_once() -> None:
    async def scenario() -> None:
        repository = await repository_with_leased_task()
        event = EventRecord(
            event_id="event_outbox_fixture",
            run_id="run_recovery_fixture",
            task_id="task_recovery_fixture",
            event_type=EventType.TASK_PROGRESS,
            idempotency_key=digest("f"),
            payload={"step": 1},
            created_at=NOW,
        )
        outbox = OutboxRecord(
            outbox_id="outbox_recovery_fixture",
            event_id=event.event_id,
            topic="run_events",
            payload={"event_id": event.event_id},
            available_at=NOW,
            created_at=NOW,
        )
        await repository.complete_task_with_event_outbox(
            "task_recovery_fixture", "worker_before_restart", NOW, event, outbox
        )
        completed = await repository.get_task("task_recovery_fixture")
        assert completed is not None
        assert completed.state is TaskState.SUCCEEDED
        assert await repository.append_event(event) is False

        delivered: list[str] = []

        async def publish(item: OutboxRecord) -> None:
            await asyncio.sleep(0)
            delivered.append(item.event_id)

        counts = await asyncio.gather(
            OutboxPublisher(repository, publish).drain_once(worker_id="publisher_first", now=NOW),
            OutboxPublisher(repository, publish).drain_once(worker_id="publisher_second", now=NOW),
        )

        assert sorted(counts) == [0, 1]
        assert delivered == ["event_outbox_fixture"]
        assert (
            await OutboxPublisher(repository, publish).drain_once(
                worker_id="publisher_after_restart", now=NOW + timedelta(seconds=60)
            )
            == 0
        )

    asyncio.run(scenario())


def test_recovery_migration_tables_are_registered() -> None:
    assert {"checkpoints", "outbox_events"} <= set(metadata.tables)
