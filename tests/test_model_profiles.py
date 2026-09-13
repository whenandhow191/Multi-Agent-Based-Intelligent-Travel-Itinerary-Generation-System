"""Tests for provider catalogs and per-Agent model selection."""

import asyncio
import json
from pathlib import Path

import httpx

from packages.harness import MessageRole, ModelMessage, ModelPolicy, ModelRequest, TraceContext
from packages.models import load_default_profiles

ROOT = Path(__file__).parents[1]


def _request() -> ModelRequest:
    return ModelRequest(
        messages=(ModelMessage(role=MessageRole.USER, content="return JSON"),),
        output_schema={"type": "object"},
        policy=ModelPolicy(model_alias="destination-intelligence", max_output_tokens=99),
        trace=TraceContext(trace_id="trace_one", run_id="run_one", task_id="task_one"),
    )


def test_default_profiles_use_deepseek_flash_and_advanced_profiles_use_gpt() -> None:
    registry = load_default_profiles(ROOT)
    for agent_id in registry.agents.agents:
        default = registry.resolve(agent_id)
        assert default.provider_id == "deepseek"
        assert default.model_id == "deepseek-flash"
        advanced = registry.resolve(agent_id, "advanced")
        assert advanced.provider_id == "openai"
        assert advanced.model_id.startswith("gpt-")


def test_profiled_gateway_applies_yaml_controls_without_agent_code_change() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = load_default_profiles(ROOT).build_gateway(
                "destination_intelligence", client, {"DEEPSEEK_API_KEY": "test-key"}
            )
            await gateway.generate(_request())

    asyncio.run(scenario())
    assert captured["model"] == "deepseek-flash"
    assert captured["max_output_tokens"] == 2400
    assert captured["reasoning"] == {"effort": "low"}
