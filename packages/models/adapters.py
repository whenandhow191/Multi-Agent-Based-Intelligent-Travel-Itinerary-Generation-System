"""HTTP model adapters that normalize Responses, OpenAI and Anthropic APIs."""

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, cast

import httpx
from pydantic import JsonValue, SecretStr

from packages.harness.model_gateway import (
    FinishReason,
    MessageRole,
    ModelGateway,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
    ModelUsage,
)


class ProviderFailureKind(StrEnum):
    """Stable failure classes consumed by bounded routing policies."""

    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    QUOTA = "quota"
    AUTH = "auth"
    SCHEMA = "schema"
    SERVER = "server"
    TRANSPORT = "transport"


class ModelProviderError(RuntimeError):
    """Secret-free provider error with retry and escalation semantics."""

    def __init__(
        self,
        kind: ProviderFailureKind,
        provider: str,
        *,
        status_code: int | None = None,
        retryable: bool,
    ) -> None:
        super().__init__(f"{provider} model request failed: {kind.value}")
        self.kind = kind
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


class ResponsesProvider(ModelGateway):
    """Provider-neutral adapter for the stateless OpenAI Responses shape."""

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        model_id: str,
        client: httpx.AsyncClient,
        api_key: SecretStr | None,
        require_api_key: bool = True,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.client = client
        self.api_key = api_key
        self.require_api_key = require_api_key

    async def generate(self, request: ModelRequest) -> ModelTurn:
        response = await self._post("/responses", _responses_payload(request, self.model_id))
        return _parse_responses(response, self.provider_id, self.model_id, request)

    async def _post(self, path: str, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        headers = self._request_headers()
        try:
            response = await self.client.post(
                f"{self.base_url}{path}", json=payload, headers=headers
            )
            response.raise_for_status()
            return _json_object(response.json(), self.provider_id)
        except httpx.TimeoutException as exc:
            raise ModelProviderError(
                ProviderFailureKind.TIMEOUT, self.provider_id, retryable=True
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise _status_error(self.provider_id, exc.response.status_code) from exc
        except (httpx.TransportError, ValueError) as exc:
            raise ModelProviderError(
                ProviderFailureKind.TRANSPORT, self.provider_id, retryable=True
            ) from exc

    def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None and self.api_key.get_secret_value().strip():
            headers["Authorization"] = f"Bearer {self.api_key.get_secret_value()}"
        elif self.require_api_key:
            raise ModelProviderError(ProviderFailureKind.AUTH, self.provider_id, retryable=False)
        return headers


class OpenAIResponsesProvider(ResponsesProvider):
    """OpenAI Responses adapter with server-side key handling."""

    def __init__(self, *, model_id: str, client: httpx.AsyncClient, api_key: SecretStr) -> None:
        super().__init__(
            provider_id="openai",
            base_url="https://api.openai.com/v1",
            model_id=model_id,
            client=client,
            api_key=api_key,
        )


class OpenAICompatibleProvider(ResponsesProvider):
    """Chat Completions adapter for compatible cloud or self-hosted endpoints."""

    async def generate(self, request: ModelRequest) -> ModelTurn:
        payload = _chat_payload(request, self.model_id)
        response = await self._post("/chat/completions", payload)
        return _parse_chat(response, self.provider_id, self.model_id, request)


class AnthropicCompatibleProvider(ResponsesProvider):
    """Anthropic Messages adapter for relays such as ``@ai-sdk/anthropic``."""

    async def generate(self, request: ModelRequest) -> ModelTurn:
        response = await self._post("/messages", _anthropic_payload(request, self.model_id))
        return _parse_anthropic(response, self.provider_id, self.model_id, request)

    def _request_headers(self) -> dict[str, str]:
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            if self.require_api_key:
                raise ModelProviderError(
                    ProviderFailureKind.AUTH, self.provider_id, retryable=False
                )
            return {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        key = self.api_key.get_secret_value()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }


class OllamaProvider(OpenAICompatibleProvider):
    """Local Ollama adapter using its documented OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        model_id: str,
        client: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:11434/v1",
    ) -> None:
        super().__init__(
            provider_id="ollama",
            base_url=base_url,
            model_id=model_id,
            client=client,
            api_key=None,
            require_api_key=False,
        )


def _responses_payload(request: ModelRequest, model_id: str) -> dict[str, JsonValue]:
    tool_names = {_wire_tool_name(item.name): item.name for item in request.tools}
    payload: dict[str, JsonValue] = {
        "model": model_id,
        "input": [
            _response_message(item.role, item.content, item.tool_call_id)
            for item in request.messages
        ],
        "tools": [
            {
                "type": "function",
                "name": wire_name,
                "description": item.description,
                "parameters": item.input_schema,
            }
            for wire_name, item in zip(tool_names, request.tools, strict=True)
        ],
        "temperature": request.policy.temperature,
        "max_output_tokens": request.policy.max_output_tokens,
        "reasoning": {"effort": request.policy.reasoning_effort},
        "store": False,
    }
    if request.output_schema is not None:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": "agent_output",
                "schema": request.output_schema,
            }
        }
    return payload


def _chat_payload(request: ModelRequest, model_id: str) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "model": model_id,
        "messages": [
            _chat_message(item.role, item.content, item.tool_call_id) for item in request.messages
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": _wire_tool_name(item.name),
                    "description": item.description,
                    "parameters": item.input_schema,
                },
            }
            for item in request.tools
        ],
        "temperature": request.policy.temperature,
        "max_tokens": request.policy.max_output_tokens,
        "reasoning_effort": request.policy.reasoning_effort,
    }
    if request.output_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "agent_output", "schema": request.output_schema},
        }
    return payload


def _anthropic_payload(request: ModelRequest, model_id: str) -> dict[str, JsonValue]:
    system_parts: list[str] = []
    messages: list[JsonValue] = []
    for message in request.messages:
        if message.role is MessageRole.SYSTEM:
            system_parts.append(message.content)
            continue
        if message.role is MessageRole.TOOL:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id or "tool_call",
                            "content": message.content,
                        }
                    ],
                }
            )
            continue
        messages.append({"role": message.role.value, "content": message.content})

    if request.output_schema is not None:
        schema = json.dumps(request.output_schema, ensure_ascii=False, separators=(",", ":"))
        system_parts.append(
            "Return only one valid JSON object matching this JSON Schema. "
            f"Do not use Markdown fences. Schema: {schema}"
        )
    payload: dict[str, JsonValue] = {
        "model": model_id,
        "system": "\n\n".join(system_parts),
        "messages": messages,
        "tools": [
            {
                "name": _wire_tool_name(item.name),
                "description": item.description,
                "input_schema": item.input_schema,
            }
            for item in request.tools
        ],
        "temperature": request.policy.temperature,
        "max_tokens": request.policy.max_output_tokens,
    }
    return payload


def _response_message(role: MessageRole, content: str, call_id: str | None) -> dict[str, JsonValue]:
    if role is MessageRole.TOOL:
        return {"role": "user", "content": f"Tool result for {call_id}: {content}"}
    return {"role": role.value, "content": content}


def _chat_message(role: MessageRole, content: str, call_id: str | None) -> dict[str, JsonValue]:
    return _response_message(role, content, call_id)


def _parse_responses(
    body: dict[str, JsonValue], provider: str, configured_model: str, request: ModelRequest
) -> ModelTurn:
    output_items = body.get("output")
    if not isinstance(output_items, list):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    reverse_names = {_wire_tool_name(item.name): item.name for item in request.tools}
    calls: list[ModelToolCall] = []
    text_parts: list[str] = []
    for item in output_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            calls.append(_responses_tool_call(item, reverse_names, provider))
        elif item.get("type") == "message" and isinstance(item.get("content"), list):
            for content in cast(list[JsonValue], item["content"]):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    text_parts.append(cast(str, content["text"]))
    usage = _responses_usage(body.get("usage"))
    model_id = body.get("model") if isinstance(body.get("model"), str) else configured_model
    if calls:
        return ModelTurn(
            tool_calls=tuple(calls),
            usage=usage,
            finish_reason=FinishReason.TOOL_CALLS,
            model_id=cast(str, model_id),
        )
    output = _structured_output("\n".join(text_parts), provider)
    return ModelTurn(
        output=output,
        usage=usage,
        finish_reason=FinishReason.STOP,
        model_id=cast(str, model_id),
    )


def _parse_chat(
    body: dict[str, JsonValue], provider: str, configured_model: str, request: ModelRequest
) -> ModelTurn:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    reverse_names = {_wire_tool_name(item.name): item.name for item in request.tools}
    raw_calls = message.get("tool_calls")
    calls = []
    if isinstance(raw_calls, list):
        for raw_call in raw_calls:
            if isinstance(raw_call, dict):
                calls.append(_chat_tool_call(raw_call, reverse_names, provider))
    usage = _chat_usage(body.get("usage"))
    model_id = body.get("model") if isinstance(body.get("model"), str) else configured_model
    if calls:
        return ModelTurn(
            tool_calls=tuple(calls),
            usage=usage,
            finish_reason=FinishReason.TOOL_CALLS,
            model_id=cast(str, model_id),
        )
    content = message.get("content")
    if not isinstance(content, str):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    return ModelTurn(
        output=_structured_output(content, provider),
        usage=usage,
        finish_reason=FinishReason.STOP,
        model_id=cast(str, model_id),
    )


def _parse_anthropic(
    body: dict[str, JsonValue], provider: str, configured_model: str, request: ModelRequest
) -> ModelTurn:
    content = body.get("content")
    if not isinstance(content, list):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    reverse_names = {_wire_tool_name(item.name): item.name for item in request.tools}
    calls: list[ModelToolCall] = []
    text_parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(cast(str, block["text"]))
        elif block.get("type") == "tool_use":
            name = block.get("name")
            arguments = block.get("input")
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
            calls.append(
                ModelToolCall(
                    call_id=_safe_identifier(block.get("id"), "model_call"),
                    name=reverse_names.get(name, name),
                    arguments=arguments,
                )
            )
    usage = _anthropic_usage(body.get("usage"))
    model_id = body.get("model") if isinstance(body.get("model"), str) else configured_model
    if calls:
        return ModelTurn(
            tool_calls=tuple(calls),
            usage=usage,
            finish_reason=FinishReason.TOOL_CALLS,
            model_id=cast(str, model_id),
        )
    return ModelTurn(
        output=_structured_output("\n".join(text_parts), provider),
        usage=usage,
        finish_reason=FinishReason.STOP,
        model_id=cast(str, model_id),
    )


def _responses_tool_call(
    item: dict[str, JsonValue], reverse_names: dict[str, str], provider: str
) -> ModelToolCall:
    name = item.get("name")
    arguments = item.get("arguments")
    call_id = item.get("call_id")
    if not isinstance(name, str) or not isinstance(arguments, str):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    return ModelToolCall(
        call_id=_safe_identifier(call_id, "model_call"),
        name=reverse_names.get(name, name),
        arguments=_arguments(arguments, provider),
    )


def _chat_tool_call(
    item: dict[str, JsonValue], reverse_names: dict[str, str], provider: str
) -> ModelToolCall:
    function = item.get("function")
    if not isinstance(function, dict):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    name = function.get("name")
    arguments = function.get("arguments")
    if not isinstance(name, str) or not isinstance(arguments, str):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    return ModelToolCall(
        call_id=_safe_identifier(item.get("id"), "model_call"),
        name=reverse_names.get(name, name),
        arguments=_arguments(arguments, provider),
    )


def _arguments(value: str, provider: str) -> dict[str, JsonValue]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False) from exc
    if not isinstance(decoded, dict):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    return cast(dict[str, JsonValue], decoded)


def _structured_output(value: str, provider: str) -> dict[str, JsonValue]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False) from exc
    if not isinstance(decoded, dict):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    return cast(dict[str, JsonValue], decoded)


def _responses_usage(raw: JsonValue | None) -> ModelUsage:
    if not isinstance(raw, dict):
        return ModelUsage()
    input_details = raw.get("input_tokens_details")
    output_details = raw.get("output_tokens_details")
    return ModelUsage(
        input_tokens=_integer(raw.get("input_tokens")),
        output_tokens=_integer(raw.get("output_tokens")),
        cached_input_tokens=_nested_integer(input_details, "cached_tokens"),
        reasoning_tokens=_nested_integer(output_details, "reasoning_tokens"),
    )


def _chat_usage(raw: JsonValue | None) -> ModelUsage:
    if not isinstance(raw, dict):
        return ModelUsage()
    input_details = raw.get("prompt_tokens_details")
    output_details = raw.get("completion_tokens_details")
    return ModelUsage(
        input_tokens=_integer(raw.get("prompt_tokens")),
        output_tokens=_integer(raw.get("completion_tokens")),
        cached_input_tokens=_nested_integer(input_details, "cached_tokens"),
        reasoning_tokens=_nested_integer(output_details, "reasoning_tokens"),
    )


def _anthropic_usage(raw: JsonValue | None) -> ModelUsage:
    if not isinstance(raw, dict):
        return ModelUsage()
    return ModelUsage(
        input_tokens=_integer(raw.get("input_tokens")),
        output_tokens=_integer(raw.get("output_tokens")),
        cached_input_tokens=(
            _integer(raw.get("cache_read_input_tokens"))
            + _integer(raw.get("cache_creation_input_tokens"))
        ),
    )


def _nested_integer(raw: JsonValue | None, key: str) -> int:
    return _integer(raw.get(key)) if isinstance(raw, dict) else 0


def _integer(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _wire_tool_name(name: str) -> str:
    return name.replace(".", "__dot__")


def _safe_identifier(value: JsonValue | None, prefix: str) -> str:
    if isinstance(value, str):
        normalized = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
        if normalized and normalized[0].isalpha():
            return normalized[:120]
        digest = hashlib.sha256(value.encode()).hexdigest()[:16]
        return f"{prefix}_{digest}"
    return prefix


def _json_object(value: Any, provider: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ModelProviderError(ProviderFailureKind.SCHEMA, provider, retryable=False)
    return cast(dict[str, JsonValue], value)


def _status_error(provider: str, status_code: int) -> ModelProviderError:
    if status_code == 429:
        kind, retryable = ProviderFailureKind.RATE_LIMIT, True
    elif status_code in {402, 403}:
        kind, retryable = ProviderFailureKind.QUOTA, False
    elif status_code in {401}:
        kind, retryable = ProviderFailureKind.AUTH, False
    elif status_code >= 500:
        kind, retryable = ProviderFailureKind.SERVER, True
    else:
        kind, retryable = ProviderFailureKind.SCHEMA, False
    return ModelProviderError(kind, provider, status_code=status_code, retryable=retryable)
