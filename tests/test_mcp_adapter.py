"""Contract tests for optional MCP discovery and Streamable HTTP transport."""

import asyncio
from collections.abc import Mapping

import httpx
from pydantic import AnyHttpUrl, JsonValue

from packages.harness.tool_gateway import (
    ToolContext,
    ToolGateway,
    ToolInputValidationError,
    ToolRegistry,
)
from packages.tools.mcp_adapter import McpAdapter, McpServerConfig, StreamableHttpMcpTransport


class FakeMcpTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, JsonValue]]] = []

    async def request(self, method: str, params: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        self.calls.append((method, params))
        if method == "initialize":
            return {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "get-weather",
                        "description": "Get weather",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                    {"name": "admin-delete", "inputSchema": {"type": "object"}},
                ]
            }
        return {"content": [{"type": "text", "text": "sunny"}], "isError": False}

    async def notify(self, method: str, params: Mapping[str, JsonValue]) -> None:
        self.calls.append((method, params))

    async def close(self) -> None:
        return None


def _context() -> ToolContext:
    return ToolContext(
        run_id="test_run",
        task_id="test_task",
        agent_id="test_agent",
        trace_id="test_trace",
    )


def test_discovery_maps_names_filters_permissions_and_calls_through_gateway() -> None:
    transport = FakeMcpTransport()
    config = McpServerConfig(
        server_name="weather",
        transport="stdio",
        command=("fake-server",),
        allowed_tools=("get-weather",),
    )
    adapter = McpAdapter(config, transport)
    definitions = asyncio.run(adapter.discover())
    assert [item.name for item in definitions] == ["mcp.weather_get_weather"]
    assert set(adapter.schema_snapshot) == {"mcp.weather_get_weather"}

    gateway = ToolGateway(ToolRegistry(definitions))
    result = asyncio.run(
        gateway.execute(
            call_id="mcp_call",
            tool_name="mcp.weather_get_weather",
            arguments={"city": "Chengdu"},
            context=_context(),
            allowlist=("mcp.weather_get_weather",),
        )
    )
    assert result.data["is_error"] is False
    assert transport.calls[-1][0] == "tools/call"


def test_discovered_schema_rejects_missing_required_input() -> None:
    adapter = McpAdapter(
        McpServerConfig(
            server_name="weather",
            transport="stdio",
            command=("fake-server",),
            allowed_tools=("get-weather",),
        ),
        FakeMcpTransport(),
    )
    gateway = ToolGateway(ToolRegistry(asyncio.run(adapter.discover())))
    try:
        asyncio.run(
            gateway.execute(
                call_id="mcp_call",
                tool_name="mcp.weather_get_weather",
                arguments={},
                context=_context(),
                allowlist=("mcp.weather_get_weather",),
            )
        )
    except ToolInputValidationError:
        pass
    else:
        raise AssertionError("missing required MCP argument must be rejected")


def test_streamable_http_negotiates_session_and_accepts_sse() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/json", "Mcp-Session-Id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-06-18"},
                },
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n\n',
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = StreamableHttpMcpTransport(
                McpServerConfig(
                    server_name="remote",
                    transport="streamable_http",
                    url=AnyHttpUrl("https://example.test/mcp"),
                    allowed_tools=("lookup",),
                ),
                client,
            )
            await transport.request("initialize", {})
            result = await transport.request("tools/list", {})
            assert result == {"tools": []}

    asyncio.run(scenario())
    assert requests[1].headers["Mcp-Session-Id"] == "session-1"
    assert requests[1].headers["MCP-Protocol-Version"] == "2025-06-18"
