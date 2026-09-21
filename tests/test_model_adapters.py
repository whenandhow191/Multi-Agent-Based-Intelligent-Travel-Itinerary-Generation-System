"""Contract tests for cloud-compatible and local model adapters."""

import asyncio

import httpx
from pydantic import SecretStr

from packages.harness import (
    MessageRole,
    ModelMessage,
    ModelPolicy,
    ModelRequest,
    ModelToolSpec,
    TraceContext,
)
from packages.models import (
    AnthropicCompatibleProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenAIResponsesProvider,
)


def _request(*, tools: bool = False) -> ModelRequest:
    return ModelRequest(
        messages=(ModelMessage(role=MessageRole.USER, content="return JSON"),),
        tools=(
            (
                ModelToolSpec(
                    name="places.search",
                    description="Search places",
                    input_schema={"type": "object"},
                ),
            )
            if tools
            else ()
        ),
        output_schema={"type": "object", "required": ["ok"]},
        policy=ModelPolicy(model_alias="test", reasoning_effort="low"),
        trace=TraceContext(trace_id="trace_one", run_id="run_one", task_id="task_one"),
    )


def test_openai_responses_normalizes_structured_output_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        payload = request.read().decode()
        assert '"store":false' in payload
        return httpx.Response(
            200,
            json={
                "model": "gpt-5.6-terra",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "input_tokens_details": {"cached_tokens": 3},
                    "output_tokens_details": {"reasoning_tokens": 2},
                },
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = OpenAIResponsesProvider(
                model_id="gpt-5.6-terra", client=client, api_key=SecretStr("secret")
            )
            turn = await gateway.generate(_request())
            assert turn.output == {"ok": True}
            assert turn.usage.cached_input_tokens == 3
            assert turn.usage.reasoning_tokens == 2

    asyncio.run(scenario())


def test_responses_tool_names_round_trip_without_provider_leakage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "places__dot__search" in request.read().decode()
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call-123",
                        "name": "places__dot__search",
                        "arguments": '{"city":"成都"}',
                    }
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = OpenAICompatibleProvider(
                provider_id="deepseek",
                base_url="https://api.deepseek.com",
                model_id="deepseek-flash",
                client=client,
                api_key=SecretStr("secret"),
            )
            turn = await ResponsesProvider.generate(gateway, _request(tools=True))
            assert turn.tool_calls[0].name == "places.search"
            assert turn.model_id == "deepseek-flash"

    from packages.models import ResponsesProvider

    asyncio.run(scenario())


def test_ollama_uses_local_chat_compatibility_without_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert "Authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "model": "local-test",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = OllamaProvider(model_id="local-test", client=client)
            turn = await gateway.generate(_request())
            assert turn.output == {"ok": True}
            assert turn.usage.total_tokens == 7

    asyncio.run(scenario())


def test_anthropic_compatible_provider_uses_messages_protocol_and_shared_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "penguin-secret"
        assert request.headers["Authorization"] == "Bearer penguin-secret"
        payload = request.read().decode()
        assert '"model":"claude-sonnet-5"' in payload
        assert "Return only one valid JSON object" in payload
        return httpx.Response(
            200,
            json={
                "id": "msg_fixture",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": '{"ok":true}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 8, "output_tokens": 3},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = AnthropicCompatibleProvider(
                provider_id="penguin",
                base_url="https://relay.example/v1",
                model_id="claude-sonnet-5",
                client=client,
                api_key=SecretStr("penguin-secret"),
            )
            turn = await gateway.generate(_request())
            assert turn.output == {"ok": True}
            assert turn.usage.total_tokens == 11

    asyncio.run(scenario())
