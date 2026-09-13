"""Tests for the 25-case Agent-separated model benchmark."""

import asyncio
from pathlib import Path

from pydantic import JsonValue

from packages.evals.model_benchmark import (
    BenchmarkCandidate,
    ModelBenchmarkRunner,
    load_benchmark_cases,
)
from packages.harness import FinishReason, ModelGateway, ModelRequest, ModelTurn, ModelUsage

ROOT = Path(__file__).parents[1]


class BenchmarkFixtureGateway(ModelGateway):
    def __init__(self, *, valid: bool) -> None:
        self.valid = valid

    async def generate(self, request: ModelRequest) -> ModelTurn:
        primary_key = {
            "coordinator": "decision",
            "destination_intelligence": "places",
            "mobility_lodging": "options",
            "itinerary_planner": "plans",
            "critic": "review",
        }[request.policy.model_alias]
        output: dict[str, JsonValue] = (
            {primary_key: [], "hard_constraint_ok": True, "citations": ["fixture_ref"]}
            if self.valid
            else {"hard_constraint_ok": False, "citations": []}
        )
        return ModelTurn(
            output=output,
            finish_reason=FinishReason.STOP,
            model_id="fixture-model",
            usage=ModelUsage(
                input_tokens=10,
                output_tokens=5,
                estimated_cost_microunits=7,
            ),
        )


def test_dataset_has_25_cases_split_evenly_across_agents() -> None:
    cases = load_benchmark_cases(ROOT / "benchmarks" / "model_cases.jsonl")
    assert len(cases) == 25
    agents = {case.agent_id for case in cases}
    assert {agent: sum(case.agent_id == agent for case in cases) for agent in agents} == {
        "coordinator": 5,
        "destination_intelligence": 5,
        "mobility_lodging": 5,
        "itinerary_planner": 5,
        "critic": 5,
    }


def test_report_is_separated_by_agent_and_candidate() -> None:
    async def scenario() -> None:
        cases = load_benchmark_cases(ROOT / "benchmarks" / "model_cases.jsonl")
        runner = ModelBenchmarkRunner(cases)
        report = await runner.run(
            {
                BenchmarkCandidate(
                    candidate_id="deepseek_flash", model_alias="deepseek-flash"
                ): BenchmarkFixtureGateway(valid=True),
                BenchmarkCandidate(
                    candidate_id="gpt_terra", model_alias="gpt-5.6-terra"
                ): BenchmarkFixtureGateway(valid=False),
            }
        )
        assert len(report.samples) == 50
        assert len(report.metrics) == 10
        valid_metrics = [item for item in report.metrics if item.candidate_id == "deepseek_flash"]
        assert all(item.schema_pass_rate == 1 for item in valid_metrics)
        assert all(item.hard_constraint_pass_rate == 1 for item in valid_metrics)
        assert all(item.citation_pass_rate == 1 for item in valid_metrics)
        assert all(item.average_success_cost_microunits == 7 for item in valid_metrics)
        markdown = report.render_markdown()
        assert "deepseek_flash" in markdown
        assert "gpt_terra" in markdown

    asyncio.run(scenario())
