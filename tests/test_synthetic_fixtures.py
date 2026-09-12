"""Offline determinism checks for synthetic fixtures and the scripted model."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from packages.evals import ScriptedModel, ScriptedTurn, ScriptExhaustedError
from packages.evals.fixtures import build_synthetic_scenario

SCRIPT_PATH = Path("fixtures/synthetic/scripted_turns.json")


def test_synthetic_scenario_is_byte_for_byte_deterministic() -> None:
    first = build_synthetic_scenario().model_dump_json()
    second = build_synthetic_scenario().model_dump_json()

    assert first == second
    assert hashlib.sha256(first.encode()).hexdigest() == hashlib.sha256(second.encode()).hexdigest()
    assert build_synthetic_scenario().final_bundle.plans[0].plan_id == "plan_balanced_fixture"


def test_scripted_model_returns_fixed_turns_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    turns = TypeAdapter(list[ScriptedTurn]).validate_json(SCRIPT_PATH.read_text(encoding="utf-8"))
    model = ScriptedModel(turns)

    first = asyncio.run(model.generate(messages=({"role": "user", "content": "fixture"},)))
    second = asyncio.run(
        model.generate(
            messages=({"role": "tool", "content": "fixed result"},),
            output_schema={"type": "object"},
        )
    )

    assert first.tool_calls[0].tool_name == "places.search"
    assert second.output == {"summary": "synthetic fixed response"}
    assert model.remaining_turns == 0
    assert model.calls[1].schema_requested is True
    with pytest.raises(ScriptExhaustedError):
        asyncio.run(model.generate(messages=()))
