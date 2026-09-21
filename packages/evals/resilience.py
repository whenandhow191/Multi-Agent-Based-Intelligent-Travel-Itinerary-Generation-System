"""Bounded concurrency runner for resilience and performance acceptance probes."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from math import ceil
from time import monotonic
from typing import Annotated, Literal

from pydantic import Field

from packages.domain.common import DomainModel, Identifier, NonEmptyText

ProbeOutcome = Literal["success", "partial"]
Probe = Callable[[], Awaitable[ProbeOutcome]]


class ResilienceSample(DomainModel):
    name: Identifier
    outcome: Literal["success", "partial", "failure"]
    latency_ms: Annotated[float, Field(ge=0)]
    failure_type: NonEmptyText | None = None


class ResilienceReport(DomainModel):
    samples: tuple[ResilienceSample, ...]
    success_count: Annotated[int, Field(ge=0)]
    partial_count: Annotated[int, Field(ge=0)]
    failure_count: Annotated[int, Field(ge=0)]
    p95_latency_ms: Annotated[float, Field(ge=0)]


class ResilienceSuite:
    """Run probes with hard concurrency and timeout bounds; never retry forever."""

    def __init__(self, *, max_concurrency: int = 8, timeout_seconds: float = 5) -> None:
        if not 1 <= max_concurrency <= 256:
            raise ValueError("max_concurrency must be between 1 and 256")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds

    async def run(self, probes: Sequence[tuple[str, Probe]]) -> ResilienceReport:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def execute(name: str, probe: Probe) -> ResilienceSample:
            async with semaphore:
                started = monotonic()
                try:
                    outcome = await asyncio.wait_for(probe(), timeout=self.timeout_seconds)
                except Exception as exc:
                    return ResilienceSample(
                        name=name,
                        outcome="failure",
                        latency_ms=(monotonic() - started) * 1000,
                        failure_type=type(exc).__name__,
                    )
                return ResilienceSample(
                    name=name,
                    outcome=outcome,
                    latency_ms=(monotonic() - started) * 1000,
                )

        samples = tuple(await asyncio.gather(*(execute(name, probe) for name, probe in probes)))
        latencies = sorted(sample.latency_ms for sample in samples)
        p95 = latencies[max(0, ceil(len(latencies) * 0.95) - 1)] if latencies else 0
        return ResilienceReport(
            samples=samples,
            success_count=sum(sample.outcome == "success" for sample in samples),
            partial_count=sum(sample.outcome == "partial" for sample in samples),
            failure_count=sum(sample.outcome == "failure" for sample in samples),
            p95_latency_ms=p95,
        )
