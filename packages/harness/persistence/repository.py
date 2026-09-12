"""In-memory contract implementation and PostgreSQL Harness repository."""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Annotated, Any, Protocol

from pydantic import AwareDatetime, Field, JsonValue
from sqlalchemy import and_, case, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql.dml import Update

from packages.domain import EventType, RunState, TaskState
from packages.domain.common import DomainModel, Identifier, SchemaVersion, Sha256Digest
from packages.harness.persistence.schema import (
    checkpoints,
    events,
    outbox_events,
    runs,
    task_dependencies,
    tasks,
)


class RunRecord(DomainModel):
    """Persisted minimum state for one Harness run."""

    run_id: Identifier
    state: RunState = RunState.CREATED
    request_schema_version: SchemaVersion = "1.0"
    revision: Annotated[int, Field(ge=0)] = 0
    trace_id: Identifier
    cancellation_requested: bool = False
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TaskRecord(DomainModel):
    """Persisted task queue row plus dependency IDs."""

    task_id: Identifier
    run_id: Identifier
    task_type: Identifier
    recipient_agent: Identifier
    state: TaskState = TaskState.PENDING
    dependency_ids: tuple[Identifier, ...] = ()
    priority: int = 0
    attempt: Annotated[int, Field(ge=0)] = 0
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    available_at: AwareDatetime
    deadline: AwareDatetime
    lease_owner: Identifier | None = None
    lease_until: AwareDatetime | None = None
    heartbeat_at: AwareDatetime | None = None
    idempotency_key: Sha256Digest
    last_error_code: Identifier | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EventRecord(DomainModel):
    """Append-only event row protected by a run-scoped idempotency key."""

    event_id: Identifier
    run_id: Identifier
    task_id: Identifier | None = None
    event_type: EventType
    idempotency_key: Sha256Digest
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: AwareDatetime


class CheckpointRecord(DomainModel):
    """Immutable recoverable Agent context after one completed step."""

    checkpoint_id: Identifier
    run_id: Identifier
    task_id: Identifier
    step: Annotated[int, Field(ge=0)]
    context: dict[str, JsonValue]
    created_at: AwareDatetime


class OutboxRecord(DomainModel):
    """Event publication intent committed in the same transaction as state."""

    outbox_id: Identifier
    event_id: Identifier
    topic: Identifier
    payload: dict[str, JsonValue]
    available_at: AwareDatetime
    lease_owner: Identifier | None = None
    lease_until: AwareDatetime | None = None
    attempt: Annotated[int, Field(ge=0)] = 0
    published_at: AwareDatetime | None = None
    created_at: AwareDatetime


class HarnessRepository(Protocol):
    """Storage operations needed by workers without exposing SQLAlchemy."""

    async def create_run(self, run: RunRecord) -> None: ...

    async def add_tasks(self, task_records: Sequence[TaskRecord]) -> None: ...

    async def lease_next_task(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        run_id: str | None = None,
    ) -> TaskRecord | None: ...

    async def complete_task(self, task_id: str, worker_id: str, now: datetime) -> None: ...

    async def get_task(self, task_id: str) -> TaskRecord | None: ...

    async def heartbeat_task(
        self, task_id: str, worker_id: str, now: datetime, lease_seconds: int
    ) -> bool: ...

    async def recover_expired_leases(self, now: datetime) -> int: ...

    async def save_checkpoint(self, checkpoint: CheckpointRecord) -> bool: ...

    async def latest_checkpoint(self, task_id: str) -> CheckpointRecord | None: ...

    async def append_event(self, event: EventRecord) -> bool: ...

    async def list_events(self, run_id: str) -> tuple[EventRecord, ...]: ...

    async def append_event_with_outbox(self, event: EventRecord, outbox: OutboxRecord) -> bool: ...

    async def complete_task_with_event_outbox(
        self,
        task_id: str,
        worker_id: str,
        now: datetime,
        event: EventRecord,
        outbox: OutboxRecord,
    ) -> None: ...

    async def lease_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> tuple[OutboxRecord, ...]: ...

    async def mark_outbox_published(
        self, outbox_id: str, worker_id: str, now: datetime
    ) -> bool: ...


