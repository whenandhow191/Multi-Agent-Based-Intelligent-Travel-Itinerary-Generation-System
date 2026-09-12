"""Deterministic cross-object validators for executable itinerary plans."""

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

from packages.domain.common import DomainModel, Identifier, NonEmptyText
from packages.domain.itinerary import ItineraryVersion
from packages.domain.messaging import Claim, ClaimStatus, Evidence
from packages.domain.travel import CostKind, Place, Route, Weekday
from packages.domain.trip_request import BudgetPolicy, TripRequest


class ValidationCode(StrEnum):
    """Stable codes consumed by the Critic, API, and evaluation suite."""

    BUDGET_EXCEEDED = "budget_exceeded"
    BUDGET_UNVERIFIABLE = "budget_unverifiable"
    BUDGET_TOTAL_MISMATCH = "budget_total_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    ITEM_OUTSIDE_DAILY_WINDOW = "item_outside_daily_window"
    ITEM_DATE_MISMATCH = "item_date_mismatch"
    ITEM_OVERLAP = "item_overlap"
    ACTIVITY_TIME_EXCEEDED = "activity_time_exceeded"
    OPENING_HOURS_CONFLICT = "opening_hours_conflict"
    OPENING_HOURS_UNKNOWN = "opening_hours_unknown"
    COORDINATE_SYSTEM_MISMATCH = "coordinate_system_mismatch"
    MISSING_PLACE_REFERENCE = "missing_place_reference"
    MISSING_ROUTE_REFERENCE = "missing_route_reference"
    ROUTE_ENDPOINT_MISMATCH = "route_endpoint_mismatch"
    MISSING_CLAIM_REFERENCE = "missing_claim_reference"
    MISSING_EVIDENCE_REFERENCE = "missing_evidence_reference"
    UNKNOWN_HARD_CONSTRAINT = "unknown_hard_constraint"


class ValidationSeverity(StrEnum):
    """Whether a finding blocks plan execution."""

    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(DomainModel):
    """One deterministic finding with a stable location and related IDs."""

    code: ValidationCode
    severity: ValidationSeverity
    message: NonEmptyText
    path: NonEmptyText
    related_ids: tuple[Identifier, ...] = ()


class ValidationReport(DomainModel):
    """Aggregated findings from all deterministic validation passes."""

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def passed(self) -> bool:
        """Return true only when no blocking issue exists."""

        return not any(issue.severity is ValidationSeverity.ERROR for issue in self.issues)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        """Return only blocking issues."""

        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR)


def _issue(
    code: ValidationCode,
    message: str,
    path: str,
    *related_ids: str,
    severity: ValidationSeverity = ValidationSeverity.ERROR,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        message=message,
        path=path,
        related_ids=tuple(related_ids),
    )


