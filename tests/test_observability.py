import asyncio
from io import StringIO
from json import loads

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from packages.domain import Claim, ClaimStatus, Evidence
from packages.evals.fixtures import build_synthetic_scenario
from packages.harness import (
    FinishReason,
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPolicy,
    ModelRequest,
    ModelTurn,
    ModelUsage,
    ToolContext,
    ToolGateway,
    ToolRegistry,
    TraceContext,
    fixture_echo_tool,
)
from packages.observability import (
    InstrumentedModelGateway,
    InstrumentedToolGateway,
    LineageIndex,
    LineageRecord,
    TelemetryRecorder,
    configure_structured_logger,
)


class _FixtureGateway(ModelGateway):
    async def generate(self, request: ModelRequest) -> ModelTurn:
        return ModelTurn(
            output={"status": "ok"},
            usage=ModelUsage(
                input_tokens=12,
                output_tokens=4,
                estimated_cost_microunits=7,
            ),
            finish_reason=FinishReason.STOP,
            model_id="fixture-model",
        )


def _telemetry() -> tuple[TelemetryRecorder, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    meter_provider = MeterProvider()
    return (
        TelemetryRecorder(
            tracer=tracer_provider.get_tracer("travel.tests"),
            meter=meter_provider.get_meter("travel.tests"),
        ),
        exporter,
    )


def test_model_and_tool_spans_are_correlated_without_sensitive_payloads() -> None:
    telemetry, exporter = _telemetry()
    request = ModelRequest(
        messages=(ModelMessage(role=MessageRole.USER, content="api_key=must-not-leak"),),
        policy=ModelPolicy(model_alias="fixture"),
        trace=TraceContext(trace_id="trace_1", run_id="run_1", task_id="task_1"),
    )
    turn = asyncio.run(
        InstrumentedModelGateway(_FixtureGateway(), telemetry, "critic").generate(request)
    )
    tool = InstrumentedToolGateway(ToolGateway(ToolRegistry((fixture_echo_tool(),))), telemetry)
    tool_result = asyncio.run(
        tool.execute(
            call_id="call_1",
            tool_name="fixture.echo",
            arguments={"message": "safe", "repeat": 1},
            context=ToolContext(
                run_id="run_1",
                task_id="task_1",
                agent_id="critic",
                trace_id="trace_1",
            ),
            allowlist=("fixture.echo",),
        )
    )

    assert turn.usage.estimated_cost_microunits == 7
    assert tool_result.data["echoed"] == "safe"
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {"model.generate", "tool.execute"}
    serialized = repr([(span.name, span.attributes) for span in spans])
    assert "must-not-leak" not in serialized
    assert "run_1" in serialized
    assert "travel.cost.microunits" in serialized


def test_lineage_traces_claim_to_agent_artifact_and_tool() -> None:
    scenario = build_synthetic_scenario()
    evidence: Evidence = scenario.evidence[0]
    claim = Claim(
        claim_id="claim_open_hours",
        subject_ref=scenario.places[0].place_id,
        predicate="opening_hours",
        value="08:30-17:00",
        status=ClaimStatus.VERIFIED,
        evidence_ids=(evidence.evidence_id,),
    )
    index = LineageIndex()
    index.record(
        LineageRecord(
            trace_id="trace_1",
            run_id="run_1",
            task_id="destination_intelligence",
            agent_id="destination_intelligence",
            artifact_id="artifact_destination_v1",
            claim_id=claim.claim_id,
            tool_names=("places.search",),
        )
    )

    traced = index.trace_claim(claim.claim_id)
    assert len(traced) == 1
    assert traced[0].agent_id == "destination_intelligence"
    assert traced[0].artifact_id == "artifact_destination_v1"
    assert traced[0].tool_names == ("places.search",)


def test_structured_logger_redacts_nested_secrets_and_prompts() -> None:
    stream = StringIO()
    logger = configure_structured_logger("travel.tests.redaction", stream)
    logger.info(
        {
            "event": "model.completed",
            "run_id": "run_1",
            "api_key": "secret-value",
            "nested": {"access_token": "token-value", "duration_ms": 12},
            "prompt": "private prompt",
        }
    )
    payload = loads(stream.getvalue())
    assert payload["event"] == "model.completed"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["access_token"] == "[REDACTED]"
    assert payload["nested"]["duration_ms"] == 12
    assert "private prompt" not in stream.getvalue()
