"""Tests for the C06 trip request contracts."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.domain import TripRequest


def test_example_trip_request_round_trips_as_valid_json() -> None:
    """The documented example should parse and serialize without information loss."""

    path = Path("examples/trip_request.beijing.json")
    request = TripRequest.model_validate_json(path.read_text(encoding="utf-8"))

    assert request.destination == "北京"
    assert request.duration_days == 3
    assert request.party.size == 2
    assert (
        TripRequest.model_validate_json(request.model_dump_json()).request_id == request.request_id
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"end_date": None, "days": None}, "either end_date or days is required"),
        ({"end_date": date(2026, 10, 4), "days": 3}, "days does not match"),
        ({"end_date": date(2026, 10, 9), "days": 9}, "less than or equal to 7"),
    ],
)
def test_invalid_date_combinations_are_rejected(changes: dict[str, object], message: str) -> None:
    """Conflicting or unsupported trip durations must never enter planning."""

    valid = TripRequest.model_validate_json(
        Path("examples/trip_request.beijing.json").read_text(encoding="utf-8")
    )

    with pytest.raises(ValidationError, match=message):
        TripRequest.model_validate({**valid.model_dump(), **changes})


def test_unknown_fields_are_rejected() -> None:
    """Misspelled or unversioned inputs should fail closed."""

    payload = TripRequest.model_validate_json(
        Path("examples/trip_request.beijing.json").read_text(encoding="utf-8")
    ).model_dump()
    payload["destinaton"] = "拼写错误"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TripRequest.model_validate(payload)
