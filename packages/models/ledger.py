"""Deterministic model/tool usage accounting and hierarchical budget gates."""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field

from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.harness import ModelGateway, ModelRequest, ModelTurn, ModelUsage


class ModelPrice(DomainModel):
    provider: Identifier
    input_per_million_microunits: Annotated[int, Field(ge=0)]
    cached_input_per_million_microunits: Annotated[int, Field(ge=0)]
    output_per_million_microunits: Annotated[int, Field(ge=0)]

    def estimate(self, usage: ModelUsage) -> int:
        cached = min(usage.cached_input_tokens, usage.input_tokens)
        uncached = usage.input_tokens - cached
        numerator = (
            uncached * self.input_per_million_microunits
            + cached * self.cached_input_per_million_microunits
            + usage.output_tokens * self.output_per_million_microunits
        )
        return (numerator + 999_999) // 1_000_000


class PriceBook(DomainModel):
    currency: Literal["USD"] = "USD"
    models: dict[NonEmptyText, ModelPrice]
    tools: dict[NonEmptyText, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> "PriceBook":
        with path.open("r", encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
        return cls.model_validate(value)


class BudgetLimits(DomainModel):
    run_microunits: Annotated[int, Field(ge=0)] | None = None
    agent_microunits: Annotated[int, Field(ge=0)] | None = None
    day_microunits: Annotated[int, Field(ge=0)] | None = None


class UsageEntry(DomainModel):
    entry_id: Identifier
    run_id: Identifier
    agent_id: Identifier
    provider_id: Identifier
    model_id: NonEmptyText
    usage: ModelUsage = ModelUsage()
    tool_name: NonEmptyText | None = None
    model_cost_microunits: Annotated[int, Field(ge=0)] = 0
    tool_cost_microunits: Annotated[int, Field(ge=0)] = 0
    total_cost_microunits: Annotated[int, Field(ge=0)] = 0
    created_at: datetime


class BudgetDecision(DomainModel):
    status: Literal["within", "partial"]
    exceeded_scopes: tuple[Literal["run", "agent", "day"], ...] = ()
    run_total_microunits: int
    agent_total_microunits: int
    day_total_microunits: int


class UsageLedger:
    """Lock-protected append-only ledger suitable for local runs and tests."""

    def __init__(self, price_book: PriceBook, limits: BudgetLimits | None = None) -> None:
        self.price_book = price_book
        self.limits = limits or BudgetLimits()
        self.entries: list[UsageEntry] = []
        self._lock = asyncio.Lock()

    async def record_model(
        self,
        *,
        entry_id: str,
        run_id: str,
        agent_id: str,
        provider_id: str,
        model_id: str,
        usage: ModelUsage,
        created_at: datetime | None = None,
    ) -> tuple[UsageEntry, BudgetDecision]:
        try:
            price = self.price_book.models[model_id]
        except KeyError as exc:
            raise KeyError(f"model price is not configured: {model_id}") from exc
        model_cost = price.estimate(usage)
        entry = UsageEntry(
            entry_id=entry_id,
            run_id=run_id,
            agent_id=agent_id,
            provider_id=provider_id,
            model_id=model_id,
            usage=usage.model_copy(update={"estimated_cost_microunits": model_cost}),
            model_cost_microunits=model_cost,
            total_cost_microunits=model_cost,
            created_at=created_at or datetime.now(UTC),
        )
        return await self._append(entry)

    async def record_tool(
        self,
        *,
        entry_id: str,
        run_id: str,
        agent_id: str,
        tool_name: str,
        created_at: datetime | None = None,
    ) -> tuple[UsageEntry, BudgetDecision]:
        fee = self.price_book.tools.get(tool_name, 0)
        entry = UsageEntry(
            entry_id=entry_id,
            run_id=run_id,
            agent_id=agent_id,
            provider_id="tool_gateway",
            model_id="none",
            tool_name=tool_name,
            tool_cost_microunits=fee,
            total_cost_microunits=fee,
            created_at=created_at or datetime.now(UTC),
        )
        return await self._append(entry)

    async def _append(self, entry: UsageEntry) -> tuple[UsageEntry, BudgetDecision]:
        async with self._lock:
            if any(item.entry_id == entry.entry_id for item in self.entries):
                raise ValueError(f"usage entry already exists: {entry.entry_id}")
            self.entries.append(entry)
            decision = self._decision(entry.run_id, entry.agent_id, entry.created_at.date())
            return entry, decision

    def _decision(self, run_id: str, agent_id: str, day: date) -> BudgetDecision:
        run_total = sum(
            item.total_cost_microunits for item in self.entries if item.run_id == run_id
        )
        agent_total = sum(
            item.total_cost_microunits
            for item in self.entries
            if item.run_id == run_id and item.agent_id == agent_id
        )
        day_total = sum(
            item.total_cost_microunits for item in self.entries if item.created_at.date() == day
        )
        exceeded: list[Literal["run", "agent", "day"]] = []
        if self.limits.run_microunits is not None and run_total > self.limits.run_microunits:
            exceeded.append("run")
        if self.limits.agent_microunits is not None and agent_total > self.limits.agent_microunits:
            exceeded.append("agent")
        if self.limits.day_microunits is not None and day_total > self.limits.day_microunits:
            exceeded.append("day")
        return BudgetDecision(
            status="partial" if exceeded else "within",
            exceeded_scopes=tuple(exceeded),
            run_total_microunits=run_total,
            agent_total_microunits=agent_total,
            day_total_microunits=day_total,
        )


class BudgetedModelGateway(ModelGateway):
    """Attach model cost and explicit partial state to every normalized turn."""

    def __init__(
        self,
        *,
        provider_id: str,
        agent_id: str,
        gateway: ModelGateway,
        ledger: UsageLedger,
    ) -> None:
        self.provider_id = provider_id
        self.agent_id = agent_id
        self.gateway = gateway
        self.ledger = ledger
        self._sequence = 0

    async def generate(self, request: ModelRequest) -> ModelTurn:
        turn = await self.gateway.generate(request)
        self._sequence += 1
        _, decision = await self.ledger.record_model(
            entry_id=f"usage_{request.trace.run_id}_{self.agent_id}_{self._sequence}",
            run_id=request.trace.run_id,
            agent_id=self.agent_id,
            provider_id=self.provider_id,
            model_id=turn.model_id,
            usage=turn.usage,
        )
        cost = self.ledger.entries[-1].model_cost_microunits
        return turn.model_copy(
            update={
                "usage": turn.usage.model_copy(update={"estimated_cost_microunits": cost}),
                "budget_status": decision.status,
                "budget_exceeded_scopes": decision.exceeded_scopes,
            }
        )
