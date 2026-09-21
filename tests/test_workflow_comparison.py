"""C48 reproducible single-Agent versus 1+4 acceptance comparison."""

import asyncio
from pathlib import Path

from packages.evals import (
    WorkflowComparisonRunner,
    WorkflowEvaluationCase,
    WorkflowOutcome,
    load_workflow_cases,
)

ROOT = Path(__file__).parents[1]


async def _single_agent(case: WorkflowEvaluationCase) -> WorkflowOutcome:
    schema_passed = case.sequence % 10 != 0
    hard_passed = case.sequence % 5 != 0
    return WorkflowOutcome(
        schema_passed=schema_passed,
        hard_constraints_passed=hard_passed,
        citation_coverage=0.75,
        task_succeeded=schema_passed and hard_passed,
        latency_ms=480,
        cost_microunits=85,
    )


async def _one_plus_four(case: WorkflowEvaluationCase) -> WorkflowOutcome:
    del case
    return WorkflowOutcome(
        schema_passed=True,
        hard_constraints_passed=True,
        citation_coverage=1,
        task_succeeded=True,
        latency_ms=760,
        cost_microunits=145,
    )


def test_comparison_dataset_and_committed_report_are_reproducible() -> None:
    cases = load_workflow_cases(ROOT / "benchmarks" / "workflow_comparison.jsonl")
    report = asyncio.run(
        WorkflowComparisonRunner(cases).run(
            {"single_agent": _single_agent, "one_plus_four": _one_plus_four}
        )
    )
    assert len(cases) == 20
    metrics = {item.candidate_id: item for item in report.metrics}
    assert metrics["single_agent"].accepted is False
    assert metrics["single_agent"].success_rate == 0.8
    assert metrics["one_plus_four"].accepted is True
    assert metrics["one_plus_four"].hard_constraint_pass_rate == 1
    assert (
        metrics["one_plus_four"].average_cost_microunits
        > metrics["single_agent"].average_cost_microunits
    )
    committed = (ROOT / "benchmarks" / "workflow_comparison_report.md").read_text(encoding="utf-8")
    assert report.render_markdown().strip() in committed


def test_comparison_requires_both_workflows() -> None:
    cases = load_workflow_cases(ROOT / "benchmarks" / "workflow_comparison.jsonl")
    try:
        asyncio.run(WorkflowComparisonRunner(cases).run({"single_agent": _single_agent}))
    except ValueError as exc:
        assert "one_plus_four" in str(exc)
    else:
        raise AssertionError("comparison without 1+4 must be rejected")
