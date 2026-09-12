"""Heartbeat, checkpoint recovery, and transactional outbox services."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import JsonValue

from packages.harness.persistence import (
    CheckpointRecord,
    HarnessRepository,
    OutboxRecord,
)

OutboxHandler = Callable[[OutboxRecord], Awaitable[None]]
Clock = Callable[[], datetime]


class LeaseLostError(RuntimeError):
    """Raised when a worker tries to continue after losing its task lease."""


class HeartbeatService:
    """Extend an owned, unexpired task lease."""

    def __init__(
        self,
        repository: HarnessRepository,
        *,
        lease_seconds: int = 30,
        interval_seconds: float = 10,
        clock: Clock | None = None,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if interval_seconds <= 0 or interval_seconds >= lease_seconds:
            raise ValueError("heartbeat interval must be positive and shorter than the lease")
        self.repository = repository
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    async def pulse(self, task_id: str, worker_id: str, now: datetime) -> None:
        """Fail closed when ownership changed or the previous lease expired."""

        renewed = await self.repository.heartbeat_task(task_id, worker_id, now, self.lease_seconds)
        if not renewed:
            raise LeaseLostError(f"worker {worker_id} no longer owns task {task_id}")

    async def run_until_stopped(self, task_id: str, worker_id: str, stop: asyncio.Event) -> None:
        """Renew periodically until work ends or ownership is lost."""

        while not stop.is_set():
            await self.pulse(task_id, worker_id, self.clock())
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue


class CheckpointManager:
    """Persist and retrieve immutable step contexts."""

    def __init__(self, repository: HarnessRepository) -> None:
        self.repository = repository

    async def save(
        self,
        *,
        run_id: str,
        task_id: str,
        step: int,
        context: dict[str, JsonValue],
        now: datetime,
    ) -> CheckpointRecord:
        checkpoint = CheckpointRecord(
            checkpoint_id=f"checkpoint_{task_id}_{step}",
            run_id=run_id,
            task_id=task_id,
            step=step,
            context=context,
            created_at=now,
        )
        await self.repository.save_checkpoint(checkpoint)
        return checkpoint

    async def latest(self, task_id: str) -> CheckpointRecord | None:
        return await self.repository.latest_checkpoint(task_id)


class RecoveryCoordinator:
    """Release expired leases before workers resume from the latest checkpoint."""

    def __init__(self, repository: HarnessRepository) -> None:
        self.repository = repository

    async def recover(self, now: datetime) -> int:
        return await self.repository.recover_expired_leases(now)

    async def resume_checkpoint(self, task_id: str) -> CheckpointRecord | None:
        return await self.repository.latest_checkpoint(task_id)


class OutboxPublisher:
    """Claim and publish committed events without concurrent duplicate delivery."""

    def __init__(
        self,
        repository: HarnessRepository,
        handler: OutboxHandler,
        *,
        lease_seconds: int = 30,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.repository = repository
        self.handler = handler
        self.lease_seconds = lease_seconds

    async def drain_once(self, *, worker_id: str, now: datetime, limit: int = 100) -> int:
        """Publish one leased batch and mark each row only after success."""

        leased = await self.repository.lease_outbox(
            worker_id=worker_id,
            now=now,
            lease_seconds=self.lease_seconds,
            limit=limit,
        )
        published = 0
        for item in leased:
            await self.handler(item)
            if not await self.repository.mark_outbox_published(item.outbox_id, worker_id, now):
                raise LeaseLostError(f"outbox lease lost: {item.outbox_id}")
            published += 1
        return published
