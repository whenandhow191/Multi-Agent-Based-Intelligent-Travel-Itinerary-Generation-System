"""Resilient HTTP primitives shared by external provider adapters."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, cast

import httpx
from pydantic import Field, JsonValue

from packages.domain import StoragePolicy
from packages.domain.common import DomainModel, Identifier, NonEmptyText


class ProviderHttpError(RuntimeError):
    """Base error with a stable provider-facing classification."""

    code = "provider_http_error"


class ProviderUnavailableError(ProviderHttpError):
    """Raised after all retry attempts have failed."""

    code = "provider_unavailable"


class CircuitOpenError(ProviderHttpError):
    """Raised while calls are blocked by an open circuit breaker."""

    code = "provider_circuit_open"


class ProviderResponseTooLargeError(ProviderHttpError):
    """Raised before parsing a response that exceeds the configured limit."""

    code = "provider_response_too_large"


class HttpProviderPolicy(DomainModel):
    """Bounded resilience, cache, and persistence policy for one provider."""

    timeout_seconds: float = Field(default=10, gt=0, le=120)
    max_attempts: int = Field(default=3, ge=1, le=6)
    backoff_base_seconds: float = Field(default=0.1, ge=0, le=10)
    requests_per_second: float = Field(default=5, gt=0, le=100)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    circuit_recovery_seconds: float = Field(default=30, gt=0, le=3600)
    cache_ttl_seconds: float = Field(default=60, ge=0, le=86_400)
    max_response_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    storage_policy: StoragePolicy = StoragePolicy.IDS_AND_METADATA
    allowed_storage_fields: tuple[NonEmptyText, ...] = ()


class ProviderCallAudit(DomainModel):
    """Secret-free call metadata suitable for logs or persistent audit tables."""

    provider: Identifier
    operation: Identifier
    request_hash: NonEmptyText
    status_code: int | None
    attempts: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cache_hit: bool
    outcome: Identifier
    stored_response: JsonValue | None = None


class AuditSink(Protocol):
    async def record(self, event: ProviderCallAudit) -> None: ...


class MemoryAuditSink:
    """Lock-protected audit sink used by tests and local development."""

    def __init__(self) -> None:
        self.events: list[ProviderCallAudit] = []
        self._lock = asyncio.Lock()

    async def record(self, event: ProviderCallAudit) -> None:
        async with self._lock:
            self.events.append(event)


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    value: JsonValue


class _RateLimiter:
    def __init__(self, requests_per_second: float, clock: Callable[[], float]) -> None:
        self._minimum_interval = 1 / requests_per_second
        self._clock = clock
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def wait(self, sleeper: Callable[[float], Awaitable[None]]) -> None:
        async with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + self._minimum_interval
        if delay:
            await sleeper(delay)


class ResilientHttpProvider:
    """Async JSON client with bounded retries, cache, rate limiting and circuit breaking."""

    def __init__(
        self,
        *,
        provider: str,
        client: httpx.AsyncClient,
        policy: HttpProviderPolicy | None = None,
        audit_sink: AuditSink | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.provider = provider
        self.client = client
        self.policy = policy or HttpProviderPolicy()
        self.audit_sink = audit_sink or MemoryAuditSink()
        self._sleeper = sleeper
        self._clock = clock
        self._rate_limiter = _RateLimiter(self.policy.requests_per_second, clock)
        self._cache: dict[str, _CacheEntry] = {}
        self._consecutive_failures = 0
        self._circuit_opened_at: float | None = None

    async def request_json(
        self,
        *,
        operation: str,
        method: str,
        url: str,
        params: Mapping[str, str | int | float] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: JsonValue | None = None,
        cache: bool = True,
    ) -> JsonValue:
        """Execute a bounded request while keeping credentials out of audit records."""

        started = self._clock()
        request_hash = _request_hash(method, url, params, json_body)
        cache_key = request_hash
        if method.upper() == "GET" and cache and self.policy.cache_ttl_seconds > 0:
            cached = self._cache.get(cache_key)
            if cached is not None and cached.expires_at > self._clock():
                await self._audit(
                    operation=operation,
                    request_hash=request_hash,
                    status_code=200,
                    attempts=0,
                    started=started,
                    cache_hit=True,
                    outcome="success",
                    response=cached.value,
                )
                return cached.value

        self._ensure_circuit_allows_request()
        last_error: Exception | None = None
        last_status: int | None = None
        attempts = 0
        for attempt in range(1, self.policy.max_attempts + 1):
            attempts = attempt
            await self._rate_limiter.wait(self._sleeper)
            try:
                response = await self.client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json_body,
                    timeout=self.policy.timeout_seconds,
                )
                last_status = response.status_code
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable provider response", request=response.request, response=response
                    )
                response.raise_for_status()
                if len(response.content) > self.policy.max_response_bytes:
                    raise ProviderResponseTooLargeError("provider response exceeded byte limit")
                value = cast(JsonValue, response.json())
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt < self.policy.max_attempts and _is_retryable(exc):
                    await self._sleeper(self.policy.backoff_base_seconds * (2 ** (attempt - 1)))
                    continue
                break
            except (json.JSONDecodeError, ValueError, ProviderResponseTooLargeError) as exc:
                last_error = exc
                break
            else:
                self._consecutive_failures = 0
                self._circuit_opened_at = None
                if method.upper() == "GET" and cache and self.policy.cache_ttl_seconds > 0:
                    self._cache[cache_key] = _CacheEntry(
                        expires_at=self._clock() + self.policy.cache_ttl_seconds,
                        value=value,
                    )
                await self._audit(
                    operation=operation,
                    request_hash=request_hash,
                    status_code=response.status_code,
                    attempts=attempts,
                    started=started,
                    cache_hit=False,
                    outcome="success",
                    response=value,
                )
                return value

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.policy.circuit_failure_threshold:
            self._circuit_opened_at = self._clock()
        await self._audit(
            operation=operation,
            request_hash=request_hash,
            status_code=last_status,
            attempts=attempts,
            started=started,
            cache_hit=False,
            outcome="failure",
            response=None,
        )
        raise ProviderUnavailableError(
            f"{self.provider}.{operation} failed after {attempts} attempt(s)"
        ) from last_error

    def _ensure_circuit_allows_request(self) -> None:
        if self._circuit_opened_at is None:
            return
        if self._clock() - self._circuit_opened_at >= self.policy.circuit_recovery_seconds:
            self._circuit_opened_at = None
            self._consecutive_failures = 0
            return
        raise CircuitOpenError(f"{self.provider} circuit is open")

    async def _audit(
        self,
        *,
        operation: str,
        request_hash: str,
        status_code: int | None,
        attempts: int,
        started: float,
        cache_hit: bool,
        outcome: str,
        response: JsonValue | None,
    ) -> None:
        await self.audit_sink.record(
            ProviderCallAudit(
                provider=self.provider,
                operation=operation,
                request_hash=request_hash,
                status_code=status_code,
                attempts=attempts,
                latency_ms=max(0, round((self._clock() - started) * 1000)),
                cache_hit=cache_hit,
                outcome=outcome,
                stored_response=_stored_response(
                    response,
                    self.policy.storage_policy,
                    self.policy.allowed_storage_fields,
                ),
            )
        )


def _request_hash(
    method: str,
    url: str,
    params: Mapping[str, str | int | float] | None,
    json_body: JsonValue | None,
) -> str:
    material = json.dumps(
        {
            "method": method.upper(),
            "url": url.split("?", 1)[0],
            "params": params,
            "json": json_body,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return f"sha256:{hashlib.sha256(material).hexdigest()}"


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return isinstance(error, httpx.HTTPStatusError) and (
        error.response.status_code == 429 or error.response.status_code >= 500
    )


def _stored_response(
    response: JsonValue | None,
    policy: StoragePolicy,
    allowed_fields: Sequence[str],
) -> JsonValue | None:
    if response is None or policy in {StoragePolicy.MEMORY_ONLY, StoragePolicy.IDS_AND_METADATA}:
        return None
    if policy is StoragePolicy.FULL_LICENSED:
        return response
    if not isinstance(response, dict):
        return None
    return {key: value for key, value in response.items() if key in set(allowed_fields)}
