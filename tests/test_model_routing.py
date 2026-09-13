"""Tests for finite model retry, fallback, escalation and Brain downgrade marking."""

import asyncio
from collections.abc import Sequence

from packages.harness import FinishReason, ModelGateway, ModelRequest, ModelTurn
from packages.models import (
    ModelCandidate,
    ModelProviderError,
    ModelRoutingError,
    ModelRoutingPolicy,
    ProviderFailureKind,
    RoutedModelGateway,
)
from tests.test_model_gateway import build_request


class SequenceGateway(ModelGateway):
    def __init__(self, outcomes: Sequence[ModelTurn | ModelProviderError]) -> None:
        self.outcomes = list(outcomes)

    async def generate(self, request: ModelRequest) -> ModelTurn:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, ModelProviderError):
            raise outcome
        return outcome


def _success(model: str = "gpt-5.6-terra") -> ModelTurn:
    return ModelTurn(
        output={"message": "ok"}, finish_reason=FinishReason.STOP, model_id=model
    )


def _failure(kind: ProviderFailureKind, provider: str = "deepseek") -> ModelProviderError:
    return ModelProviderError(
        kind,
        provider,
        retryable=kind
        in {
            ProviderFailureKind.RATE_LIMIT,
            ProviderFailureKind.TIMEOUT,
            ProviderFailureKind.SERVER,
        },
    )


def test_rate_limit_retries_then_escalates_to_gpt() -> None:
    router = RoutedModelGateway(
        agent_id="critic",
        candidates=(
            ModelCandidate(
                "deepseek",
                "deepseek-flash",
                0,
                SequenceGateway(
                    (
                        _failure(ProviderFailureKind.RATE_LIMIT),
                        _failure(ProviderFailureKind.RATE_LIMIT),
                    )
                ),
                max_attempts=2,
            ),
            ModelCandidate("openai", "gpt-5.6-terra", 1, SequenceGateway((_success(),))),
        ),
        policy=ModelRoutingPolicy(retry_base_seconds=0),
    )

    turn = asyncio.run(router.generate(build_request()))

    assert turn.routing is not None
    assert turn.routing.escalation_used is True
    assert turn.routing.selected_provider == "openai"
    assert [item.outcome for item in turn.routing.attempts] == [
        "rate_limit",
        "rate_limit",
        "success",
    ]


def test_schema_failure_switches_candidate_without_retry() -> None:
    invalid = ModelTurn(
        output={}, finish_reason=FinishReason.STOP, model_id="deepseek-flash"
    )
    request = build_request().model_copy(
        update={
            "output_schema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            }
        }
    )
    router = RoutedModelGateway(
        agent_id="critic",
        candidates=(
            ModelCandidate("deepseek", "deepseek-flash", 0, SequenceGateway((invalid,))),
            ModelCandidate("openai", "gpt-5.6-terra", 1, SequenceGateway((_success(),))),
        ),
    )

    turn = asyncio.run(router.generate(request))

    assert turn.routing is not None
    assert [item.outcome for item in turn.routing.attempts] == ["schema", "success"]


def test_coordinator_downgrade_is_explicit() -> None:
    router = RoutedModelGateway(
        agent_id="coordinator",
        candidates=(
            ModelCandidate(
                "openai",
                "gpt-6-astra",
                2,
                SequenceGateway((_failure(ProviderFailureKind.QUOTA, "openai"),)),
            ),
            ModelCandidate(
                "deepseek",
                "deepseek-flash",
                0,
                SequenceGateway((_success("deepseek-flash"),)),
            ),
        ),
    )

    turn = asyncio.run(router.generate(build_request()))

    assert turn.routing is not None
    assert turn.routing.brain_downgraded is True
    assert turn.routing.escalation_used is False


def test_total_attempt_limit_prevents_cycles() -> None:
    router = RoutedModelGateway(
        agent_id="critic",
        candidates=(
            ModelCandidate(
                "deepseek",
                "deepseek-flash",
                0,
                SequenceGateway((_failure(ProviderFailureKind.TIMEOUT),) * 3),
                max_attempts=3,
            ),
        ),
        policy=ModelRoutingPolicy(max_total_attempts=2, retry_base_seconds=0),
    )

    try:
        asyncio.run(router.generate(build_request()))
    except ModelRoutingError as exc:
        assert len(exc.attempts) == 2
    else:
        raise AssertionError("bounded router must stop after max_total_attempts")