class MemoryHarnessRepository(HarnessRepository):
    """Lock-protected repository for fast contract and recovery tests."""

    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.tasks: dict[str, TaskRecord] = {}
        self.events: dict[str, EventRecord] = {}
        self.checkpoints: dict[tuple[str, int], CheckpointRecord] = {}
        self.outbox: dict[str, OutboxRecord] = {}
        self._event_keys: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()

    async def create_run(self, run: RunRecord) -> None:
        async with self._lock:
            if run.run_id in self.runs:
                raise ValueError(f"run already exists: {run.run_id}")
            self.runs[run.run_id] = run

    async def add_tasks(self, task_records: Sequence[TaskRecord]) -> None:
        async with self._lock:
            incoming_ids = {task.task_id for task in task_records}
            if len(incoming_ids) != len(task_records):
                raise ValueError("task IDs must be unique")
            if incoming_ids & self.tasks.keys():
                raise ValueError("task already exists")
            all_task_ids = incoming_ids | self.tasks.keys()
            for task in task_records:
                if task.run_id not in self.runs:
                    raise ValueError(f"run does not exist: {task.run_id}")
                missing = set(task.dependency_ids) - all_task_ids
                if missing:
                    raise ValueError(f"task dependencies do not exist: {sorted(missing)}")
            self.tasks.update((task.task_id, task) for task in task_records)

    async def lease_next_task(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        run_id: str | None = None,
    ) -> TaskRecord | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        async with self._lock:
            candidates = []
            for task in self.tasks.values():
                if run_id is not None and task.run_id != run_id:
                    continue
                if task.state not in {TaskState.PENDING, TaskState.WAITING_RETRY}:
                    continue
                if task.available_at > now or task.deadline <= now:
                    continue
                if task.lease_until is not None and task.lease_until >= now:
                    continue
                if any(
                    self.tasks[dependency_id].state is not TaskState.SUCCEEDED
                    for dependency_id in task.dependency_ids
                ):
                    continue
                candidates.append(task)
            if not candidates:
                return None
            selected = sorted(candidates, key=lambda item: (-item.priority, item.created_at))[0]
            leased = selected.model_copy(
                update={
                    "state": TaskState.LEASED,
                    "lease_owner": worker_id,
                    "lease_until": now + timedelta(seconds=lease_seconds),
                    "heartbeat_at": now,
                    "attempt": selected.attempt + 1,
                    "updated_at": now,
                }
            )
            self.tasks[selected.task_id] = leased
            return leased

    async def complete_task(self, task_id: str, worker_id: str, now: datetime) -> None:
        async with self._lock:
            task = self.tasks[task_id]
            if task.state is not TaskState.LEASED or task.lease_owner != worker_id:
                raise ValueError("worker does not own the task lease")
            self.tasks[task_id] = task.model_copy(
                update={
                    "state": TaskState.SUCCEEDED,
                    "lease_owner": None,
                    "lease_until": None,
                    "heartbeat_at": now,
                    "updated_at": now,
                }
            )

    async def get_task(self, task_id: str) -> TaskRecord | None:
        async with self._lock:
            return self.tasks.get(task_id)

    async def heartbeat_task(
        self, task_id: str, worker_id: str, now: datetime, lease_seconds: int
    ) -> bool:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        async with self._lock:
            task = self.tasks.get(task_id)
            if (
                task is None
                or task.lease_owner != worker_id
                or task.state not in {TaskState.LEASED, TaskState.RUNNING, TaskState.WAITING_TOOL}
                or task.lease_until is None
                or task.lease_until <= now
            ):
                return False
            self.tasks[task_id] = task.model_copy(
                update={
                    "heartbeat_at": now,
                    "lease_until": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                }
            )
            return True

    async def recover_expired_leases(self, now: datetime) -> int:
        recovered = 0
        async with self._lock:
            for task_id, task in tuple(self.tasks.items()):
                if (
                    task.state
                    not in {
                        TaskState.LEASED,
                        TaskState.RUNNING,
                        TaskState.WAITING_TOOL,
                        TaskState.VALIDATING,
                    }
                    or task.lease_until is None
                    or task.lease_until >= now
                ):
                    continue
                next_state = (
                    TaskState.FAILED
                    if task.attempt >= task.max_attempts
                    else TaskState.WAITING_RETRY
                )
                self.tasks[task_id] = task.model_copy(
                    update={
                        "state": next_state,
                        "lease_owner": None,
                        "lease_until": None,
                        "last_error_code": "lease_expired",
                        "updated_at": now,
                    }
                )
                recovered += 1
        return recovered

    async def save_checkpoint(self, checkpoint: CheckpointRecord) -> bool:
        async with self._lock:
            key = (checkpoint.task_id, checkpoint.step)
            if key in self.checkpoints:
                return False
            if checkpoint.task_id not in self.tasks:
                raise ValueError(f"task does not exist: {checkpoint.task_id}")
            self.checkpoints[key] = checkpoint
            return True

    async def latest_checkpoint(self, task_id: str) -> CheckpointRecord | None:
        async with self._lock:
            task_checkpoints = [
                checkpoint
                for (stored_task_id, _), checkpoint in self.checkpoints.items()
                if stored_task_id == task_id
            ]
            if not task_checkpoints:
                return None
            return max(task_checkpoints, key=lambda checkpoint: checkpoint.step)

    async def append_event(self, event: EventRecord) -> bool:
        async with self._lock:
            key = (event.run_id, event.idempotency_key)
            if key in self._event_keys:
                return False
            if event.run_id not in self.runs:
                raise ValueError(f"run does not exist: {event.run_id}")
            self._event_keys.add(key)
            self.events[event.event_id] = event
            return True

    async def list_events(self, run_id: str) -> tuple[EventRecord, ...]:
        async with self._lock:
            return tuple(
                sorted(
                    (event for event in self.events.values() if event.run_id == run_id),
                    key=lambda event: (event.created_at, event.event_id),
                )
            )

    async def append_event_with_outbox(self, event: EventRecord, outbox: OutboxRecord) -> bool:
        async with self._lock:
            key = (event.run_id, event.idempotency_key)
            if key in self._event_keys:
                return False
            if event.run_id not in self.runs:
                raise ValueError(f"run does not exist: {event.run_id}")
            if outbox.event_id != event.event_id:
                raise ValueError("outbox event_id must match the committed event")
            self._event_keys.add(key)
            self.events[event.event_id] = event
            self.outbox[outbox.outbox_id] = outbox
            return True

    async def complete_task_with_event_outbox(
        self,
        task_id: str,
        worker_id: str,
        now: datetime,
        event: EventRecord,
        outbox: OutboxRecord,
    ) -> None:
        async with self._lock:
            task = self.tasks[task_id]
            key = (event.run_id, event.idempotency_key)
            if task.state is not TaskState.LEASED or task.lease_owner != worker_id:
                raise ValueError("worker does not own the task lease")
            if event.task_id != task_id or event.run_id != task.run_id:
                raise ValueError("event must describe the completed task")
            if outbox.event_id != event.event_id:
                raise ValueError("outbox event_id must match the committed event")
            if key in self._event_keys:
                raise ValueError("event idempotency key already exists")
            self.tasks[task_id] = task.model_copy(
                update={
                    "state": TaskState.SUCCEEDED,
                    "lease_owner": None,
                    "lease_until": None,
                    "heartbeat_at": now,
                    "updated_at": now,
                }
            )
            self._event_keys.add(key)
            self.events[event.event_id] = event
            self.outbox[outbox.outbox_id] = outbox

    async def lease_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> tuple[OutboxRecord, ...]:
        if lease_seconds < 1 or limit < 1:
            raise ValueError("lease_seconds and limit must be positive")
        async with self._lock:
            candidates = sorted(
                (
                    item
                    for item in self.outbox.values()
                    if item.published_at is None
                    and item.available_at <= now
                    and (item.lease_until is None or item.lease_until < now)
                ),
                key=lambda item: (item.created_at, item.outbox_id),
            )[:limit]
            leased = tuple(
                item.model_copy(
                    update={
                        "lease_owner": worker_id,
                        "lease_until": now + timedelta(seconds=lease_seconds),
                        "attempt": item.attempt + 1,
                    }
                )
                for item in candidates
            )
            self.outbox.update((item.outbox_id, item) for item in leased)
            return leased

    async def mark_outbox_published(self, outbox_id: str, worker_id: str, now: datetime) -> bool:
        async with self._lock:
            item = self.outbox.get(outbox_id)
            if item is None or item.lease_owner != worker_id or item.published_at is not None:
                return False
            self.outbox[outbox_id] = item.model_copy(
                update={
                    "published_at": now,
                    "lease_owner": None,
                    "lease_until": None,
                }
            )
            return True


