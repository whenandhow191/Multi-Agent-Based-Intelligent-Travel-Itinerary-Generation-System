"""C30 weather horizon, normalization, and fallback tests."""

import asyncio
from datetime import UTC, date, datetime

import httpx

from packages.domain import Coordinate, CoordinateSystem, Freshness
from packages.tools.http_provider import HttpProviderPolicy, ResilientHttpProvider
from packages.tools.weather import OpenMeteoAdapter, WeatherForecastInput

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
LOCATION = Coordinate(longitude=116.397, latitude=39.908, system=CoordinateSystem.WGS84)


def test_live_weather_is_normalized_with_validity_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "daily": {
                    "time": ["2026-09-14", "2026-09-15"],
                    "weather_code": [1, 61],
                    "temperature_2m_max": [26.0, 23.5],
                    "temperature_2m_min": [17.0, 15.0],
                    "precipitation_probability_max": [10, 80],
                }
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenMeteoAdapter(
                ResilientHttpProvider(
                    provider="open_meteo",
                    client=client,
                    policy=HttpProviderPolicy(requests_per_second=100),
                ),
                now=NOW,
            )
            result = await adapter.forecast(
                WeatherForecastInput(
                    location=LOCATION,
                    start_date=date(2026, 9, 14),
                    end_date=date(2026, 9, 15),
                )
            )
        assert result.provider_mode == "live"
        assert result.forecasts[1].condition == "rain"
        assert result.forecasts[1].precipitation_probability == 0.8
        assert result.evidence[0].freshness is Freshness.FRESH

    asyncio.run(scenario())


def test_far_future_date_returns_guidance_without_calling_provider() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, request=request, json={})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenMeteoAdapter(
                ResilientHttpProvider(provider="open_meteo", client=client), now=NOW
            )
            result = await adapter.forecast(
                WeatherForecastInput(
                    location=LOCATION,
                    start_date=date(2026, 12, 1),
                    end_date=date(2026, 12, 2),
                )
            )
        assert result.provider_mode == "climate_guidance"
        assert result.forecasts == ()
        assert result.warnings
        assert called is False

    asyncio.run(scenario())


def test_provider_failure_uses_clearly_marked_synthetic_fixture() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, json={"error": True})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenMeteoAdapter(
                ResilientHttpProvider(
                    provider="open_meteo",
                    client=client,
                    policy=HttpProviderPolicy(
                        max_attempts=1,
                        requests_per_second=100,
                    ),
                ),
                now=NOW,
            )
            result = await adapter.forecast(
                WeatherForecastInput(
                    location=LOCATION,
                    start_date=date(2026, 9, 14),
                    end_date=date(2026, 9, 14),
                )
            )
        assert result.provider_mode == "fixture"
        assert result.forecasts[0].provider == "fixture"
        assert "synthetic" in result.warnings[0].lower()

    asyncio.run(scenario())
