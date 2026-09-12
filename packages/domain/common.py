"""Shared Pydantic behavior and constrained scalar types for domain contracts."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class DomainModel(BaseModel):
    """Base class that rejects unknown fields and keeps validated values immutable."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


NonEmptyText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=120)]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", min_length=3, max_length=120),
]
SchemaVersion = Annotated[str, StringConstraints(pattern=r"^[1-9]\d*\.\d+$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


def ensure_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    """Reject duplicate string values while preserving user-provided order."""

    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values
