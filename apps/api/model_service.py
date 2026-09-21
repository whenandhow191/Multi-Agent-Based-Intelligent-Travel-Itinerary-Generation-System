"""Penguin relay discovery and minimal generation probes without exposing its key."""

from __future__ import annotations

from typing import Literal, cast

import httpx
from pydantic import JsonValue

from apps.api.model_models import (
    ModelCatalogResponse,
    ModelOption,
    ModelProbeResponse,
    PenguinModelId,
)
from apps.api.settings import Settings
from packages.harness import MessageRole, ModelMessage, ModelPolicy, ModelRequest, TraceContext
from packages.models.adapters import (
    AnthropicCompatibleProvider,
    ModelProviderError,
    ProviderFailureKind,
)

PENGUIN_MODELS: tuple[PenguinModelId, ...] = (
    "claude-sonnet-5",
    "claude-opus-5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
)


class PenguinModelService:
    """Keep relay authentication server-side and expose an allowlisted model catalog."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def catalog(self) -> ModelCatalogResponse:
        key = self._key()
        if key is None:
            return self._catalog("unconfigured", set())
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{self.settings.penguin_base_url.rstrip('/')}/models",
                    headers=self._headers(key),
                )
                response.raise_for_status()
                remote_ids = _model_ids(response.json())
        except (httpx.HTTPError, ValueError):
            return self._catalog("unavailable", set())
        return self._catalog("verified", remote_ids)

    async def probe(self, model_id: PenguinModelId) -> ModelProbeResponse:
        key = self._key()
        if key is None:
            raise ModelProviderError(
                kind=ProviderFailureKind.AUTH, provider="penguin", retryable=False
            )
        async with httpx.AsyncClient(timeout=30) as client:
            gateway = AnthropicCompatibleProvider(
                provider_id="penguin",
                base_url=self.settings.penguin_base_url,
                model_id=model_id,
                client=client,
                api_key=self.settings.penguin_api_key,
            )
            turn = await gateway.generate(
                ModelRequest(
                    messages=(
                        ModelMessage(
                            role=MessageRole.USER,
                            content='Return exactly this JSON object: {"status":"ok"}',
                        ),
                    ),
                    output_schema={
                        "type": "object",
                        "properties": {"status": {"const": "ok"}},
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                    policy=ModelPolicy(
                        model_alias="penguin-connectivity-probe",
                        max_output_tokens=32,
                        timeout_seconds=30,
                    ),
                    trace=TraceContext(
                        trace_id="trace_penguin_probe",
                        run_id="run_penguin_probe",
                        task_id="task_penguin_probe",
                    ),
                )
            )
        if turn.output != {"status": "ok"}:
            raise ModelProviderError(
                kind=ProviderFailureKind.SCHEMA, provider="penguin", retryable=False
            )
        return ModelProbeResponse(
            model_id=model_id,
            input_tokens=turn.usage.input_tokens,
            output_tokens=turn.usage.output_tokens,
        )

    def _catalog(
        self,
        connection: LiteralConnection,
        remote_ids: set[str],
    ) -> ModelCatalogResponse:
        verified = connection == "verified"
        return ModelCatalogResponse(
            configured=self._key() is not None,
            connection=connection,
            models=tuple(
                ModelOption(
                    id=model_id,
                    name=model_id,
                    family="claude" if model_id.startswith("claude-") else "gpt",
                    available=verified and model_id in remote_ids,
                )
                for model_id in PENGUIN_MODELS
            ),
        )

    def _key(self) -> str | None:
        if self.settings.penguin_api_key is None:
            return None
        value = self.settings.penguin_api_key.get_secret_value().strip()
        return value or None

    @staticmethod
    def _headers(key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key}",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }


LiteralConnection = Literal["unconfigured", "verified", "unavailable"]


def _model_ids(value: object) -> set[str]:
    if not isinstance(value, dict):
        raise ValueError("model catalog must be an object")
    raw = value.get("data", value.get("models"))
    if not isinstance(raw, list):
        raise ValueError("model catalog has no model list")
    identifiers: set[str] = set()
    for item in cast(list[JsonValue], raw):
        if isinstance(item, str):
            identifiers.add(item)
        elif isinstance(item, dict):
            identifier = item.get("id", item.get("name"))
            if isinstance(identifier, str):
                identifiers.add(identifier)
    return identifiers
