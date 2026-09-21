"""C47 concurrency, bounded failure, recovery, and performance acceptance."""

import asyncio
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from apps.api.run_service import InMemoryTripRunService
from packages.evals import ProbeOutcome, ResilienceSuite
from packages.evals.fixtures import build_trip_request
from packages.harness import FinishReason, ModelGateway, ModelRequest, ModelTurn
from packages.models import (
    ModelCandidate,
    ModelProviderError,
    ModelRoutingPolicy,
    ProviderFailureKind,
    RoutedModelGateway,
)
from tests.test_model_gateway import build_request


class _SequenceGateway(ModelGateway):
    def __init__(self, outcomes: Sequence[ModelTurn | ModelProviderError]) -> None:
        self.outcomes = list(outcomes)

    async def generate(self, request: ModelRequest) -> ModelTurn:
        del request
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, ModelProviderError):
            raise outcome
        return outcome


def test_concurrent_idempotent_creates_never_duplicate_a_run() -> None:
    service = InMemoryTripRunService()
    request = build_trip_request()
    with ThreadPoolExecutor(max_workers=16) as pool:
        created = list(pool.map(lambda _: service.create(request, "same-key"), range(64)))
    assert len({item.run.run_id for item in created}) == 1
    assert len(service._runs) == 1  # noqa: SLF001 - white-box race acceptance


def test_provider_failure_returns_explicit_partial_through_fallback() -> None:
    failure = ModelProviderError(
        ProviderFailureKind.SERVER,
        "deepseek",
        retryable=True,
    )
    success = ModelTurn(
        output={"message": "fixture fallback"},
        finish_reason=FinishReason.STOP,
        model_id="fixture-fallback",
    )
    router = RoutedModelGateway(
        agent_id="critic",
        candidates=(
            ModelCandidate("deepseek", "deepseek-flash", 0, _SequenceGateway((failure,))),
            ModelCandidate("fixture", "fixture-fallback", 0, _SequenceGateway((success,))),
        ),
        policy=ModelRoutingPolicy(retry_base_seconds=0),
    )

    async def provider_probe() -> ProbeOutcome:
        turn = await router.generate(build_request())
        assert turn.routing is not None
        return "partial" if turn.routing.fallback_used else "success"

    report = asyncio.run(ResilienceSuite().run((("provider_failure", provider_probe),)))
    assert report.partial_count == 1
    assert report.failure_count == 0


def test_suite_enforces_concurrency_timeout_and_keeps_other_results() -> None:
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def healthy() -> ProbeOutcome:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        try:
            await asyncio.sleep(0.005)
        finally:
            async with lock:
                active -= 1
        return "success"

    async def crashed_worker() -> ProbeOutcome:
        raise RuntimeError("simulated worker crash")

    async def timed_out_provider() -> ProbeOutcome:
        await asyncio.sleep(0.2)
        return "success"

    probes = tuple((f"healthy_{index}", healthy) for index in range(20)) + (
        ("crash", crashed_worker),
        ("timeout", timed_out_provider),
    )
    report = asyncio.run(ResilienceSuite(max_concurrency=4, timeout_seconds=0.05).run(probes))
    assert peak == 4
    assert report.success_count == 20
    assert report.failure_count == 2
    assert {item.failure_type for item in report.samples if item.outcome == "failure"} == {
        "RuntimeError",
        "TimeoutError",
    }
    assert report.p95_latency_ms < 200
