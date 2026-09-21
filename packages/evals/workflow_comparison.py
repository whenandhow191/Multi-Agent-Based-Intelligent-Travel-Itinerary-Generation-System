"""Single-Agent versus 1+4 workflow quality, latency, and cost comparison."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated

from pydantic import Field

from packages.domain.common import DomainModel, Identifier, NonEmptyText


class WorkflowEvaluationCase(DomainModel):
    case_id: Identifier
    sequence: Annotated[int, Field(ge=1)]
    scenario: NonEmptyText
    risk_category: Identifier


class WorkflowOutcome(DomainModel):
    schema_passed: bool
    hard_constraints_passed: bool
    citation_coverage: Annotated[float, Field(ge=0, le=1)]
    task_succeeded: bool
    latency_ms: Annotated[int, Field(ge=0)]
    cost_microunits: Annotated[int, Field(ge=0)]


WorkflowEvaluator = Callable[[WorkflowEvaluationCase], Awaitable[WorkflowOutcome]]


class WorkflowMetrics(DomainModel):
    candidate_id: Identifier
    cases: Annotated[int, Field(ge=1)]
    schema_pass_rate: Annotated[float, Field(ge=0, le=1)]
    hard_constraint_pass_rate: Annotated[float, Field(ge=0, le=1)]
    average_citation_coverage: Annotated[float, Field(ge=0, le=1)]
    success_rate: Annotated[float, Field(ge=0, le=1)]
    average_latency_ms: Annotated[float, Field(ge=0)]
    average_cost_microunits: Annotated[float, Field(ge=0)]
    accepted: bool


class WorkflowComparisonReport(DomainModel):
    dataset_size: Annotated[int, Field(ge=20, le=50)]
    metrics: tuple[WorkflowMetrics, ...]

    def render_markdown(self) -> str:
        lines = [
            "# Single Agent vs 1+4 workflow",
            "",
            f"Dataset cases: {self.dataset_size}",
            "",
            "| Workflow | Schema | Hard constraints | Citations | Success | Latency ms | "
            "Cost microunits | Gate |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for item in self.metrics:
            lines.append(
                f"| {item.candidate_id} | {item.schema_pass_rate:.1%} | "
                f"{item.hard_constraint_pass_rate:.1%} | "
                f"{item.average_citation_coverage:.1%} | {item.success_rate:.1%} | "
                f"{item.average_latency_ms:.1f} | {item.average_cost_microunits:.1f} | "
                f"{'PASS' if item.accepted else 'FAIL'} |"
            )
        return "\n".join(lines) + "\n"


class WorkflowAcceptance(DomainModel):
    minimum_schema_pass_rate: float = 0.95
    minimum_hard_constraint_pass_rate: float = 0.95
    minimum_citation_coverage: float = 0.95
    minimum_success_rate: float = 0.90


class WorkflowComparisonRunner:
    def __init__(
        self,
        cases: Sequence[WorkflowEvaluationCase],
        acceptance: WorkflowAcceptance | None = None,
    ) -> None:
        if not 20 <= len(cases) <= 50:
            raise ValueError("workflow comparison dataset must contain 20 to 50 cases")
        self.cases = tuple(cases)
        self.acceptance = acceptance or WorkflowAcceptance()

    async def run(self, candidates: Mapping[str, WorkflowEvaluator]) -> WorkflowComparisonReport:
        if set(candidates) != {"single_agent", "one_plus_four"}:
            raise ValueError("comparison requires single_agent and one_plus_four")
        metrics: list[WorkflowMetrics] = []
        for candidate_id, evaluator in candidates.items():
            outcomes = [await evaluator(case) for case in self.cases]
            metrics.append(self._metrics(candidate_id, outcomes))
        return WorkflowComparisonReport(dataset_size=len(self.cases), metrics=tuple(metrics))

    def _metrics(self, candidate_id: str, outcomes: Sequence[WorkflowOutcome]) -> WorkflowMetrics:
        count = len(outcomes)
        schema_rate = sum(item.schema_passed for item in outcomes) / count
        hard_rate = sum(item.hard_constraints_passed for item in outcomes) / count
        citation_coverage = sum(item.citation_coverage for item in outcomes) / count
        success_rate = sum(item.task_succeeded for item in outcomes) / count
        accepted = (
            schema_rate >= self.acceptance.minimum_schema_pass_rate
            and hard_rate >= self.acceptance.minimum_hard_constraint_pass_rate
            and citation_coverage >= self.acceptance.minimum_citation_coverage
            and success_rate >= self.acceptance.minimum_success_rate
        )
        return WorkflowMetrics(
            candidate_id=candidate_id,
            cases=count,
            schema_pass_rate=schema_rate,
            hard_constraint_pass_rate=hard_rate,
            average_citation_coverage=citation_coverage,
            success_rate=success_rate,
            average_latency_ms=sum(item.latency_ms for item in outcomes) / count,
            average_cost_microunits=sum(item.cost_microunits for item in outcomes) / count,
            accepted=accepted,
        )


def load_workflow_cases(path: Path) -> tuple[WorkflowEvaluationCase, ...]:
    cases = tuple(
        WorkflowEvaluationCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("workflow comparison case IDs must be unique")
    return cases
