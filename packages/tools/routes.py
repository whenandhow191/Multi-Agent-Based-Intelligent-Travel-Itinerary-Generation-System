"""Controlled Amap route batching, coordinate normalization, and map payloads."""

import hashlib
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import BaseModel, Field, JsonValue, SecretStr, model_validator

from packages.domain import (
    Coordinate,
    CoordinateSystem,
    Evidence,
    Freshness,
    Route,
    RouteMode,
    RouteQuality,
    StoragePolicy,
)
from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.harness import ToolContext, ToolDefinition
from packages.tools.amap import AmapProviderError
from packages.tools.http_provider import ResilientHttpProvider

AMAP_DIRECTION_URLS = {
    RouteMode.DRIVING: "https://restapi.amap.com/v5/direction/driving",
    RouteMode.WALKING: "https://restapi.amap.com/v5/direction/walking",
    RouteMode.CYCLING: "https://restapi.amap.com/v5/direction/bicycling",
    RouteMode.PUBLIC_TRANSIT: "https://restapi.amap.com/v5/direction/transit/integrated",
}


class RouteEndpoint(DomainModel):
    place_id: Identifier
    coordinate: Coordinate
    city_code: NonEmptyText | None = None
    provider_place_id: NonEmptyText | None = None


class RouteComputeInput(DomainModel):
    origin: RouteEndpoint
    destination: RouteEndpoint
    mode: RouteMode = RouteMode.WALKING

    @model_validator(mode="after")
    def endpoints_differ(self) -> "RouteComputeInput":
        if self.origin.place_id == self.destination.place_id:
            raise ValueError("route endpoints must differ")
        if self.mode is RouteMode.PUBLIC_TRANSIT and (
            not self.origin.city_code or not self.destination.city_code
        ):
            raise ValueError("public transit routes require both city codes")
        if self.mode is RouteMode.TAXI:
            raise ValueError("taxi uses the driving route plus a separate price estimate")
        return self


class MapPolyline(DomainModel):
    route_id: Identifier
    coordinate_system: CoordinateSystem = CoordinateSystem.GCJ02
    points: tuple[Coordinate, ...]


class RouteComputeOutput(DomainModel):
    provider_mode: Literal["live", "fixture"]
    route: Route
    polyline: MapPolyline
    evidence: Evidence


class RouteMatrixInput(DomainModel):
    endpoints: tuple[RouteEndpoint, ...] = Field(min_length=2, max_length=8)
    modes: tuple[RouteMode, ...] = Field(default=(RouteMode.WALKING,), min_length=1, max_length=2)
    max_pairs: int = Field(default=30, ge=1, le=56)

    @model_validator(mode="after")
    def unique_and_supported(self) -> "RouteMatrixInput":
        ids = [item.place_id for item in self.endpoints]
        if len(ids) != len(set(ids)):
            raise ValueError("route matrix endpoint IDs must be unique")
        if len(self.modes) != len(set(self.modes)):
            raise ValueError("route matrix modes must be unique")
        if RouteMode.TAXI in self.modes:
            raise ValueError("taxi is not a route matrix mode")
        return self


class RouteMatrixOutput(DomainModel):
    provider_mode: Literal["live", "fixture", "mixed"]
    routes: tuple[Route, ...]
    map_polylines: tuple[MapPolyline, ...]
    evidence: tuple[Evidence, ...]
    truncated: bool


