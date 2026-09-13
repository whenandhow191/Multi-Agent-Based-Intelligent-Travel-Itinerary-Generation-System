"""Agent-separated model benchmark runner with quality, latency and cost metrics."""

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import monotonic
from typing import Annotated

from pydantic import Field, JsonValue

from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.harness import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPolicy,
    ModelRequest,
    TraceContext,
)


class ModelBenchmarkCase(DomainModel):
    case_id: Identifier
    agent_id: Identifier
    prompt: NonEmptyText
    expected_keys: tuple[NonEmptyText, ...]
    hard_constraints: dict[NonEmptyText, JsonValue]
    citation_key: NonEmptyText
    minimum_citations: Annotated[int, Field(ge=0, le=100)] = 1


class BenchmarkCandidate(DomainModel):
    candidate_id: Identifier
    model_alias: NonEmptyText


class BenchmarkSample(DomainModel):
    case_id: Identifier
    agent_id: Identifier
    candidate_id: Identifier
    schema_passed: bool
    hard_constraints_passed: bool
    citations_passed: bool
    latency_ms: Annotated[int, Field(ge=0)]
    cost_microunits: Annotated[int, Field(ge=0)]
    succeeded: bool
    error_code: Identifier | None = None


class AgentBenchmarkMetrics(DomainModel):
    agent_id: Identifier
    candidate_id: Identifier
    cases: Annotated[int, Field(ge=1)]
    schema_pass_rate: float
    hard_constraint_pass_rate: float
    citation_pass_rate: float
    average_latency_ms: float
    average_success_cost_microunits: float | None
    success_rate: float


class ModelBenchmarkReport(DomainModel):
    dataset_size: int
    samples: tuple[BenchmarkSample, ...]
    metrics: tuple[AgentBenchmarkMetrics, ...]

    def render_markdown(self) -> str:
        lines = [
            "# Model benchmark report",
            "",
            f"Dataset cases: {self.dataset_size}",
            "",
            "| Candidate | Agent | Schema | Hard constraints | Citations | Success | "
            "Latency ms | Success cost |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for item in self.metrics:
            cost = (
                "n/a"
                if item.average_success_cost_microunits is None
                else f"{item.average_success_cost_microunits:.1f}"
            )
            lines.append(
                f"| {item.candidate_id} | {item.agent_id} | "
                f"{item.schema_pass_rate:.1%} | {item.hard_constraint_pass_rate:.1%} | "
                f"{item.citation_pass_rate:.1%} | {item.success_rate:.1%} | "
                f"{item.average_latency_ms:.1f} | {cost} |"
            )
        return "\n".join(lines) + "\n"


class ModelBenchmarkRunner:
    def __init__(self, cases: Sequence[ModelBenchmarkCase]) -> None:
        if not 20 <= len(cases) <= 50:
            raise ValueError("model benchmark dataset must contain 20 to 50 cases")
        self.cases = tuple(cases)

    async def run(
        self, candidates: Mapping[BenchmarkCandidate, ModelGateway]
    ) -> ModelBenchmarkReport:
        samples: list[BenchmarkSample] = []
        for candidate, gateway in candidates.items():
            for case in self.cases:
                samples.append(await self._run_case(candidate, gateway, case))
        return ModelBenchmarkReport(
            dataset_size=len(self.cases),
            samples=tuple(samples),
            metrics=_metrics(samples),
        )

    async def _run_case(
        self,
        candidate: BenchmarkCandidate,
        gateway: ModelGateway,
        case: ModelBenchmarkCase,
    ) -> BenchmarkSample:
        request = ModelRequest(
            messages=(ModelMessage(role=MessageRole.USER, content=case.prompt),),
            output_schema={
                "type": "object",
                "properties": {key: {} for key in case.expected_keys},
                "required": list(case.expected_keys),
            },
            policy=ModelPolicy(model_alias=case.agent_id),
            trace=TraceContext(
                trace_id=f"trace_{case.case_id}",
                run_id=f"benchmark_{candidate.candidate_id}",
                task_id=case.case_id,
            ),
        )
        started = monotonic()
        try:
            turn = await gateway.generate(request)
            output = turn.output or {}
            schema_passed = all(key in output for key in case.expected_keys)
            hard_passed = all(
                output.get(key) == value for key, value in case.hard_constraints.items()
            )
            citations = output.get(case.citation_key)
            citations_passed = (
                isinstance(citations, list) and len(citations) >= case.minimum_citations
            )
            error_code = None
            cost = turn.usage.estimated_cost_microunits
        except Exception:
            schema_passed = hard_passed = citations_passed = False
            error_code = "model_error"
            cost = 0
        latency_ms = max(0, round((monotonic() - started) * 1000))
        return BenchmarkSample(
            case_id=case.case_id,
            agent_id=case.agent_id,
            candidate_id=candidate.candidate_id,
            schema_passed=schema_passed,
            hard_constraints_passed=hard_passed,
            citations_passed=citations_passed,
            latency_ms=latency_ms,
            cost_microunits=cost,
            succeeded=schema_passed and hard_passed and citations_passed,
            error_code=error_code,
        )


def load_benchmark_cases(path: Path) -> tuple[ModelBenchmarkCase, ...]:
    cases = tuple(
        ModelBenchmarkCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    counts = Counter(case.agent_id for case in cases)
    if any(count < 4 for count in counts.values()):
        raise ValueError("every represented Agent needs at least four benchmark cases")
    return cases


def _metrics(samples: Sequence[BenchmarkSample]) -> tuple[AgentBenchmarkMetrics, ...]:
    grouped: dict[tuple[str, str], list[BenchmarkSample]] = {}
    for sample in samples:
        grouped.setdefault((sample.candidate_id, sample.agent_id), []).append(sample)
    output = []
    for (candidate_id, agent_id), group in sorted(grouped.items()):
        count = len(group)
        successful_costs = [item.cost_microunits for item in group if item.succeeded]
        output.append(
            AgentBenchmarkMetrics(
                agent_id=agent_id,
                candidate_id=candidate_id,
                cases=count,
                schema_pass_rate=sum(item.schema_passed for item in group) / count,
                hard_constraint_pass_rate=(
                    sum(item.hard_constraints_passed for item in group) / count
                ),
                citation_pass_rate=sum(item.citations_passed for item in group) / count,
                average_latency_ms=sum(item.latency_ms for item in group) / count,
                average_success_cost_microunits=(
                    sum(successful_costs) / len(successful_costs) if successful_costs else None
                ),
                success_rate=sum(item.succeeded for item in group) / count,
            )
        )
    return tuple(output)
