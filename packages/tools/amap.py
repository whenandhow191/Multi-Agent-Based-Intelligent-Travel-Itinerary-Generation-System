"""Amap Web Service adapters with provider-neutral outputs and offline fixtures."""

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, JsonValue, SecretStr

from packages.domain import (
    Coordinate,
    CoordinateSystem,
    CostEstimate,
    CostKind,
    Evidence,
    Freshness,
    Place,
    StoragePolicy,
    VisitDurationEstimate,
)
from packages.domain.common import DomainModel, NonEmptyText, ShortText
from packages.harness import ToolContext, ToolDefinition
from packages.tools.http_provider import ProviderHttpError, ResilientHttpProvider

AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
AMAP_PLACE_TEXT_URL = "https://restapi.amap.com/v5/place/text"


class AmapProviderError(ProviderHttpError):
    """Amap returned a valid HTTP response with an unsuccessful API status."""

    code = "amap_provider_error"


class GeocodeInput(DomainModel):
    address: NonEmptyText
    city: ShortText | None = None


class GeocodeCandidate(DomainModel):
    formatted_address: NonEmptyText
    coordinate: Coordinate
    provider_place_id: NonEmptyText
    evidence: Evidence


class GeocodeOutput(DomainModel):
    provider_mode: Literal["live", "fixture"]
    candidates: tuple[GeocodeCandidate, ...]


class PlaceSearchInput(DomainModel):
    query: ShortText
    city: ShortText
    limit: int = Field(default=10, ge=1, le=20)


class PlaceSearchOutput(DomainModel):
    provider_mode: Literal["live", "fixture"]
    places: tuple[Place, ...]
    evidence: tuple[Evidence, ...]


class AmapAdapter:
    """Normalize Amap responses without allowing provider JSON above the tool layer."""

    def __init__(
        self,
        http: ResilientHttpProvider,
        api_key: SecretStr | None,
        *,
        now: datetime | None = None,
    ) -> None:
        self.http = http
        self.api_key = api_key
        self.now = now

    @property
    def live(self) -> bool:
        return self.api_key is not None and bool(self.api_key.get_secret_value().strip())

    async def geocode(self, request: GeocodeInput) -> GeocodeOutput:
        if not self.live:
            return _fixture_geocode(request, self._now())
        payload = await self.http.request_json(
            operation="geocode",
            method="GET",
            url=AMAP_GEOCODE_URL,
            params={
                "key": cast(SecretStr, self.api_key).get_secret_value(),
                "address": request.address,
                **({"city": request.city} if request.city else {}),
                "output": "JSON",
            },
        )
        body = _successful_mapping(payload)
        geocodes = _mapping_list(body.get("geocodes"))
        candidates = tuple(
            _geocode_candidate(item, self._now(), index) for index, item in enumerate(geocodes)
        )
        return GeocodeOutput(provider_mode="live", candidates=candidates)

    async def search_places(self, request: PlaceSearchInput) -> PlaceSearchOutput:
        if not self.live:
            return _fixture_places(request, self._now())
        payload = await self.http.request_json(
            operation="place_search",
            method="GET",
            url=AMAP_PLACE_TEXT_URL,
            params={
                "key": cast(SecretStr, self.api_key).get_secret_value(),
                "keywords": request.query,
                "region": request.city,
                "city_limit": "true",
                "page_size": request.limit,
                "page_num": 1,
                "show_fields": "business",
            },
        )
        body = _successful_mapping(payload)
        now = self._now()
        normalized = tuple(
            _place(item, now, index) for index, item in enumerate(_mapping_list(body.get("pois")))
        )
        places = normalized[: request.limit]
        evidence = tuple(item[1] for item in places)
        return PlaceSearchOutput(
            provider_mode="live",
            places=tuple(item[0] for item in places),
            evidence=evidence,
        )

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        async def geocode_handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            return await self.geocode(GeocodeInput.model_validate(arguments))

        async def search_handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            return await self.search_places(PlaceSearchInput.model_validate(arguments))

        return (
            ToolDefinition(
                name="maps.geocode",
                description="Resolve a structured address to provider-neutral GCJ-02 coordinates.",
                input_model=GeocodeInput,
                output_model=GeocodeOutput,
                handler=geocode_handler,
            ),
            ToolDefinition(
                name="places.search",
                description="Search and normalize POI candidates in one Chinese city.",
                input_model=PlaceSearchInput,
                output_model=PlaceSearchOutput,
                handler=search_handler,
            ),
        )

    def _now(self) -> datetime:
        return self.now or datetime.now(UTC)


def _successful_mapping(payload: JsonValue) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise AmapProviderError("Amap response must be a JSON object")
    body = cast(dict[str, Any], payload)
    if str(body.get("status")) != "1":
        code = str(body.get("infocode", "unknown"))
        raise AmapProviderError(f"Amap rejected request with infocode {code}")
    return body