class AmapRouteAdapter:
    """Compute normalized routes without assuming a provider-side NxN endpoint."""

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

    async def compute(self, request: RouteComputeInput) -> RouteComputeOutput:
        origin = to_gcj02(request.origin.coordinate)
        destination = to_gcj02(request.destination.coordinate)
        now = self.now or datetime.now(UTC)
        if not self.live:
            return _estimated_route(request, origin, destination, now)
        params: dict[str, str | int | float] = {
            "key": cast(SecretStr, self.api_key).get_secret_value(),
            "origin": _format_coordinate(origin),
            "destination": _format_coordinate(destination),
            "show_fields": "polyline,cost",
        }
        if request.origin.provider_place_id:
            params["origin_id"] = request.origin.provider_place_id
        if request.destination.provider_place_id:
            params["destination_id"] = request.destination.provider_place_id
        if request.mode is RouteMode.PUBLIC_TRANSIT:
            params["city1"] = cast(str, request.origin.city_code)
            params["city2"] = cast(str, request.destination.city_code)
        payload = await self.http.request_json(
            operation=f"route_{request.mode.value}",
            method="GET",
            url=AMAP_DIRECTION_URLS[request.mode],
            params=params,
        )
        return _normalize_live_route(payload, request, origin, destination, now)

    async def matrix(self, request: RouteMatrixInput) -> RouteMatrixOutput:
        pairs = [
            (origin, destination, mode)
            for mode in request.modes
            for origin in request.endpoints
            for destination in request.endpoints
            if origin.place_id != destination.place_id
        ]
        selected = pairs[: request.max_pairs]
        results = []
        for origin, destination, mode in selected:
            results.append(
                await self.compute(
                    RouteComputeInput(origin=origin, destination=destination, mode=mode)
                )
            )
        modes = {item.provider_mode for item in results}
        provider_mode: Literal["live", "fixture", "mixed"] = (
            next(iter(modes)) if len(modes) == 1 else "mixed"
        )
        return RouteMatrixOutput(
            provider_mode=provider_mode,
            routes=tuple(item.route for item in results),
            map_polylines=tuple(item.polyline for item in results),
            evidence=tuple(item.evidence for item in results),
            truncated=len(selected) < len(pairs),
        )

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        async def compute_handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            return await self.compute(RouteComputeInput.model_validate(arguments))

        async def matrix_handler(arguments: BaseModel, _: ToolContext) -> BaseModel:
            return await self.matrix(RouteMatrixInput.model_validate(arguments))

        return (
            ToolDefinition(
                name="routes.compute",
                description="Compute one bounded provider-neutral route and renderable polyline.",
                input_model=RouteComputeInput,
                output_model=RouteComputeOutput,
                handler=compute_handler,
            ),
            ToolDefinition(
                name="routes.matrix",
                description="Build a bounded directed route matrix for Top-K endpoints.",
                input_model=RouteMatrixInput,
                output_model=RouteMatrixOutput,
                handler=matrix_handler,
            ),
        )


def to_gcj02(coordinate: Coordinate) -> Coordinate:
    """Normalize WGS84/BD09 coordinates to the domestic Amap GCJ-02 system."""

    if coordinate.system is CoordinateSystem.GCJ02:
        return coordinate
    if coordinate.system is CoordinateSystem.BD09:
        x = coordinate.longitude - 0.0065
        y = coordinate.latitude - 0.006
        z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * math.pi * 3000 / 180)
        theta = math.atan2(y, x) - 0.000003 * math.cos(x * math.pi * 3000 / 180)
        return Coordinate(
            longitude=z * math.cos(theta),
            latitude=z * math.sin(theta),
            system=CoordinateSystem.GCJ02,
        )
    if _outside_china(coordinate.longitude, coordinate.latitude):
        return Coordinate(
            longitude=coordinate.longitude,
            latitude=coordinate.latitude,
            system=CoordinateSystem.GCJ02,
        )
    delta_lon, delta_lat = _gcj_delta(coordinate.longitude, coordinate.latitude)
    return Coordinate(
        longitude=coordinate.longitude + delta_lon,
        latitude=coordinate.latitude + delta_lat,
        system=CoordinateSystem.GCJ02,
    )


def _gcj_delta(longitude: float, latitude: float) -> tuple[float, float]:
    earth_radius = 6_378_245.0
    eccentricity = 0.006693421622965943
    x = longitude - 105.0
    y = latitude - 35.0
    d_lat = _transform_lat(x, y)
    d_lon = _transform_lon(x, y)
    rad_lat = latitude / 180.0 * math.pi
    magic = 1 - eccentricity * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = d_lat * 180 / ((earth_radius * (1 - eccentricity)) / (magic * sqrt_magic) * math.pi)
    d_lon = d_lon * 180 / (earth_radius / sqrt_magic * math.cos(rad_lat) * math.pi)
    return d_lon, d_lat


def _transform_lat(x: float, y: float) -> float:
    value = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y
    value += 0.2 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    value += (20 * math.sin(y * math.pi) + 40 * math.sin(y / 3 * math.pi)) * 2 / 3
    return value + (160 * math.sin(y / 12 * math.pi) + 320 * math.sin(y / 30 * math.pi)) * 2 / 3


def _transform_lon(x: float, y: float) -> float:
    value = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y
    value += 0.1 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    value += (20 * math.sin(x * math.pi) + 40 * math.sin(x / 3 * math.pi)) * 2 / 3
    return value + (150 * math.sin(x / 12 * math.pi) + 300 * math.sin(x / 30 * math.pi)) * 2 / 3


def _outside_china(longitude: float, latitude: float) -> bool:
    return not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271)


def _format_coordinate(value: Coordinate) -> str:
    return f"{value.longitude:.6f},{value.latitude:.6f}"


