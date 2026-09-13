"""C28 Amap geocoding and POI normalization tests."""

import asyncio
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr

from packages.domain import CoordinateSystem, CostKind
from packages.tools.amap import AmapAdapter, GeocodeInput, PlaceSearchInput
from packages.tools.http_provider import HttpProviderPolicy, ResilientHttpProvider

NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def _provider(handler: httpx.MockTransport) -> tuple[httpx.AsyncClient, ResilientHttpProvider]:
    client = httpx.AsyncClient(transport=handler)
    return client, ResilientHttpProvider(
        provider="amap",
        client=client,
        policy=HttpProviderPolicy(requests_per_second=100),
    )


def test_missing_key_uses_synthetic_fixture() -> None:
    async def scenario() -> None:
        client, provider = _provider(httpx.MockTransport(lambda _: httpx.Response(500)))
        async with client:
            adapter = AmapAdapter(provider, None, now=NOW)
            geocode = await adapter.geocode(GeocodeInput(address="天安门", city="北京"))
            places = await adapter.search_places(
                PlaceSearchInput(query="博物馆", city="北京", limit=2)
            )
        assert geocode.provider_mode == "fixture"
        assert geocode.candidates[0].coordinate.system is CoordinateSystem.GCJ02
        assert places.provider_mode == "fixture"
        assert all(place.provider == "amap" for place in places.places)

    asyncio.run(scenario())


def test_live_responses_are_normalized_and_key_is_not_in_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "test-key"
        if request.url.path.endswith("/geo"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": "1",
                    "geocodes": [
                        {
                            "formatted_address": "北京市东城区天安门",
                            "adcode": "110101",
                            "location": "116.397499,39.908722",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "1",
                "pois": [
                    {
                        "id": "B000A83M61",
                        "name": "示例博物馆",
                        "type": "科教文化服务;博物馆",
                        "address": "示例地址",
                        "location": "116.397026,39.918058",
                        "business": {"cost": "60"},
                    }
                ],
            },
        )

    async def scenario() -> None:
        client, provider = _provider(httpx.MockTransport(handler))
        async with client:
            adapter = AmapAdapter(provider, SecretStr("test-key"), now=NOW)
            geocode = await adapter.geocode(GeocodeInput(address="天安门", city="北京"))
            result = await adapter.search_places(
                PlaceSearchInput(query="博物馆", city="北京", limit=1)
            )
        assert geocode.provider_mode == "live"
        assert result.provider_mode == "live"
        assert result.places[0].price.kind is CostKind.KNOWN
        assert result.places[0].visit_duration_estimate.method.endswith("not_provider_fact")
        assert "test-key" not in result.model_dump_json()

    asyncio.run(scenario())