def validate_budget(plan: ItineraryVersion, request: TripRequest) -> tuple[ValidationIssue, ...]:
    """Recalculate item/day/plan totals and enforce the request budget policy."""

    issues: list[ValidationIssue] = []
    plan_lower = Decimal(0)
    plan_upper = Decimal(0)
    plan_has_unknown = False

    for day_index, day in enumerate(plan.days):
        day_lower = Decimal(0)
        day_upper = Decimal(0)
        day_has_unknown = False
        for item_index, item in enumerate(day.items):
            cost = item.estimated_cost
            item_path = f"days[{day_index}].items[{item_index}].estimated_cost"
            if cost.currency != request.budget.currency:
                issues.append(
                    _issue(
                        ValidationCode.CURRENCY_MISMATCH,
                        "item currency differs from the request budget currency",
                        item_path,
                        item.plan_item_id,
                    )
                )
            if cost.kind is CostKind.UNKNOWN:
                day_has_unknown = True
                continue
            assert cost.lower is not None and cost.upper is not None
            day_lower += cost.lower
            day_upper += cost.upper

        if day.daily_cost.currency != request.budget.currency:
            issues.append(
                _issue(
                    ValidationCode.CURRENCY_MISMATCH,
                    "daily total currency differs from the request budget currency",
                    f"days[{day_index}].daily_cost",
                    plan.plan_id,
                )
            )
        if not day_has_unknown and (
            day.daily_cost.lower != day_lower or day.daily_cost.upper != day_upper
        ):
            issues.append(
                _issue(
                    ValidationCode.BUDGET_TOTAL_MISMATCH,
                    "declared daily cost does not equal the sum of its items",
                    f"days[{day_index}].daily_cost",
                    plan.plan_id,
                )
            )
        plan_lower += day_lower
        plan_upper += day_upper
        plan_has_unknown = plan_has_unknown or day_has_unknown

    if plan.total_cost.currency != request.budget.currency:
        issues.append(
            _issue(
                ValidationCode.CURRENCY_MISMATCH,
                "plan total currency differs from the request budget currency",
                "total_cost",
                plan.plan_id,
            )
        )
    if not plan_has_unknown and (
        plan.total_cost.lower != plan_lower or plan.total_cost.upper != plan_upper
    ):
        issues.append(
            _issue(
                ValidationCode.BUDGET_TOTAL_MISMATCH,
                "declared plan cost does not equal the sum of its days",
                "total_cost",
                plan.plan_id,
            )
        )

    if plan_has_unknown:
        severity = (
            ValidationSeverity.ERROR
            if request.budget.policy is BudgetPolicy.HARD
            else ValidationSeverity.WARNING
        )
        issues.append(
            _issue(
                ValidationCode.BUDGET_UNVERIFIABLE,
                "unknown item cost prevents a complete budget proof",
                "total_cost",
                plan.plan_id,
                severity=severity,
            )
        )
    elif plan_upper > request.budget.total:
        severity = (
            ValidationSeverity.ERROR
            if request.budget.policy is BudgetPolicy.HARD
            else ValidationSeverity.WARNING
        )
        issues.append(
            _issue(
                ValidationCode.BUDGET_EXCEEDED,
                "maximum calculated plan cost exceeds the request budget",
                "total_cost",
                plan.plan_id,
                severity=severity,
            )
        )
    return tuple(issues)