def _normalize_live_route(
    payload: JsonValue,
    request: RouteComputeInput,
    origin: Coordinate,
    destination: Coordinate,
    now: datetime,
) -> RouteComputeOutput:
    if not isinstance(payload, dict) or str(payload.get("status")) != "1":
        code = payload.get("infocode", "unknown") if isinstance(payload, dict) else "invalid_json"
        raise AmapProviderError(f"Amap route request failed with infocode {code}")
    route_body = payload.get("route")
    if not isinstance(route_body, dict):
        raise AmapProviderError("Amap route object is missing")
    choices = route_body.get("transits" if request.mode is RouteMode.PUBLIC_TRANSIT else "paths")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AmapProviderError("Amap returned no route candidates")
    choice = choices[0]
    distance = _positive_int(choice.get("distance"), fallback=_haversine(origin, destination))
    duration = _positive_int(
        choice.get("duration"), fallback=_estimated_seconds(distance, request.mode)
    )
    route_id = _route_id(request)
    evidence_id = f"evidence_{route_id}"
    points = _extract_polyline(choice)
    if len(points) < 2:
        points = (origin, destination)
    route = Route(
        route_id=route_id,
        provider="amap",
        origin_place_id=request.origin.place_id,
        destination_place_id=request.destination.place_id,
        mode=request.mode,
        distance_meters=distance,
        duration_minutes=max(1, math.ceil(duration / 60)),
        quality=RouteQuality.MEASURED,
        coordinate_system=CoordinateSystem.GCJ02,
        retrieved_at=now,
        evidence_ids=(evidence_id,),
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        source_name="Amap Route Planning 2.0",
        source_url_or_provider_id=route_id,
        provider="amap",
        retrieved_at=now,
        field_paths=("distance_meters", "duration_minutes", "map_polylines"),
        freshness=Freshness.LIVE,
        confidence=0.95,
        storage_policy=StoragePolicy.MEMORY_ONLY,
    )
    return RouteComputeOutput(
        provider_mode="live",
        route=route,
        polyline=MapPolyline(route_id=route_id, points=points),
        evidence=evidence,
    )


def _estimated_route(
    request: RouteComputeInput,
    origin: Coordinate,
    destination: Coordinate,
    now: datetime,
) -> RouteComputeOutput:
    distance = _haversine(origin, destination)
    route_id = _route_id(request)
    evidence_id = f"evidence_{route_id}"
    route = Route(
        route_id=route_id,
        provider="fixture",
        origin_place_id=request.origin.place_id,
        destination_place_id=request.destination.place_id,
        mode=request.mode,
        distance_meters=distance,
        duration_minutes=max(1, math.ceil(_estimated_seconds(distance, request.mode) / 60)),
        quality=RouteQuality.ESTIMATED,
        coordinate_system=CoordinateSystem.GCJ02,
        retrieved_at=now,
        evidence_ids=(evidence_id,),
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        source_name="Synthetic route estimate",
        source_url_or_provider_id=route_id,
        provider="fixture",
        retrieved_at=now,
        field_paths=("distance_meters", "duration_minutes"),
        freshness=Freshness.UNKNOWN,
        confidence=0.3,
        storage_policy=StoragePolicy.FULL_LICENSED,
    )
    return RouteComputeOutput(
        provider_mode="fixture",
        route=route,
        polyline=MapPolyline(route_id=route_id, points=(origin, destination)),
        evidence=evidence,
    )


def _route_id(request: RouteComputeInput) -> str:
    value = f"{request.origin.place_id}:{request.destination.place_id}:{request.mode.value}"
    return f"route_{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _positive_int(value: object, *, fallback: int) -> int:
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _extract_polyline(choice: Mapping[str, object]) -> tuple[Coordinate, ...]:
    raw_segments = choice.get("steps") or choice.get("segments") or []
    if not isinstance(raw_segments, list):
        return ()
    points: list[Coordinate] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        raw = segment.get("polyline")
        if not isinstance(raw, str):
            continue
        for pair in raw.split(";"):
            try:
                longitude, latitude = (float(item) for item in pair.split(",", 1))
                point = Coordinate(
                    longitude=longitude,
                    latitude=latitude,
                    system=CoordinateSystem.GCJ02,
                )
            except (ValueError, TypeError):
                continue
            if not points or point != points[-1]:
                points.append(point)
    return tuple(points)


def _haversine(origin: Coordinate, destination: Coordinate) -> int:
    radius = 6_371_000
    lat1, lat2 = math.radians(origin.latitude), math.radians(destination.latitude)
    d_lat = lat2 - lat1
    d_lon = math.radians(destination.longitude - origin.longitude)
    value = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return max(1, round(radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))))


def _estimated_seconds(distance_meters: int, mode: RouteMode) -> int:
    meters_per_second = {
        RouteMode.WALKING: 1.2,
        RouteMode.CYCLING: 4.0,
        RouteMode.PUBLIC_TRANSIT: 6.0,
        RouteMode.DRIVING: 8.0,
    }[mode]
    return max(60, round(distance_meters / meters_per_second))