def build_lease_statement(
    *, worker_id: str, now: datetime, lease_until: datetime, run_id: str | None = None
) -> Update:
    """Build the PostgreSQL CTE/skip-locked atomic lease statement."""

    parent = tasks.alias("parent")
    blocked_by_dependency = exists(
        select(1)
        .select_from(
            task_dependencies.join(
                parent,
                parent.c.task_id == task_dependencies.c.depends_on_task_id,
            )
        )
        .where(
            task_dependencies.c.task_id == tasks.c.task_id,
            parent.c.state != TaskState.SUCCEEDED.value,
        )
    )
    conditions = [
        tasks.c.state.in_((TaskState.PENDING.value, TaskState.WAITING_RETRY.value)),
        tasks.c.available_at <= now,
        tasks.c.deadline > now,
        or_(tasks.c.lease_until.is_(None), tasks.c.lease_until < now),
        ~blocked_by_dependency,
    ]
    if run_id is not None:
        conditions.append(tasks.c.run_id == run_id)
    candidate = (
        select(tasks.c.task_id)
        .where(and_(*conditions))
        .order_by(tasks.c.priority.desc(), tasks.c.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
        .cte("candidate")
    )
    return (
        update(tasks)
        .where(tasks.c.task_id == candidate.c.task_id)
        .values(
            state=TaskState.LEASED.value,
            lease_owner=worker_id,
            lease_until=lease_until,
            heartbeat_at=now,
            attempt=tasks.c.attempt + 1,
            updated_at=now,
        )
        .returning(*tasks.c)
    )


class PostgresHarnessRepository(HarnessRepository):
    """Async PostgreSQL implementation with transaction-scoped atomic operations."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @classmethod
    def from_url(cls, database_url: str) -> "PostgresHarnessRepository":
        return cls(create_async_engine(database_url, pool_pre_ping=True))

    async def create_run(self, run: RunRecord) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(runs.insert().values(**run.model_dump()))

    async def add_tasks(self, task_records: Sequence[TaskRecord]) -> None:
        async with self.engine.begin() as connection:
            for task in task_records:
                values = task.model_dump(exclude={"dependency_ids"})
                await connection.execute(tasks.insert().values(**values))
            dependency_rows = [
                {"task_id": task.task_id, "depends_on_task_id": dependency_id}
                for task in task_records
                for dependency_id in task.dependency_ids
            ]
            if dependency_rows:
                await connection.execute(task_dependencies.insert(), dependency_rows)

    async def lease_next_task(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        run_id: str | None = None,
    ) -> TaskRecord | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        statement = build_lease_statement(
            worker_id=worker_id,
            now=now,
            lease_until=now + timedelta(seconds=lease_seconds),
            run_id=run_id,
        )
        async with self.engine.begin() as connection:
            row = (await connection.execute(statement)).mappings().first()
            if row is None:
                return None
            dependency_rows = await connection.execute(
                select(task_dependencies.c.depends_on_task_id).where(
                    task_dependencies.c.task_id == row["task_id"]
                )
            )
            dependencies = tuple(dependency_rows.scalars())
            return _task_from_mapping(row, dependencies)

    async def complete_task(self, task_id: str, worker_id: str, now: datetime) -> None:
        statement = (
            update(tasks)
            .where(
                tasks.c.task_id == task_id,
                tasks.c.state == TaskState.LEASED.value,
                tasks.c.lease_owner == worker_id,
            )
            .values(
                state=TaskState.SUCCEEDED.value,
                lease_owner=None,
                lease_until=None,
                heartbeat_at=now,
                updated_at=now,
            )
            .returning(tasks.c.task_id)
        )
        async with self.engine.begin() as connection:
            if (await connection.execute(statement)).scalar_one_or_none() is None:
                raise ValueError("worker does not own the task lease")

    async def get_task(self, task_id: str) -> TaskRecord | None:
        async with self.engine.connect() as connection:
            row = (
                (await connection.execute(select(tasks).where(tasks.c.task_id == task_id)))
                .mappings()
                .first()
            )
            if row is None:
                return None
            dependency_rows = await connection.execute(
                select(task_dependencies.c.depends_on_task_id).where(
                    task_dependencies.c.task_id == task_id
                )
            )
            return _task_from_mapping(row, tuple(dependency_rows.scalars()))

    async def heartbeat_task(
        self, task_id: str, worker_id: str, now: datetime, lease_seconds: int
    ) -> bool:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        statement = (
            update(tasks)
            .where(
                tasks.c.task_id == task_id,
                tasks.c.lease_owner == worker_id,
                tasks.c.state.in_(
                    (
                        TaskState.LEASED.value,
                        TaskState.RUNNING.value,
                        TaskState.WAITING_TOOL.value,
                    )
                ),
                tasks.c.lease_until > now,
            )
            .values(
                heartbeat_at=now,
                lease_until=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
            .returning(tasks.c.task_id)
        )
        async with self.engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def recover_expired_leases(self, now: datetime) -> int:
        statement = (
            update(tasks)
            .where(
                tasks.c.state.in_(
                    (
                        TaskState.LEASED.value,
                        TaskState.RUNNING.value,
                        TaskState.WAITING_TOOL.value,
                        TaskState.VALIDATING.value,
                    )
                ),
                tasks.c.lease_until < now,
            )
            .values(
                state=case(
                    (
                        tasks.c.attempt >= tasks.c.max_attempts,
                        TaskState.FAILED.value,
                    ),
                    else_=TaskState.WAITING_RETRY.value,
                ),
                lease_owner=None,
                lease_until=None,
                last_error_code="lease_expired",
                updated_at=now,
            )
            .returning(tasks.c.task_id)
        )
        async with self.engine.begin() as connection:
            return len((await connection.execute(statement)).all())

    async def save_checkpoint(self, checkpoint: CheckpointRecord) -> bool:
        statement = (
            postgres_insert(checkpoints)
            .values(**checkpoint.model_dump())
            .on_conflict_do_nothing(constraint="uq_checkpoints_task_step")
            .returning(checkpoints.c.checkpoint_id)
        )
        async with self.engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def latest_checkpoint(self, task_id: str) -> CheckpointRecord | None:
        statement = (
            select(checkpoints)
            .where(checkpoints.c.task_id == task_id)
            .order_by(checkpoints.c.step.desc())
            .limit(1)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
            return None if row is None else CheckpointRecord.model_validate(dict(row))

    async def append_event(self, event: EventRecord) -> bool:
        statement = (
            postgres_insert(events)
            .values(**event.model_dump())
            .on_conflict_do_nothing(constraint="uq_events_run_idempotency")
            .returning(events.c.event_id)
        )
        async with self.engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def list_events(self, run_id: str) -> tuple[EventRecord, ...]:
        statement = (
            select(events)
            .where(events.c.run_id == run_id)
            .order_by(events.c.created_at, events.c.event_id)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings()
            return tuple(EventRecord.model_validate(dict(row)) for row in rows)

    async def append_event_with_outbox(self, event: EventRecord, outbox: OutboxRecord) -> bool:
        if outbox.event_id != event.event_id:
            raise ValueError("outbox event_id must match the committed event")
        event_statement = (
            postgres_insert(events)
            .values(**event.model_dump())
            .on_conflict_do_nothing(constraint="uq_events_run_idempotency")
            .returning(events.c.event_id)
        )
        async with self.engine.begin() as connection:
            inserted = (await connection.execute(event_statement)).scalar_one_or_none()
            if inserted is None:
                return False
            await connection.execute(outbox_events.insert().values(**outbox.model_dump()))
            return True

    async def complete_task_with_event_outbox(
        self,
        task_id: str,
        worker_id: str,
        now: datetime,
        event: EventRecord,
        outbox: OutboxRecord,
    ) -> None:
        if event.task_id != task_id:
            raise ValueError("event must describe the completed task")
        if outbox.event_id != event.event_id:
            raise ValueError("outbox event_id must match the committed event")
        task_statement = (
            update(tasks)
            .where(
                tasks.c.task_id == task_id,
                tasks.c.run_id == event.run_id,
                tasks.c.state == TaskState.LEASED.value,
                tasks.c.lease_owner == worker_id,
            )
            .values(
                state=TaskState.SUCCEEDED.value,
                lease_owner=None,
                lease_until=None,
                heartbeat_at=now,
                updated_at=now,
            )
            .returning(tasks.c.task_id)
        )
        event_statement = (
            postgres_insert(events)
            .values(**event.model_dump())
            .on_conflict_do_nothing(constraint="uq_events_run_idempotency")
            .returning(events.c.event_id)
        )
        async with self.engine.begin() as connection:
            if (await connection.execute(task_statement)).scalar_one_or_none() is None:
                raise ValueError("worker does not own the task lease")
            if (await connection.execute(event_statement)).scalar_one_or_none() is None:
                raise ValueError("event idempotency key already exists")
            await connection.execute(outbox_events.insert().values(**outbox.model_dump()))

    async def lease_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> tuple[OutboxRecord, ...]:
        if lease_seconds < 1 or limit < 1:
            raise ValueError("lease_seconds and limit must be positive")
        candidate = (
            select(outbox_events.c.outbox_id)
            .where(
                outbox_events.c.published_at.is_(None),
                outbox_events.c.available_at <= now,
                or_(outbox_events.c.lease_until.is_(None), outbox_events.c.lease_until < now),
            )
            .order_by(outbox_events.c.created_at, outbox_events.c.outbox_id)
            .with_for_update(skip_locked=True)
            .limit(limit)
            .cte("outbox_candidate")
        )
        statement = (
            update(outbox_events)
            .where(outbox_events.c.outbox_id == candidate.c.outbox_id)
            .values(
                lease_owner=worker_id,
                lease_until=now + timedelta(seconds=lease_seconds),
                attempt=outbox_events.c.attempt + 1,
            )
            .returning(*outbox_events.c)
        )
        async with self.engine.begin() as connection:
            rows = (await connection.execute(statement)).mappings()
            return tuple(OutboxRecord.model_validate(dict(row)) for row in rows)

    async def mark_outbox_published(self, outbox_id: str, worker_id: str, now: datetime) -> bool:
        statement = (
            update(outbox_events)
            .where(
                outbox_events.c.outbox_id == outbox_id,
                outbox_events.c.lease_owner == worker_id,
                outbox_events.c.published_at.is_(None),
            )
            .values(published_at=now, lease_owner=None, lease_until=None)
            .returning(outbox_events.c.outbox_id)
        )
        async with self.engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def close(self) -> None:
        await self.engine.dispose()


def _task_from_mapping(
    row: Mapping[str, Any] | RowMapping, dependency_ids: tuple[str, ...] = ()
) -> TaskRecord:
    values = dict(row)
    values["dependency_ids"] = dependency_ids
    return TaskRecord.model_validate(values)