def _mapping_list(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(cast(Mapping[str, Any], item) for item in value if isinstance(item, dict))


def _coordinate(value: object) -> Coordinate:
    if not isinstance(value, str):
        raise AmapProviderError("Amap coordinate is missing")
    try:
        longitude, latitude = (float(part) for part in value.split(",", 1))
    except (ValueError, TypeError) as exc:
        raise AmapProviderError("Amap coordinate is invalid") from exc
    return Coordinate(longitude=longitude, latitude=latitude, system=CoordinateSystem.GCJ02)


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _geocode_candidate(item: Mapping[str, Any], now: datetime, index: int) -> GeocodeCandidate:
    formatted = str(item.get("formatted_address") or item.get("address") or "unknown address")
    provider_id = str(item.get("adcode") or f"geocode_{index}")
    evidence_id = _stable_id("evidence_amap_geocode", f"{provider_id}:{formatted}:{index}")
    return GeocodeCandidate(
        formatted_address=formatted,
        coordinate=_coordinate(item.get("location")),
        provider_place_id=provider_id,
        evidence=Evidence(
            evidence_id=evidence_id,
            source_name="Amap Web Service geocoding",
            source_url_or_provider_id=provider_id,
            provider="amap",
            retrieved_at=now,
            field_paths=("formatted_address", "coordinate"),
            freshness=Freshness.FRESH,
            confidence=0.9,
            storage_policy=StoragePolicy.IDS_AND_METADATA,
        ),
    )


def _place(item: Mapping[str, Any], now: datetime, index: int) -> tuple[Place, Evidence]:
    provider_id = str(item.get("id") or f"poi_{index}")
    name = str(item.get("name") or "Unknown POI")
    evidence_id = _stable_id("evidence_amap_poi", provider_id)
    coordinate = _coordinate(item.get("location"))
    business = item.get("business") if isinstance(item.get("business"), dict) else {}
    cost = _cost_estimate(cast(Mapping[str, Any], business).get("cost"))
    category = str(item.get("type") or "unknown").split(";")[0]
    place = Place(
        place_id=_stable_id("place", provider_id),
        provider="amap",
        provider_place_id=provider_id,
        name=name,
        category=category,
        address=_optional_text(item.get("address")),
        raw_coordinate=coordinate,
        canonical_coordinate=coordinate,
        opening_hours=None,
        price=cost,
        visit_duration_estimate=VisitDurationEstimate(
            minimum_minutes=60,
            maximum_minutes=120,
            method="category_default_not_provider_fact",
            confidence=0.35,
        ),
        evidence_ids=(evidence_id,),
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        source_name="Amap POI 2.0",
        source_url_or_provider_id=provider_id,
        provider="amap",
        retrieved_at=now,
        field_paths=("name", "category", "address", "raw_coordinate", "price"),
        freshness=Freshness.FRESH,
        confidence=0.9,
        storage_policy=StoragePolicy.IDS_AND_METADATA,
    )
    return place, evidence


def _cost_estimate(value: object) -> CostEstimate:
    if value in (None, "", []):
        return CostEstimate(kind=CostKind.UNKNOWN, basis="Amap did not provide reference cost")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return CostEstimate(kind=CostKind.UNKNOWN, basis="Amap cost was not numeric")
    return CostEstimate(
        kind=CostKind.KNOWN,
        lower=amount,
        upper=amount,
        basis="Amap POI reference consumption; not live inventory",
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _fixture_geocode(request: GeocodeInput, now: datetime) -> GeocodeOutput:
    candidate = _geocode_candidate(
        {
            "formatted_address": f"{request.city or '北京市'}{request.address}",
            "adcode": "110101",
            "location": "116.397499,39.908722",
        },
        now,
        0,
    )
    return GeocodeOutput(provider_mode="fixture", candidates=(candidate,))


def _fixture_places(request: PlaceSearchInput, now: datetime) -> PlaceSearchOutput:
    samples = (
        {
            "id": "synthetic_forbidden_city",
            "name": "合成故宫博物院",
            "type": "风景名胜;博物馆",
            "address": "仅用于离线测试的合成地址",
            "location": "116.397026,39.918058",
            "business": {},
        },
        {
            "id": "synthetic_temple_of_heaven",
            "name": "合成天坛公园",
            "type": "风景名胜;公园",
            "address": "仅用于离线测试的合成地址",
            "location": "116.417312,39.887977",
            "business": {},
        },
    )[: request.limit]
    normalized = tuple(_place(item, now, index) for index, item in enumerate(samples))
    return PlaceSearchOutput(
        provider_mode="fixture",
        places=tuple(item[0] for item in normalized),
        evidence=tuple(item[1] for item in normalized),
    )
