"""Deterministic C11 validator tests using only the synthetic scenario."""

from datetime import datetime
from decimal import Decimal

from packages.domain import (
    ClaimStatus,
    CoordinateSystem,
    ItineraryVersion,
    ValidationCode,
    validate_itinerary,
)
from packages.evals.fixtures import FIXTURE_ZONE, build_synthetic_scenario


def codes_for_report(*, plan: ItineraryVersion | None = None) -> set[ValidationCode]:
    """Build a fresh scenario and return codes for the selected plan."""

    scenario = build_synthetic_scenario()
    selected_plan = scenario.candidates.plans[0] if plan is None else plan
    report = validate_itinerary(
        selected_plan,
        scenario.request,
        places=scenario.places,
        routes=scenario.routes,
        claims=scenario.claims,
        evidence=scenario.evidence,
    )
    return {issue.code for issue in report.issues}


def test_valid_synthetic_plan_passes_every_validator() -> None:
    scenario = build_synthetic_scenario()

    report = validate_itinerary(
        scenario.candidates.plans[0],
        scenario.request,
        places=scenario.places,
        routes=scenario.routes,
        claims=scenario.claims,
        evidence=scenario.evidence,
    )

    assert report.passed is True
    assert report.issues == ()


def test_hard_budget_overrun_is_blocking() -> None:
    scenario = build_synthetic_scenario()
    request = scenario.request.model_copy(
        update={"budget": scenario.request.budget.model_copy(update={"total": Decimal("100.00")})}
    )

    report = validate_itinerary(
        scenario.candidates.plans[0],
        request,
        places=scenario.places,
        routes=scenario.routes,
        claims=scenario.claims,
        evidence=scenario.evidence,
    )

    assert report.passed is False
    assert ValidationCode.BUDGET_EXCEEDED in {issue.code for issue in report.errors}


def test_overlap_and_daily_window_violations_are_detected() -> None:
    scenario = build_synthetic_scenario()
    plan = scenario.candidates.plans[0]
    first, second = plan.days[0].items
    changed_second = second.model_copy(
        update={"start_at": datetime(2026, 10, 1, 11, 30, tzinfo=FIXTURE_ZONE)}
    )
    changed_day = plan.days[0].model_copy(update={"items": (first, changed_second)})
    changed_plan = plan.model_copy(update={"days": (changed_day,)})

    assert ValidationCode.ITEM_OVERLAP in codes_for_report(plan=changed_plan)

    early_first = first.model_copy(
        update={"start_at": datetime(2026, 10, 1, 8, 30, tzinfo=FIXTURE_ZONE)}
    )
    early_day = plan.days[0].model_copy(update={"items": (early_first, second)})
    early_plan = plan.model_copy(update={"days": (early_day,)})
    assert ValidationCode.ITEM_OUTSIDE_DAILY_WINDOW in codes_for_report(plan=early_plan)


def test_special_closure_is_detected() -> None:
    scenario = build_synthetic_scenario()
    palace, park = scenario.places
    assert palace.opening_hours is not None
    closed_schedule = palace.opening_hours.model_copy(
        update={"special_closures": (scenario.request.start_date,)}
    )
    closed_palace = palace.model_copy(update={"opening_hours": closed_schedule})

    report = validate_itinerary(
        scenario.candidates.plans[0],
        scenario.request,
        places=(closed_palace, park),
        routes=scenario.routes,
        claims=scenario.claims,
        evidence=scenario.evidence,
    )

    assert ValidationCode.OPENING_HOURS_CONFLICT in {issue.code for issue in report.errors}


def test_coordinate_and_reference_integrity_are_detected() -> None:
    scenario = build_synthetic_scenario()
    palace, park = scenario.places
    wgs_coordinate = park.canonical_coordinate.model_copy(update={"system": CoordinateSystem.WGS84})
    mismatched_park = park.model_copy(update={"canonical_coordinate": wgs_coordinate})
    plan = scenario.candidates.plans[0]
    first, second = plan.days[0].items
    broken_second = second.model_copy(
        update={
            "evidence_ids": ("evidence_missing_fixture",),
            "route_from_previous_id": "route_missing_fixture",
        }
    )
    broken_day = plan.days[0].model_copy(update={"items": (first, broken_second)})
    broken_plan = plan.model_copy(update={"days": (broken_day,)})

    report = validate_itinerary(
        broken_plan,
        scenario.request,
        places=(palace, mismatched_park),
        routes=scenario.routes,
        claims=scenario.claims,
        evidence=scenario.evidence,
    )
    codes = {issue.code for issue in report.errors}

    assert ValidationCode.COORDINATE_SYSTEM_MISMATCH in codes
    assert ValidationCode.MISSING_EVIDENCE_REFERENCE in codes
    assert ValidationCode.MISSING_ROUTE_REFERENCE in codes


def test_unknown_required_claim_blocks_execution() -> None:
    scenario = build_synthetic_scenario()
    first_claim, second_claim = scenario.claims
    unknown_claim = first_claim.model_copy(
        update={"status": ClaimStatus.UNKNOWN, "evidence_ids": ()}
    )

    report = validate_itinerary(
        scenario.candidates.plans[0],
        scenario.request,
        places=scenario.places,
        routes=scenario.routes,
        claims=(unknown_claim, second_claim),
        evidence=scenario.evidence,
    )

    assert ValidationCode.UNKNOWN_HARD_CONSTRAINT in {issue.code for issue in report.errors}
