"""Canonical provider-neutral models for travel facts and estimates."""

from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, model_validator

from packages.domain.common import (
    CurrencyCode,
    DomainModel,
    Identifier,
    NonEmptyText,
    ShortText,
    ensure_unique,
)


class CoordinateSystem(StrEnum):
    """Coordinate reference systems accepted at the provider boundary."""

    WGS84 = "WGS84"
    GCJ02 = "GCJ02"
    BD09 = "BD09"


class Coordinate(DomainModel):
    """A bounded geographic point with an explicit coordinate system."""

    longitude: Annotated[float, Field(ge=-180, le=180)]
    latitude: Annotated[float, Field(ge=-90, le=90)]
    system: CoordinateSystem


class CostKind(StrEnum):
    """Whether a cost is exact, an estimated range, or unknown."""

    KNOWN = "known"
    RANGE = "range"
    UNKNOWN = "unknown"


class CostEstimate(DomainModel):
    """Money represented without silently treating unknown values as zero."""

    kind: CostKind
    currency: CurrencyCode = "CNY"
    lower: Annotated[Decimal | None, Field(ge=0, max_digits=12, decimal_places=2)] = None
    upper: Annotated[Decimal | None, Field(ge=0, max_digits=12, decimal_places=2)] = None
    basis: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_amount_shape(self) -> Self:
        """Keep known, range, and unknown semantics mutually exclusive."""

        if self.kind is CostKind.UNKNOWN:
            if self.lower is not None or self.upper is not None:
                raise ValueError("unknown cost must not contain numeric amounts")
            return self
        if self.lower is None or self.upper is None:
            raise ValueError("known and range costs require lower and upper amounts")
        if self.lower > self.upper:
            raise ValueError("cost lower amount must not exceed upper amount")
        if self.kind is CostKind.KNOWN and self.lower != self.upper:
            raise ValueError("known cost requires equal lower and upper amounts")
        return self


class Weekday(StrEnum):
    """Locale-independent weekday labels used by opening periods."""

    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class OpeningPeriod(DomainModel):
    """One non-overnight local opening interval."""

    weekday: Weekday
    opens: time
    closes: time

    @model_validator(mode="after")
    def closes_after_opening(self) -> Self:
        """Reject overnight periods until the planner supports date rollover."""

        if self.closes <= self.opens:
            raise ValueError("opening period closes must be later than opens")
        return self


class OpeningSchedule(DomainModel):
    """Weekly opening hours and known exceptional closure dates."""

    timezone: ShortText
    periods: tuple[OpeningPeriod, ...]
    special_closures: tuple[date, ...] = ()
    notes: NonEmptyText | None = None

    @model_validator(mode="after")
    def schedule_is_non_empty_and_unique(self) -> Self:
        """Require at least one period and unique closure dates."""

        if not self.periods:
            raise ValueError("opening schedule requires at least one period")
        if len(self.special_closures) != len(set(self.special_closures)):
            raise ValueError("special_closures must not contain duplicates")
        return self


class VisitDurationEstimate(DomainModel):
    """System estimate kept separate from supplier-provided place facts."""

    minimum_minutes: Annotated[int, Field(ge=1, le=1440)]
    maximum_minutes: Annotated[int, Field(ge=1, le=1440)]
    method: ShortText
    confidence: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def range_is_ordered(self) -> Self:
        """Reject reversed visit-duration ranges."""

        if self.minimum_minutes > self.maximum_minutes:
            raise ValueError("minimum visit duration must not exceed maximum")
        return self


class Place(DomainModel):
    """Canonical POI with raw provenance and separately estimated fields."""

    place_id: Identifier
    provider: Identifier
    provider_place_id: NonEmptyText
    name: ShortText
    category: ShortText
    address: NonEmptyText | None = None
    raw_coordinate: Coordinate
    canonical_coordinate: Coordinate
    opening_hours: OpeningSchedule | None = None
    price: CostEstimate
    visit_duration_estimate: VisitDurationEstimate
    evidence_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def evidence_is_present(self) -> Self:
        """Provider-derived place facts require at least one source reference."""

        if not self.evidence_ids:
            raise ValueError("place requires at least one evidence reference")
        ensure_unique(self.evidence_ids, "evidence_ids")
        return self


