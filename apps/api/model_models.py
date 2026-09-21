"""Public contracts for server-side model discovery and connectivity checks."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

PenguinModelId = Literal[
    "claude-sonnet-5",
    "claude-opus-5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
]


class ModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: PenguinModelId
    name: str
    family: Literal["claude", "gpt"]
    available: bool


class ModelCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Literal["penguin"] = "penguin"
    configured: bool
    connection: Literal["unconfigured", "verified", "unavailable"]
    models: tuple[ModelOption, ...]


class ModelProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: PenguinModelId


class ModelProbeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Literal["penguin"] = "penguin"
    model_id: PenguinModelId
    status: Literal["ok"] = "ok"
    input_tokens: int = 0
    output_tokens: int = 0
