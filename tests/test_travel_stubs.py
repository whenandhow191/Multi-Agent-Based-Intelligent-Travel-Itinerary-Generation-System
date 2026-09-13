"""C31 transport and lodging stub behavior tests."""

import asyncio
from datetime import UTC, date, datetime

from packages.domain import Availability, CostKind
from packages.tools.travel_stubs import (
    FixtureTravelProviders,
    FlightSearchInput,
    LodgingSearchInput,
    RailOfficialLinkInput,
)

NOW = datetime(2026, 9, 13, 13, 0, tzinfo=UTC)


def test_flight_fixture_never_claims_live_inventory_or_price() -> None:
    result = asyncio.run(
        FixtureTravelProviders(now=NOW).search_flights(
            FlightSearchInput(
                origin="上海",
                destination="北京",
                departure_date=date(2026, 10, 1),
            )
        )
    )

    assert result.inventory_live is False
    assert result.options[0].availability is Availability.REFERENCE_ONLY
    assert result.options[0].price.kind is CostKind.UNKNOWN
    assert result.warnings


def test_lodging_fixture_marks_room_price_and_availability_unknown() -> None:
    result = asyncio.run(
        FixtureTravelProviders(now=NOW).search_lodging(
            LodgingSearchInput(
                city="北京",
                area="东城区",
                check_in=date(2026, 10, 1),
                check_out=date(2026, 10, 3),
                limit=2,
            )
        )
    )

    assert result.inventory_live is False
    assert len(result.candidates) == 2
    assert all(item.nightly_price.kind is CostKind.UNKNOWN for item in result.candidates)


def test_rail_provider_only_returns_official_handoff() -> None:
    result = asyncio.run(
        FixtureTravelProviders(now=NOW).rail_link(
            RailOfficialLinkInput(
                origin="上海",
                destination="北京",
                departure_date=date(2026, 10, 1),
            )
        )
    )

    assert result.url.startswith("https://www.12306.cn/")
    assert result.inventory_live is False
    assert result.manual_confirmation_required is True
