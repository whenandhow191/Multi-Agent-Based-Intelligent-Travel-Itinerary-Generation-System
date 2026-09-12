"""Contracts for user trip requests, constraints, preferences, and clarification."""

from datetime import date, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from packages.domain.common import (
    CurrencyCode,
    DomainModel,
    NonEmptyText,
    ShortText,
    ensure_unique,
)


class TravelPace(StrEnum):
    """Supported itinerary density preferences."""

    RELAXED = "relaxed"
    BALANCED = "balanced"
    INTENSIVE = "intensive"


class DayPeriodPreference(StrEnum):
    """User preference for earlier or later daily activities."""

    EARLY = "early"
    NEUTRAL = "neutral"
    LATE = "late"


class BudgetPolicy(StrEnum):
    """Whether a total budget is a hard cap or a flexible target."""

    HARD = "hard"
    FLEXIBLE = "flexible"


class TravelParty(DomainModel):
    """Composition and accessibility characteristics of the travelling group."""

    adults: Annotated[int, Field(ge=1, le=20)] = 1
    children: Annotated[int, Field(ge=0, le=20)] = 0
    seniors: Annotated[int, Field(ge=0, le=20)] = 0
    mobility_restricted: bool = False
    accessibility_needs: tuple[ShortText, ...] = ()

    @property
    def size(self) -> int:
        """Return the deterministic number of travellers."""

        return self.adults + self.children + self.seniors


class TripBudget(DomainModel):
    """Total trip budget and enforcement policy."""

    total: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    currency: CurrencyCode = "CNY"
    policy: BudgetPolicy = BudgetPolicy.FLEXIBLE
    flexibility_percent: Annotated[int, Field(ge=0, le=100)] = 10

    @model_validator(mode="after")
    def hard_budget_has_no_flexibility(self) -> Self:
        """Keep hard-cap semantics unambiguous."""

        if self.policy is BudgetPolicy.HARD and self.flexibility_percent != 0:
            raise ValueError("hard budget must use flexibility_percent=0")
        return self


class DailyTimeWindow(DomainModel):
    """Default local-time window in which itinerary activities may occur."""

    start: time = time(hour=9)
    end: time = time(hour=21)

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty and overnight windows in the MVP contract."""

        if self.end <= self.start:
            raise ValueError("daily window end must be later than start")
        return self


class FixedAppointment(DomainModel):
    """A user-confirmed activity that must remain at a fixed local time."""

    appointment_id: ShortText
    title: ShortText
    date: date
    start: time
    end: time
    place_name: ShortText

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject appointments with non-positive duration."""

        if self.end <= self.start:
            raise ValueError("appointment end must be later than start")
        return self


class HardConstraints(DomainModel):
    """Rules that executable plans are not allowed to violate."""

    daily_time_window: DailyTimeWindow = Field(default_factory=DailyTimeWindow)
    must_visit: tuple[ShortText, ...] = ()
    excluded_places: tuple[ShortText, ...] = ()
    dietary_restrictions: tuple[ShortText, ...] = ()
    fixed_appointments: tuple[FixedAppointment, ...] = ()
    max_activity_minutes_per_day: Annotated[int, Field(ge=60, le=960)] = 600

    @model_validator(mode="after")
    def lists_are_unique_and_compatible(self) -> Self:
        """Reject duplicated or directly contradictory place rules."""

        ensure_unique(self.must_visit, "must_visit")
        ensure_unique(self.excluded_places, "excluded_places")
        overlap = set(self.must_visit) & set(self.excluded_places)
        if overlap:
            raise ValueError(f"places cannot be both required and excluded: {sorted(overlap)}")
        return self


class SoftPreferences(DomainModel):
    """Preferences used for scoring but allowed to yield to hard constraints."""

    interests: tuple[ShortText, ...] = ()
    pace: TravelPace = TravelPace.BALANCED
    day_period: DayPeriodPreference = DayPeriodPreference.NEUTRAL
    transport_modes: tuple[ShortText, ...] = ()
    lodging_preferences: tuple[ShortText, ...] = ()
    dining_preferences: tuple[ShortText, ...] = ()

    @model_validator(mode="after")
    def preference_lists_are_unique(self) -> Self:
        """Avoid double-counting duplicated scoring preferences."""

        for field_name in (
            "interests",
            "transport_modes",
            "lodging_preferences",
            "dining_preferences",
        ):
            ensure_unique(getattr(self, field_name), field_name)
        return self


class ClarificationQuestion(DomainModel):
    """A structured question raised when a request cannot safely proceed."""

    question_id: ShortText
    field_path: ShortText
    question: NonEmptyText
    reason: NonEmptyText
    blocking: bool = True
    suggestions: tuple[NonEmptyText, ...] = ()


class TripRequest(DomainModel):
    """Versioned, validated input contract for one itinerary planning run."""

    schema_version: Annotated[str, Field(pattern=r"^1\.0$")] = "1.0"
    request_id: ShortText
    origin: ShortText
    destination: ShortText
    start_date: date
    end_date: date | None = None
    days: Annotated[int | None, Field(ge=1, le=7)] = None
    timezone: ShortText = "Asia/Shanghai"
    party: TravelParty = Field(default_factory=TravelParty)
    budget: TripBudget
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    soft_preferences: SoftPreferences = Field(default_factory=SoftPreferences)
    natural_language_notes: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_date_range_and_appointments(self) -> Self:
        """Resolve the date range and reject conflicting duration information."""

        if self.end_date is None and self.days is None:
            raise ValueError("either end_date or days is required")

        resolved_end = self.resolved_end_date
        duration = (resolved_end - self.start_date).days + 1
        if duration < 1 or duration > 7:
            raise ValueError("trip duration must be between 1 and 7 days")
        if self.days is not None and self.days != duration:
            raise ValueError("days does not match start_date and end_date")

        for appointment in self.hard_constraints.fixed_appointments:
            if not self.start_date <= appointment.date <= resolved_end:
                raise ValueError(
                    f"appointment {appointment.appointment_id} falls outside trip dates"
                )
        return self

    @property
    def resolved_end_date(self) -> date:
        """Return the inclusive end date regardless of which input form was used."""

        if self.end_date is not None:
            return self.end_date
        assert self.days is not None
        return self.start_date + timedelta(days=self.days - 1)

    @property
    def duration_days(self) -> int:
        """Return the inclusive number of travel days."""

        return (self.resolved_end_date - self.start_date).days + 1
