"""C27 resilient provider client tests."""

import asyncio

import httpx
import pytest

from packages.domain import StoragePolicy
from packages.tools.http_provider import (
    CircuitOpenError,
    HttpProviderPolicy,
    MemoryAuditSink,
    ProviderUnavailableError,
    ResilientHttpProvider,
)


def test_retries_429_and_500_then_caches_success_without_storing_body() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = (429, 500, 200)[calls - 1]
        return httpx.Response(status, request=request, json={"secret": "not-persisted", "id": "1"})

    async def scenario() -> None:
        audit = MemoryAuditSink()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = ResilientHttpProvider(
                provider="fixture",
                client=client,
                policy=HttpProviderPolicy(
                    max_attempts=3,
                    backoff_base_seconds=0,
                    requests_per_second=100,
                    storage_policy=StoragePolicy.IDS_AND_METADATA,
                ),
                audit_sink=audit,
                sleeper=lambda _: asyncio.sleep(0),
            )
            first = await provider.request_json(
                operation="status_probe", method="GET", url="https://fixture.test/status"
            )
            second = await provider.request_json(
                operation="status_probe", method="GET", url="https://fixture.test/status"
            )
        assert first == second == {"secret": "not-persisted", "id": "1"}
        assert calls == 3
        assert audit.events[0].attempts == 3
        assert audit.events[0].stored_response is None
        assert audit.events[1].cache_hit is True

    asyncio.run(scenario())


def test_timeout_opens_circuit_after_bounded_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = ResilientHttpProvider(
                provider="fixture",
                client=client,
                policy=HttpProviderPolicy(
                    max_attempts=2,
                    backoff_base_seconds=0,
                    requests_per_second=100,
                    circuit_failure_threshold=1,
                ),
                sleeper=lambda _: asyncio.sleep(0),
            )
            with pytest.raises(ProviderUnavailableError):
                await provider.request_json(
                    operation="timeout_probe", method="GET", url="https://fixture.test/timeout"
                )
            with pytest.raises(CircuitOpenError):
                await provider.request_json(
                    operation="timeout_probe", method="GET", url="https://fixture.test/timeout"
                )

    asyncio.run(scenario())


def test_allowed_fields_are_the_only_response_values_in_audit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"id": "poi_1", "phone": "private"})

    async def scenario() -> None:
        audit = MemoryAuditSink()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = ResilientHttpProvider(
                provider="fixture",
                client=client,
                policy=HttpProviderPolicy(
                    requests_per_second=100,
                    storage_policy=StoragePolicy.ALLOWED_FIELDS,
                    allowed_storage_fields=("id",),
                ),
                audit_sink=audit,
            )
            await provider.request_json(
                operation="field_filter", method="GET", url="https://fixture.test/data"
            )
        assert audit.events[0].stored_response == {"id": "poi_1"}

    asyncio.run(scenario())
