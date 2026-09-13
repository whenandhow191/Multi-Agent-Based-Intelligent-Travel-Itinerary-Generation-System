"""Optional MCP discovery and transports behind the local ToolGateway contract."""

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Protocol, cast

import httpx
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, JsonValue, create_model

from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.harness.tool_gateway import ToolContext, ToolDefinition, ToolHandler

MCP_PROTOCOL_VERSION = "2025-06-18"


class McpAdapterError(RuntimeError):
    """Raised for bounded, secret-free MCP protocol failures."""


class McpServerConfig(DomainModel):
    """Explicit transport, permission and resource limits for one MCP server."""

    server_name: Identifier
    transport: Literal["stdio", "streamable_http"]
    command: tuple[NonEmptyText, ...] = ()
    url: AnyHttpUrl | None = None
    allowed_tools: tuple[NonEmptyText, ...]
    name_mapping: dict[NonEmptyText, NonEmptyText] = Field(default_factory=dict)
    timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 20
    max_response_bytes: Annotated[int, Field(ge=1_024, le=10_000_000)] = 1_000_000

    def validate_transport(self) -> None:
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP requires a fixed command argv")
        if self.transport == "streamable_http" and self.url is None:
            raise ValueError("streamable HTTP MCP requires a URL")


class McpToolOutput(DomainModel):
    """Provider-neutral representation of one MCP tool result."""

    content: JsonValue
    is_error: bool = False


class McpTransport(Protocol):
    """Minimum JSON-RPC operations shared by stdio and HTTP transports."""

    async def request(
        self, method: str, params: Mapping[str, JsonValue]
    ) -> dict[str, JsonValue]: ...

    async def notify(self, method: str, params: Mapping[str, JsonValue]) -> None: ...

    async def close(self) -> None: ...


class StdioMcpTransport:
    """Persistent newline-delimited JSON-RPC stdio client using argv, never a shell."""

    def __init__(self, config: McpServerConfig) -> None:
        config.validate_transport()
        if config.transport != "stdio":
            raise ValueError("StdioMcpTransport requires stdio config")
        self.command = tuple(config.command)
        self.timeout_seconds = config.timeout_seconds
        self.max_response_bytes = config.max_response_bytes
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def _start(self) -> asyncio.subprocess.Process:
        if self._process is None:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        return self._process

    async def request(self, method: str, params: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        async with self._lock:
            request_id = self._next_id
            self._next_id += 1
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": dict(params),
                }
            )
            response = await self._read()
        if response.get("id") != request_id:
            raise McpAdapterError("MCP stdio response id does not match request")
        return _json_rpc_result(response)

    async def notify(self, method: str, params: Mapping[str, JsonValue]) -> None:
        async with self._lock:
            await self._write({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    async def _write(self, message: Mapping[str, Any]) -> None:
        process = await self._start()
        if process.stdin is None:
            raise McpAdapterError("MCP stdio input is unavailable")
        payload = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        process.stdin.write(payload)
        await process.stdin.drain()

    async def _read(self) -> dict[str, JsonValue]:
        process = await self._start()
        if process.stdout is None:
            raise McpAdapterError("MCP stdio output is unavailable")
        line = await asyncio.wait_for(process.stdout.readline(), timeout=self.timeout_seconds)
        if not line:
            raise McpAdapterError("MCP stdio server closed the stream")
        if len(line) > self.max_response_bytes:
            raise McpAdapterError("MCP stdio response exceeds configured limit")
        return _json_object(json.loads(line))

    async def close(self) -> None:
        if self._process is None:
            return
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=self.timeout_seconds)
        except TimeoutError:
            self._process.terminate()
            await self._process.wait()
        self._process = None


class StreamableHttpMcpTransport:
    """MCP Streamable HTTP client supporting JSON and SSE request responses."""

    def __init__(self, config: McpServerConfig, client: httpx.AsyncClient) -> None:
        config.validate_transport()
        if config.transport != "streamable_http" or config.url is None:
            raise ValueError("StreamableHttpMcpTransport requires streamable_http config")
        self.url = str(config.url)
        self.client = client
        self.timeout_seconds = config.timeout_seconds
        self.max_response_bytes = config.max_response_bytes
        self.session_id: str | None = None
        self.protocol_version = MCP_PROTOCOL_VERSION
        self._next_id = 1

    async def request(self, method: str, params: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        request_id = self._next_id
        self._next_id += 1
        response = await self._post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)},
            method,
        )
        result = _json_rpc_result(_response_message(response, request_id))
        if method == "initialize" and isinstance(result.get("protocolVersion"), str):
            self.protocol_version = cast(str, result["protocolVersion"])
        return result

    async def notify(self, method: str, params: Mapping[str, JsonValue]) -> None:
        response = await self._post(
            {"jsonrpc": "2.0", "method": method, "params": dict(params)}, method
        )
        if response.status_code != 202 and response.content:
            raise McpAdapterError("MCP notification was not accepted")

    async def _post(self, body: Mapping[str, Any], method: str) -> httpx.Response:
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        response = await self.client.post(
            self.url,
            json=body,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        if len(response.content) > self.max_response_bytes:
            raise McpAdapterError("MCP HTTP response exceeds configured limit")
        returned_session = response.headers.get("Mcp-Session-Id")
        if returned_session:
            self.session_id = returned_session
        return response

    async def close(self) -> None:
        if not self.session_id:
            return
        await self.client.delete(
            self.url,
            headers={
                "Mcp-Session-Id": self.session_id,
                "MCP-Protocol-Version": self.protocol_version,
            },
            timeout=self.timeout_seconds,
        )
        self.session_id = None


class McpAdapter:
    """Discover an allowlisted MCP snapshot and wrap it as local ToolDefinitions."""

    def __init__(self, config: McpServerConfig, transport: McpTransport) -> None:
        config.validate_transport()
        self.config = config
        self.transport = transport
        self.schema_snapshot: dict[str, dict[str, JsonValue]] = {}

    async def discover(self) -> tuple[ToolDefinition, ...]:
        initialized = await asyncio.wait_for(
            self.transport.request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "travel-harness", "version": "0.1.0"},
                },
            ),
            timeout=self.config.timeout_seconds,
        )
        if initialized.get("protocolVersion") != MCP_PROTOCOL_VERSION:
            raise McpAdapterError("MCP server selected an unsupported protocol version")
        await self.transport.notify("notifications/initialized", {})
        raw_tools: list[JsonValue] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, JsonValue] = {} if cursor is None else {"cursor": cursor}
            result = await asyncio.wait_for(
                self.transport.request("tools/list", params),
                timeout=self.config.timeout_seconds,
            )
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpAdapterError("MCP tools/list did not return a tools array")
            raw_tools.extend(page)
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        else:
            raise McpAdapterError("MCP tools/list exceeded the pagination limit")
        definitions: list[ToolDefinition] = []
        allowed = set(self.config.allowed_tools)
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict) or not isinstance(raw_tool.get("name"), str):
                continue
            remote_name = cast(str, raw_tool["name"])
            if remote_name not in allowed:
                continue
            input_schema = raw_tool.get("inputSchema")
            if not isinstance(input_schema, dict):
                raise McpAdapterError(f"MCP tool {remote_name} has no object inputSchema")
            schema = cast(dict[str, JsonValue], json.loads(json.dumps(input_schema)))
            local_name = self.config.name_mapping.get(remote_name) or _local_tool_name(
                self.config.server_name, remote_name
            )
            input_model = _model_from_schema(local_name, schema)
            description = raw_tool.get("description")
            definitions.append(
                ToolDefinition(
                    name=local_name,
                    description=(
                        description
                        if isinstance(description, str) and description.strip()
                        else f"Allowlisted MCP tool {remote_name}."
                    ),
                    input_model=input_model,
                    output_model=McpToolOutput,
                    handler=self._handler(remote_name, input_model),
                )
            )
            self.schema_snapshot[local_name] = schema
        return tuple(definitions)

    def _handler(self, remote_name: str, input_model: type[BaseModel]) -> ToolHandler:
        async def handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            parsed = input_model.model_validate(arguments)
            result = await asyncio.wait_for(
                self.transport.request(
                    "tools/call",
                    {
                        "name": remote_name,
                        "arguments": cast(
                            dict[str, JsonValue], parsed.model_dump(mode="json", exclude_none=True)
                        ),
                    },
                ),
                timeout=self.config.timeout_seconds,
            )
            return McpToolOutput(
                content=result.get("content"),
                is_error=result.get("isError") is True,
            )

        return handler


