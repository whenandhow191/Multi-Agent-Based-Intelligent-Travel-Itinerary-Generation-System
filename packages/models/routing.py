"""Bounded retry, fallback and escalation for normalized model gateways."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field, JsonValue

from packages.domain.common import DomainModel
from packages.harness import ModelGateway, ModelRequest, ModelRouteAttempt, ModelRouting, ModelTurn
from packages.models.adapters import ModelProviderError, ProviderFailureKind


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    provider_id: str
    model_id: str
    tier: Literal[0, 1, 2]
    gateway: ModelGateway
    max_attempts: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("candidate max_attempts must be between 1 and 3")


class ModelRoutingPolicy(DomainModel):
    max_total_attempts: Annotated[int, Field(ge=1, le=8)] = 5
    retry_base_seconds: Annotated[float, Field(ge=0, le=10)] = 0.1
    retry_max_seconds: Annotated[float, Field(ge=0, le=30)] = 2
    retryable_failures: tuple[ProviderFailureKind, ...] = (
        ProviderFailureKind.RATE_LIMIT,
        ProviderFailureKind.TIMEOUT,
        ProviderFailureKind.SERVER,
        ProviderFailureKind.TRANSPORT,
    )


class ModelRoutingError(RuntimeError):
    """All bounded candidates failed; attempts remain safe to persist."""

    def __init__(self, attempts: Sequence[ModelRouteAttempt]) -> None:
        super().__init__("all configured model candidates failed")
        self.attempts = tuple(attempts)


class RoutedModelGateway(ModelGateway):
    """Try configured candidates only; never invent or cycle routes dynamically."""

    def __init__(
        self,
        *,
        agent_id: str,
        candidates: Sequence[ModelCandidate],
        policy: ModelRoutingPolicy | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not candidates:
            raise ValueError("at least one model candidate is required")
        self.agent_id = agent_id
        self.candidates = tuple(candidates)
        self.policy = policy or ModelRoutingPolicy()
        self.sleeper = sleeper

    async def generate(self, request: ModelRequest) -> ModelTurn:
        attempts: list[ModelRouteAttempt] = []
        initial_tier = self.candidates[0].tier
        total_attempts = 0
        for candidate_index, candidate in enumerate(self.candidates):
            for local_attempt in range(candidate.max_attempts):
                if total_attempts >= self.policy.max_total_attempts:
                    raise ModelRoutingError(attempts)
                total_attempts += 1
                try:
                    turn = await candidate.gateway.generate(request)
                    if not _output_conforms(turn.output, request.output_schema):
                        raise ModelProviderError(
                            ProviderFailureKind.SCHEMA,
                            candidate.provider_id,
                            retryable=False,
                        )
                except ModelProviderError as exc:
                    attempts.append(
                        ModelRouteAttempt(
                            provider_id=candidate.provider_id,
                            model_id=candidate.model_id,
                            outcome=exc.kind.value,
                        )
                    )
                    can_retry = (
                        exc.kind in self.policy.retryable_failures
                        and local_attempt + 1 < candidate.max_attempts
                        and total_attempts < self.policy.max_total_attempts
                    )
                    if can_retry:
                        delay = min(
                            self.policy.retry_max_seconds,
                            self.policy.retry_base_seconds * (2**local_attempt),
                        )
                        if delay:
                            await self.sleeper(delay)
                        continue
                    break
                attempts.append(
                    ModelRouteAttempt(
                        provider_id=candidate.provider_id,
                        model_id=candidate.model_id,
                        outcome="success",
                    )
                )
                return turn.model_copy(
                    update={
                        "routing": ModelRouting(
                            selected_provider=candidate.provider_id,
                            selected_model=turn.model_id,
                            attempts=tuple(attempts),
                            fallback_used=candidate_index > 0,
                            escalation_used=candidate.tier > initial_tier,
                            brain_downgraded=(
                                self.agent_id == "coordinator" and candidate.tier < initial_tier
                            ),
                        )
                    }
                )
        raise ModelRoutingError(attempts)


def _output_conforms(
    output: Mapping[str, JsonValue] | None, schema: Mapping[str, JsonValue] | None
) -> bool:
    if output is None or schema is None:
        return True
    return _value_conforms(dict(output), schema)


def _value_conforms(value: JsonValue, schema: Mapping[str, JsonValue]) -> bool:
    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _matches_type(value, expected_type):
        return False
    if expected_type == "object" and isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list) and any(
            isinstance(name, str) and name not in value for name in required
        ):
            return False
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for name, child_schema in properties.items():
                if (
                    name in value
                    and isinstance(child_schema, dict)
                    and not _value_conforms(value[name], child_schema)
                ):
                    return False
    if expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return all(_value_conforms(item, item_schema) for item in value)
    return True


def _matches_type(value: JsonValue, expected: str) -> bool:
    expected_types: dict[str, type[Any] | tuple[type[Any], ...]] = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    target = expected_types.get(expected)
    if target is None:
        return True
    if expected in {"integer", "number"} and isinstance(value, bool):
        return False
    return isinstance(value, target)