def validate_time_windows(
    plan: ItineraryVersion, request: TripRequest
) -> tuple[ValidationIssue, ...]:
    """Enforce trip dates, local daily bounds, and maximum activity minutes."""

    issues: list[ValidationIssue] = []
    window = request.hard_constraints.daily_time_window
    for day_index, day in enumerate(plan.days):
        daily_minutes = 0
        for item_index, item in enumerate(day.items):
            path = f"days[{day_index}].items[{item_index}]"
            if item.start_at.date() != day.date or item.end_at.date() != day.date:
                issues.append(
                    _issue(
                        ValidationCode.ITEM_DATE_MISMATCH,
                        "activity timestamps must remain on their declared itinerary date",
                        path,
                        item.plan_item_id,
                    )
                )
            if item.start_at.time() < window.start or item.end_at.time() > window.end:
                issues.append(
                    _issue(
                        ValidationCode.ITEM_OUTSIDE_DAILY_WINDOW,
                        "activity falls outside the request daily time window",
                        path,
                        item.plan_item_id,
                    )
                )
            daily_minutes += int((item.end_at - item.start_at).total_seconds() // 60)
        if daily_minutes > request.hard_constraints.max_activity_minutes_per_day:
            issues.append(
                _issue(
                    ValidationCode.ACTIVITY_TIME_EXCEEDED,
                    "scheduled activity minutes exceed the daily maximum",
                    f"days[{day_index}].items",
                    plan.plan_id,
                )
            )
    return tuple(issues)


def validate_overlaps(plan: ItineraryVersion) -> tuple[ValidationIssue, ...]:
    """Reject activities whose scheduled intervals intersect."""

    issues: list[ValidationIssue] = []
    for day_index, day in enumerate(plan.days):
        ordered = sorted(day.items, key=lambda item: item.start_at)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.start_at < previous.end_at:
                issues.append(
                    _issue(
                        ValidationCode.ITEM_OVERLAP,
                        "activity overlaps the preceding scheduled activity",
                        f"days[{day_index}].items",
                        previous.plan_item_id,
                        current.plan_item_id,
                    )
                )
    return tuple(issues)


def validate_opening_hours(
    plan: ItineraryVersion, places: Sequence[Place]
) -> tuple[ValidationIssue, ...]:
    """Require every scheduled activity to fit a known opening interval."""

    place_by_id = {place.place_id: place for place in places}
    weekdays = tuple(Weekday)
    issues: list[ValidationIssue] = []
    for day_index, day in enumerate(plan.days):
        for item_index, item in enumerate(day.items):
            place = place_by_id.get(item.place_id)
            if place is None:
                continue
            path = f"days[{day_index}].items[{item_index}]"
            schedule = place.opening_hours
            if schedule is None:
                issues.append(
                    _issue(
                        ValidationCode.OPENING_HOURS_UNKNOWN,
                        "place opening hours are unknown",
                        path,
                        item.plan_item_id,
                        place.place_id,
                    )
                )
                continue
            weekday = weekdays[day.date.weekday()]
            fits_period = any(
                period.weekday is weekday
                and period.opens <= item.start_at.time()
                and item.end_at.time() <= period.closes
                for period in schedule.periods
            )
            if day.date in schedule.special_closures or not fits_period:
                issues.append(
                    _issue(
                        ValidationCode.OPENING_HOURS_CONFLICT,
                        "activity is scheduled while the place is closed",
                        path,
                        item.plan_item_id,
                        place.place_id,
                    )
                )
    return tuple(issues)


def validate_coordinates(
    places: Sequence[Place], routes: Sequence[Route]
) -> tuple[ValidationIssue, ...]:
    """Require one explicit canonical coordinate system across places and routes."""

    issues: list[ValidationIssue] = []
    systems = {place.canonical_coordinate.system for place in places}
    if len(systems) > 1:
        issues.append(
            _issue(
                ValidationCode.COORDINATE_SYSTEM_MISMATCH,
                "canonical place coordinates use more than one coordinate system",
                "places",
                *(place.place_id for place in places),
            )
        )
    if systems:
        canonical_system = next(iter(systems))
        for route_index, route in enumerate(routes):
            if route.coordinate_system is not canonical_system:
                issues.append(
                    _issue(
                        ValidationCode.COORDINATE_SYSTEM_MISMATCH,
                        "route coordinate system differs from canonical places",
                        f"routes[{route_index}].coordinate_system",
                        route.route_id,
                    )
                )
    return tuple(issues)


def validate_references(
    plan: ItineraryVersion,
    places: Sequence[Place],
    routes: Sequence[Route],
    claims: Sequence[Claim],
    evidence: Sequence[Evidence],
) -> tuple[ValidationIssue, ...]:
    """Resolve every cross-object ID and verify route endpoints and hard claims."""

    place_by_id = {place.place_id: place for place in places}
    route_by_id = {route.route_id: route for route in routes}
    claim_by_id = {claim.claim_id: claim for claim in claims}
    evidence_ids = {item.evidence_id for item in evidence}
    issues: list[ValidationIssue] = []

    for place_index, place in enumerate(places):
        for evidence_id in place.evidence_ids:
            if evidence_id not in evidence_ids:
                issues.append(
                    _issue(
                        ValidationCode.MISSING_EVIDENCE_REFERENCE,
                        "place references unknown evidence",
                        f"places[{place_index}].evidence_ids",
                        place.place_id,
                        evidence_id,
                    )
                )

    for route_index, route in enumerate(routes):
        for endpoint_name, place_id in (
            ("origin_place_id", route.origin_place_id),
            ("destination_place_id", route.destination_place_id),
        ):
            if place_id not in place_by_id:
                issues.append(
                    _issue(
                        ValidationCode.MISSING_PLACE_REFERENCE,
                        "route endpoint references an unknown place",
                        f"routes[{route_index}].{endpoint_name}",
                        route.route_id,
                        place_id,
                    )
                )
        for evidence_id in route.evidence_ids:
            if evidence_id not in evidence_ids:
                issues.append(
                    _issue(
                        ValidationCode.MISSING_EVIDENCE_REFERENCE,
                        "route references unknown evidence",
                        f"routes[{route_index}].evidence_ids",
                        route.route_id,
                        evidence_id,
                    )
                )

    for claim_index, claim in enumerate(claims):
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_ids:
                issues.append(
                    _issue(
                        ValidationCode.MISSING_EVIDENCE_REFERENCE,
                        "claim references unknown evidence",
                        f"claims[{claim_index}].evidence_ids",
                        claim.claim_id,
                        evidence_id,
                    )
                )
        if claim.required_hard_constraint and claim.status is ClaimStatus.UNKNOWN:
            issues.append(
                _issue(
                    ValidationCode.UNKNOWN_HARD_CONSTRAINT,
                    "a required hard-constraint claim remains unknown",
                    f"claims[{claim_index}].status",
                    claim.claim_id,
                )
            )

    for day_index, day in enumerate(plan.days):
        previous_place_id: str | None = None
        for item_index, item in enumerate(day.items):
            path = f"days[{day_index}].items[{item_index}]"
            if item.place_id not in place_by_id:
                issues.append(
                    _issue(
                        ValidationCode.MISSING_PLACE_REFERENCE,
                        "activity references an unknown place",
                        f"{path}.place_id",
                        item.plan_item_id,
                        item.place_id,
                    )
                )
            for claim_id in item.claim_ids:
                if claim_id not in claim_by_id:
                    issues.append(
                        _issue(
                            ValidationCode.MISSING_CLAIM_REFERENCE,
                            "activity references an unknown claim",
                            f"{path}.claim_ids",
                            item.plan_item_id,
                            claim_id,
                        )
                    )
            for evidence_id in item.evidence_ids:
                if evidence_id not in evidence_ids:
                    issues.append(
                        _issue(
                            ValidationCode.MISSING_EVIDENCE_REFERENCE,
                            "activity references unknown evidence",
                            f"{path}.evidence_ids",
                            item.plan_item_id,
                            evidence_id,
                        )
                    )
            if item.route_from_previous_id is not None:
                referenced_route = route_by_id.get(item.route_from_previous_id)
                if referenced_route is None:
                    issues.append(
                        _issue(
                            ValidationCode.MISSING_ROUTE_REFERENCE,
                            "activity references an unknown route",
                            f"{path}.route_from_previous_id",
                            item.plan_item_id,
                            item.route_from_previous_id,
                        )
                    )
                elif (
                    previous_place_id is None
                    or referenced_route.origin_place_id != previous_place_id
                    or referenced_route.destination_place_id != item.place_id
                ):
                    issues.append(
                        _issue(
                            ValidationCode.ROUTE_ENDPOINT_MISMATCH,
                            "route endpoints do not connect the preceding and current activities",
                            f"{path}.route_from_previous_id",
                            item.plan_item_id,
                            referenced_route.route_id,
                        )
                    )
            previous_place_id = item.place_id
    return tuple(issues)


def validate_itinerary(
    plan: ItineraryVersion,
    request: TripRequest,
    *,
    places: Sequence[Place],
    routes: Sequence[Route],
    claims: Sequence[Claim],
    evidence: Sequence[Evidence],
) -> ValidationReport:
    """Run every deterministic validator in a stable, documented order."""

    passes: tuple[Sequence[ValidationIssue], ...] = (
        validate_budget(plan, request),
        validate_time_windows(plan, request),
        validate_overlaps(plan),
        validate_opening_hours(plan, places),
        validate_coordinates(places, routes),
        validate_references(plan, places, routes, claims, evidence),
    )
    return ValidationReport(issues=tuple(issue for result in passes for issue in result))