class RouteMode(StrEnum):
    """Normalized local route modes."""

    WALKING = "walking"
    PUBLIC_TRANSIT = "public_transit"
    DRIVING = "driving"
    CYCLING = "cycling"
    TAXI = "taxi"


class RouteQuality(StrEnum):
    """Whether route metrics came from a provider or a marked fallback."""

    MEASURED = "measured"
    ESTIMATED = "estimated"


class Route(DomainModel):
    """Canonical route between two already normalized places."""

    route_id: Identifier
    provider: Identifier
    origin_place_id: Identifier
    destination_place_id: Identifier
    mode: RouteMode
    distance_meters: Annotated[int, Field(ge=0)]
    duration_minutes: Annotated[int, Field(ge=0)]
    quality: RouteQuality
    coordinate_system: CoordinateSystem
    retrieved_at: AwareDatetime
    evidence_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def endpoints_and_evidence_are_valid(self) -> Self:
        """Reject self-routes and routes without provenance."""

        if self.origin_place_id == self.destination_place_id:
            raise ValueError("route origin and destination must differ")
        if not self.evidence_ids:
            raise ValueError("route requires at least one evidence reference")
        ensure_unique(self.evidence_ids, "evidence_ids")
        return self


class WeatherForecast(DomainModel):
    """Canonical weather forecast with retrieval and validity metadata."""

    weather_id: Identifier
    provider: Identifier
    location: Coordinate
    forecast_date: date
    condition: ShortText
    temperature_min_c: Annotated[float, Field(ge=-100, le=70)]
    temperature_max_c: Annotated[float, Field(ge=-100, le=70)]
    precipitation_probability: Annotated[float, Field(ge=0, le=1)] | None = None
    retrieved_at: AwareDatetime
    valid_until: AwareDatetime
    evidence_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def forecast_ranges_are_valid(self) -> Self:
        """Validate temperature order, freshness window, and provenance."""

        if self.temperature_min_c > self.temperature_max_c:
            raise ValueError("minimum temperature must not exceed maximum")
        if self.valid_until <= self.retrieved_at:
            raise ValueError("weather valid_until must be later than retrieved_at")
        if not self.evidence_ids:
            raise ValueError("weather forecast requires evidence")
        return self


class Lodging(DomainModel):
    """Canonical lodging candidate without implying live availability."""

    lodging_id: Identifier
    provider: Identifier
    provider_lodging_id: NonEmptyText
    name: ShortText
    area: ShortText
    coordinate: Coordinate
    nightly_price: CostEstimate
    check_in_after: time = time(hour=14)
    check_out_before: time = time(hour=12)
    evidence_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def evidence_is_present(self) -> Self:
        """Require provenance even when the live price is unknown."""

        if not self.evidence_ids:
            raise ValueError("lodging requires evidence")
        return self


class IntercityMode(StrEnum):
    """Normalized modes for travel into or out of the destination."""

    FLIGHT = "flight"
    RAIL = "rail"
    COACH = "coach"
    SELF_DRIVE = "self_drive"


class Availability(StrEnum):
    """How strongly a transport option represents current inventory."""

    LIVE = "live"
    REFERENCE_ONLY = "reference_only"
    UNKNOWN = "unknown"


class IntercityTransport(DomainModel):
    """Canonical intercity transport option with explicit inventory quality."""

    transport_id: Identifier
    provider: Identifier
    mode: IntercityMode
    service_number: ShortText | None = None
    origin_name: ShortText
    destination_name: ShortText
    departure_at: AwareDatetime
    arrival_at: AwareDatetime
    price: CostEstimate
    availability: Availability
    retrieved_at: AwareDatetime
    evidence_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def chronology_and_evidence_are_valid(self) -> Self:
        """Reject time travel and untraceable transport options."""

        if self.arrival_at <= self.departure_at:
            raise ValueError("transport arrival must be later than departure")
        if not self.evidence_ids:
            raise ValueError("transport option requires evidence")
        return self
