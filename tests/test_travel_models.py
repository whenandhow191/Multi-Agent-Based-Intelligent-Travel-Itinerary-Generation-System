"""Tests for C08 canonical travel data models."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TypedDict

import pytest
from pydantic import ValidationError

from packages.domain import (
    Coordinate,
    CoordinateSystem,
    CostEstimate,
    CostKind,
    Place,
    VisitDurationEstimate,
    WeatherForecast,
)


class ProviderPlacePayload(TypedDict):
    """Small stand-in for values extracted by a provider adapter."""

    provider: str
    provider_id: str
    longitude: float
    latitude: float
    coordinate_system: CoordinateSystem


def normalized_place(provider_payload: ProviderPlacePayload) -> Place:
    """Represent the output that a provider adapter must hand to the domain layer."""

    return Place(
        place_id="place_palace_001",
        provider=provider_payload["provider"],
        provider_place_id=provider_payload["provider_id"],
        name="故宫博物院",
        category="museum",
        address="北京市东城区景山前街4号",
        raw_coordinate=Coordinate(
            longitude=provider_payload["longitude"],
            latitude=provider_payload["latitude"],
            system=provider_payload["coordinate_system"],
        ),
        canonical_coordinate=Coordinate(
            longitude=116.397026,
            latitude=39.918058,
            system=CoordinateSystem.GCJ02,
        ),
        price=CostEstimate(kind=CostKind.UNKNOWN, basis="预约票价待官方渠道复核"),
        visit_duration_estimate=VisitDurationEstimate(
            minimum_minutes=120,
            maximum_minutes=240,
            method="synthetic category baseline",
            confidence=0.6,
        ),
        evidence_ids=("evidence_palace_001",),
    )


def test_different_provider_coordinates_map_to_one_canonical_shape() -> None:
    """Provider identity and raw CRS remain visible after normalization."""

    amap = normalized_place(
        {
            "provider": "amap",
            "provider_id": "amap-b000a8urxb",
            "longitude": 116.397026,
            "latitude": 39.918058,
            "coordinate_system": CoordinateSystem.GCJ02,
        }
    )
    baidu = normalized_place(
        {
            "provider": "baidu",
            "provider_id": "baidu-palace-synthetic",
            "longitude": 116.403414,
            "latitude": 39.924091,
            "coordinate_system": CoordinateSystem.BD09,
        }
    )

    assert amap.canonical_coordinate == baidu.canonical_coordinate
    assert amap.raw_coordinate.system is CoordinateSystem.GCJ02
    assert baidu.raw_coordinate.system is CoordinateSystem.BD09


def test_unknown_cost_cannot_smuggle_a_zero_amount() -> None:
    """Unknown prices stay unknown instead of becoming a misleading zero."""

    with pytest.raises(ValidationError, match="must not contain numeric"):
        CostEstimate(kind=CostKind.UNKNOWN, lower=Decimal("0"), upper=Decimal("0"))


@pytest.mark.parametrize(
    ("longitude", "latitude"),
    [(181, 39.9), (116.4, -91)],
)
def test_out_of_bounds_coordinates_are_rejected(longitude: float, latitude: float) -> None:
    """Invalid provider coordinates must fail before route computation."""

    with pytest.raises(ValidationError):
        Coordinate(
            longitude=longitude,
            latitude=latitude,
            system=CoordinateSystem.WGS84,
        )


def test_timestamps_for_dynamic_models_must_be_timezone_aware() -> None:
    """Pydantic's aware timestamp type is exercised for later dynamic facts."""

    retrieved_at = datetime(2026, 9, 12, 8, 0)

    with pytest.raises(ValidationError, match="timezone"):
        WeatherForecast(
            weather_id="weather_beijing_001",
            provider="fixture",
            location=Coordinate(
                longitude=116.4,
                latitude=39.9,
                system=CoordinateSystem.GCJ02,
            ),
            forecast_date=date(2026, 10, 1),
            condition="晴",
            temperature_min_c=12,
            temperature_max_c=24,
            retrieved_at=retrieved_at,
            valid_until=datetime.now(UTC) + timedelta(days=1),
            evidence_ids=("evidence_weather_001",),
        )
