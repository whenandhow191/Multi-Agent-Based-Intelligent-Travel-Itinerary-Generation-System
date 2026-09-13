"""Tests for deterministic model/tool costs and hierarchical budget gates."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from packages.evals import ScriptedModelGateway
from packages.harness import FinishReason, ModelTurn, ModelUsage
from packages.models import BudgetedModelGateway, BudgetLimits, PriceBook, UsageLedger
from tests.test_model_gateway import build_request

ROOT = Path(__file__).parents[1]


def test_deepseek_cost_distinguishes_cached_input_without_double_counting_reasoning() -> None:
    price = PriceBook.from_yaml(ROOT / "config" / "pricing.yaml").models["deepseek-flash"]
    usage = ModelUsage(
        input_tokens=1_000_000,
        cached_input_tokens=500_000,
        output_tokens=1_000_000,
        reasoning_tokens=400_000,
    )

    assert price.estimate(usage) == 351_400


def test_run_agent_and_day_budget_returns_explicit_partial() -> None:
    async def scenario() -> None:
        ledger = UsageLedger(
            PriceBook.from_yaml(ROOT / "config" / "pricing.yaml"),
            BudgetLimits(run_microunits=1, agent_microunits=1, day_microunits=1),
        )
        _, decision = await ledger.record_model(
            entry_id="usage_one",
            run_id="run_one",
            agent_id="critic",
            provider_id="deepseek",
            model_id="deepseek-flash",
            usage=ModelUsage(input_tokens=100, output_tokens=100),
            created_at=datetime(2026, 9, 13, tzinfo=UTC),
        )
        assert decision.status == "partial"
        assert decision.exceeded_scopes == ("run", "agent", "day")

    asyncio.run(scenario())


def test_budgeted_gateway_adds_cost_and_partial_to_model_turn() -> None:
    async def scenario() -> None:
        source = ScriptedModelGateway(
            (
                ModelTurn(
                    output={"message": "ok"},
                    finish_reason=FinishReason.STOP,
                    model_id="deepseek-flash",
                    usage=ModelUsage(input_tokens=100, output_tokens=100),
                ),
            )
        )
        ledger = UsageLedger(
            PriceBook.from_yaml(ROOT / "config" / "pricing.yaml"),
            BudgetLimits(run_microunits=1),
        )
        gateway = BudgetedModelGateway(
            provider_id="deepseek", agent_id="critic", gateway=source, ledger=ledger
        )
        turn = await gateway.generate(build_request())
        assert turn.budget_status == "partial"
        assert turn.budget_exceeded_scopes == ("run",)
        assert turn.usage.estimated_cost_microunits > 0

    asyncio.run(scenario())


def test_tool_fees_share_the_same_ledger() -> None:
    async def scenario() -> None:
        price_book = PriceBook.from_yaml(ROOT / "config" / "pricing.yaml")
        price_book = price_book.model_copy(update={"tools": {"paid.lookup": 25}})
        ledger = UsageLedger(price_book)
        entry, decision = await ledger.record_tool(
            entry_id="tool_one",
            run_id="run_one",
            agent_id="destination_intelligence",
            tool_name="paid.lookup",
        )
        assert entry.tool_cost_microunits == 25
        assert decision.run_total_microunits == 25

    asyncio.run(scenario())
