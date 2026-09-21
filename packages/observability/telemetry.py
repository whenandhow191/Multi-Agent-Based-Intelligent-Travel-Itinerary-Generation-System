"""OpenTelemetry spans, safe JSON logs, and queryable claim lineage."""

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast

from opentelemetry.metrics import Meter
from opentelemetry.trace import Status, StatusCode, Tracer
from pydantic import Field

from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.harness.model_gateway import ModelGateway, ModelRequest, ModelTurn
from packages.harness.tool_gateway import (
    ToolContext,
    ToolExecutionResult,
    ToolGateway,
)

_SENSITIVE_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "key",
    "password",
    "prompt",
    "raw_response",
    "secret",
    "token",
)
_REDACTED = "[REDACTED]"


def redact_fields(value: Any, field_name: str = "") -> Any:
    """Recursively replace sensitive fields before serialization or export."""

    lowered = field_name.casefold()
    if any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS):
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(key): redact_fields(item, str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_fields(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class SafeJsonFormatter(logging.Formatter):
    """Serialize structured log dictionaries after mandatory field redaction."""

    def format(self, record: logging.LogRecord) -> str:
        message = (
            record.msg if isinstance(record.msg, Mapping) else {"message": record.getMessage()}
        )
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            **redact_fields(message),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def configure_structured_logger(name: str, stream: Any | None = None) -> logging.Logger:
    """Build an isolated JSON logger without mutating the process root logger."""

    logger = logging.getLogger(name)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


class LineageRecord(DomainModel):
    """One immutable edge connecting a Claim to its producing execution facts."""

    trace_id: Identifier
    run_id: Identifier
    task_id: Identifier
    agent_id: Identifier
    artifact_id: Identifier
    claim_id: Identifier
    tool_names: tuple[NonEmptyText, ...] = ()
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LineageIndex:
    """In-process read model for claim-to-Agent/Artifact/tool trace queries."""

    def __init__(self) -> None:
        self._records: list[LineageRecord] = []

    def record(self, item: LineageRecord) -> None:
        if item not in self._records:
            self._records.append(item)

    def trace_claim(self, claim_id: str) -> tuple[LineageRecord, ...]:
        return tuple(item for item in self._records if item.claim_id == claim_id)

    def for_run(self, run_id: str) -> tuple[LineageRecord, ...]:
        return tuple(item for item in self._records if item.run_id == run_id)


@dataclass(frozen=True)
class TelemetryRecorder:
    """Own low-cardinality OpenTelemetry instruments used by Gateway wrappers."""

    tracer: Tracer
    meter: Meter

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_duration",
            self.meter.create_histogram(
                "travel.gateway.duration",
                unit="ms",
                description="Normalized model and tool gateway latency",
            ),
        )
        object.__setattr__(
            self,
            "_cost",
            self.meter.create_counter(
                "travel.gateway.cost",
                unit="microunit",
                description="Estimated model and tool cost",
            ),
        )

    def observe(
        self,
        *,
        operation: Literal["model", "tool"],
        name: str,
        duration_ms: float,
        cost_microunits: int,
        outcome: Literal["success", "error"],
    ) -> None:
        attributes = {"operation": operation, "name": name, "outcome": outcome}
        cast(Any, self)._duration.record(duration_ms, attributes)
        cast(Any, self)._cost.add(cost_microunits, attributes)


class InstrumentedModelGateway(ModelGateway):
    """Decorate any ModelGateway with safe spans and usage/cost metrics."""

    def __init__(self, gateway: ModelGateway, telemetry: TelemetryRecorder, agent_id: str) -> None:
        self.gateway = gateway
        self.telemetry = telemetry
        self.agent_id = agent_id

    async def generate(self, request: ModelRequest) -> ModelTurn:
        started = monotonic()
        model_alias = request.policy.model_alias
        attributes: dict[str, str | int | float] = {
            "travel.run.id": request.trace.run_id,
            "travel.task.id": request.trace.task_id,
            "travel.agent.id": self.agent_id,
            "gen_ai.request.model": model_alias,
        }
        with self.telemetry.tracer.start_as_current_span(
            "model.generate", attributes=attributes
        ) as span:
            try:
                turn = await self.gateway.generate(request)
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                span.set_attribute("error.type", type(exc).__name__)
                self.telemetry.observe(
                    operation="model",
                    name=model_alias,
                    duration_ms=(monotonic() - started) * 1000,
                    cost_microunits=0,
                    outcome="error",
                )
                raise
            duration_ms = (monotonic() - started) * 1000
            span.set_attribute("gen_ai.response.model", turn.model_id)
            span.set_attribute("gen_ai.usage.input_tokens", turn.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", turn.usage.output_tokens)
            span.set_attribute("travel.cost.microunits", turn.usage.estimated_cost_microunits)
            span.set_attribute("travel.duration.ms", duration_ms)
            span.set_status(Status(StatusCode.OK))
            self.telemetry.observe(
                operation="model",
                name=model_alias,
                duration_ms=duration_ms,
                cost_microunits=turn.usage.estimated_cost_microunits,
                outcome="success",
            )
            return turn


class InstrumentedToolGateway:
    """Decorate ToolGateway without weakening allowlist or schema validation."""

    def __init__(self, gateway: ToolGateway, telemetry: TelemetryRecorder) -> None:
        self.gateway = gateway
        self.telemetry = telemetry
        self.registry = gateway.registry

    async def execute(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        allowlist: Sequence[str],
    ) -> ToolExecutionResult:
        started = monotonic()
        attributes = {
            "travel.run.id": context.run_id,
            "travel.task.id": context.task_id,
            "travel.agent.id": context.agent_id,
            "tool.name": tool_name,
        }
        with self.telemetry.tracer.start_as_current_span(
            "tool.execute", attributes=attributes
        ) as span:
            try:
                result = await self.gateway.execute(
                    call_id=call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    context=context,
                    allowlist=allowlist,
                )
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                span.set_attribute("error.type", type(exc).__name__)
                self.telemetry.observe(
                    operation="tool",
                    name=tool_name,
                    duration_ms=(monotonic() - started) * 1000,
                    cost_microunits=0,
                    outcome="error",
                )
                raise
            duration_ms = (monotonic() - started) * 1000
            span.set_attribute("travel.duration.ms", duration_ms)
            span.set_status(Status(StatusCode.OK))
            self.telemetry.observe(
                operation="tool",
                name=tool_name,
                duration_ms=duration_ms,
                cost_microunits=0,
                outcome="success",
            )
            return result
