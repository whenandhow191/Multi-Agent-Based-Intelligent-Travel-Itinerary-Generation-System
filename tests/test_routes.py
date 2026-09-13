"""C29 route matrix, map payload, and coordinate-system tests."""

import asyncio
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr

from packages.domain import Coordinate, CoordinateSystem, RouteMode, RouteQuality
from packages.tools.http_provider import HttpProviderPolicy, ResilientHttpProvider
from packages.tools.routes import (
    AmapRouteAdapter,
    RouteComputeInput,
    RouteEndpoint,
    RouteMatrixInput,
    to_gcj02,
)

NOW = datetime(2026, 9, 13, 11, 0, tzinfo=UTC)


def endpoint(name: str, lon: float, lat: float) -> RouteEndpoint:
    return RouteEndpoint(
        place_id=name,
        coordinate=Coordinate(longitude=lon, latitude=lat, system=CoordinateSystem.GCJ02),
    )


def test_fixture_matrix_is_bounded_and_renderable() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = AmapRouteAdapter(
                ResilientHttpProvider(
                    provider="amap",
                    client=client,
                    policy=HttpProviderPolicy(requests_per_second=100),
                ),
                None,
                now=NOW,
            )
            result = await adapter.matrix(
                RouteMatrixInput(
                    endpoints=(
                        endpoint("place_one", 116.397, 39.908),
                        endpoint("place_two", 116.407, 39.918),
                        endpoint("place_three", 116.417, 39.928),
                    ),
                    modes=(RouteMode.WALKING,),
                    max_pairs=4,
                )
            )
        assert len(result.routes) == len(result.map_polylines) == 4
        assert result.truncated is True
        assert all(item.quality is RouteQuality.ESTIMATED for item in result.routes)
        assert all(len(item.points) == 2 for item in result.map_polylines)

    asyncio.run(scenario())


def test_live_route_normalizes_provider_result_and_polyline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "1",
                "route": {
                    "paths": [
                        {
                            "distance": "1200",
                            "duration": "900",
                            "steps": [
                                {"polyline": "116.397000,39.908000;116.407000,39.918000"}
                            ],
                        }
                    ]
                },
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = AmapRouteAdapter(
                ResilientHttpProvider(
                    provider="amap",
                    client=client,
                    policy=HttpProviderPolicy(requests_per_second=100),
                ),
                SecretStr("test-key"),
                now=NOW,
            )
            result = await adapter.compute(
                RouteComputeInput(
                    origin=endpoint("place_one", 116.397, 39.908),
                    destination=endpoint("place_two", 116.407, 39.918),
                    mode=RouteMode.WALKING,
                )
            )
        assert result.route.distance_meters == 1200
        assert result.route.duration_minutes == 15
        assert result.route.quality is RouteQuality.MEASURED
        assert len(result.polyline.points) == 2

    asyncio.run(scenario())


def test_wgs84_and_bd09_are_not_silently_mixed_with_gcj02() -> None:
    wgs = Coordinate(longitude=116.397, latitude=39.908, system=CoordinateSystem.WGS84)
    bd = Coordinate(longitude=116.410, latitude=39.920, system=CoordinateSystem.BD09)

    converted_wgs = to_gcj02(wgs)
    converted_bd = to_gcj02(bd)

    assert converted_wgs.system is CoordinateSystem.GCJ02
    assert converted_bd.system is CoordinateSystem.GCJ02
    assert abs(converted_wgs.longitude - wgs.longitude) > 0.001
    assert abs(converted_bd.longitude - bd.longitude) > 0.001