def _model_from_schema(local_name: str, schema: Mapping[str, JsonValue]) -> type[BaseModel]:
    if schema.get("type") != "object":
        raise McpAdapterError(f"MCP tool {local_name} inputSchema must be an object")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise McpAdapterError(f"MCP tool {local_name} inputSchema is malformed")
    required_names = {item for item in required if isinstance(item, str)}
    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, raw_field in properties.items():
        if not isinstance(field_name, str) or not isinstance(raw_field, dict):
            continue
        field_type = _schema_type(raw_field.get("type"))
        fields[field_name] = (field_type, ... if field_name in required_names else None)
    model_name = "Mcp" + "".join(part.title() for part in local_name.split(".")) + "Input"
    generated = create_model(  # type: ignore[call-overload]
        model_name, __config__=ConfigDict(extra="forbid"), **fields
    )
    return cast(type[BaseModel], generated)


def _schema_type(value: JsonValue | None) -> Any:
    if not isinstance(value, str):
        return JsonValue
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict[str, JsonValue],
        "array": list[JsonValue],
    }.get(value, JsonValue)


def _local_tool_name(server_name: str, remote_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", remote_name.lower()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"tool_{normalized or 'unnamed'}"
    return f"mcp.{server_name}_{normalized}"[:120]


def _json_object(value: Any) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise McpAdapterError("MCP response must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _json_rpc_result(response: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    if response.get("error") is not None:
        raise McpAdapterError("MCP server returned a JSON-RPC error")
    result = response.get("result")
    if not isinstance(result, dict):
        raise McpAdapterError("MCP JSON-RPC response has no object result")
    return result


def _response_message(response: httpx.Response, request_id: int) -> dict[str, JsonValue]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return _json_object(response.json())
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        message = _json_object(json.loads(line.removeprefix("data:").strip()))
        if message.get("id") == request_id:
            return message
    raise McpAdapterError("MCP SSE stream ended before the matching response")
