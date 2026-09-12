"""Contracts for itinerary candidates, reviews, comparisons, and final output."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from packages.domain.common import DomainModel, Identifier, NonEmptyText, SchemaVersion, ShortText
from packages.domain.messaging import Evidence, Freshness
from packages.domain.travel import CostEstimate, RouteMode
from packages.domain.trip_request import TripRequest


class PlanStrategy(StrEnum):
    """Distinct optimization profiles offered to the user."""

    BALANCED = "balanced"
    ECONOMY = "economy"
    RELAXED = "relaxed"


class ItineraryItem(DomainModel):
    """One scheduled activity and the references that justify it."""

    plan_item_id: Identifier
    start_at: AwareDatetime
    end_at: AwareDatetime
    place_id: Identifier
    activity: NonEmptyText
    estimated_cost: CostEstimate
    route_from_previous_id: Identifier | None = None
    travel_mode_from_previous: RouteMode | None = None
    travel_minutes_from_previous: Annotated[int | None, Field(ge=0)] = None
    claim_ids: tuple[Identifier, ...] = ()
    evidence_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def chronology_and_route_shape_are_valid(self) -> Self:
        """Validate activity duration and all-or-nothing route metadata."""

        if self.end_at <= self.start_at:
            raise ValueError("itinerary item end must be later than start")
        route_fields = (
            self.route_from_previous_id,
            self.travel_mode_from_previous,
            self.travel_minutes_from_previous,
        )
        if self.route_from_previous_id is None and any(
            value is not None for value in route_fields[1:]
        ):
            raise ValueError("travel metadata requires route_from_previous_id")
        if self.route_from_previous_id is not None and any(
            value is None for value in route_fields[1:]
        ):
            raise ValueError("route reference requires travel mode and duration")
        if not self.evidence_ids:
            raise ValueError("itinerary item requires at least one evidence reference")
        return self


class DailyItinerary(DomainModel):
    """All activities and declared cost for one local calendar day."""

    date: date
    timezone: ShortText
    items: Annotated[tuple[ItineraryItem, ...], Field(min_length=1)]
    daily_cost: CostEstimate
    warnings: tuple[NonEmptyText, ...] = ()

    @model_validator(mode="after")
    def item_identifiers_are_unique(self) -> Self:
        """Reject duplicate IDs that would break map/timeline linking."""

        item_ids = [item.plan_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("daily itinerary item IDs must be unique")
        return self


class ScoreBreakdown(DomainModel):
    """Explainable score dimensions on a normalized zero-to-one scale."""

    feasibility: Annotated[float, Field(ge=0, le=1)]
    preference_match: Annotated[float, Field(ge=0, le=1)]
    budget_fit: Annotated[float, Field(ge=0, le=1)]
    risk_resilience: Annotated[float, Field(ge=0, le=1)]
    evidence_quality: Annotated[float, Field(ge=0, le=1)]

    @property
    def weighted_total(self) -> float:
        """Return the documented 35/25/20/10/10 weighted score."""

        return round(
            self.feasibility * 0.35
            + self.preference_match * 0.25
            + self.budget_fit * 0.20
            + self.risk_resilience * 0.10
            + self.evidence_quality * 0.10,
            4,
        )


class ItineraryVersion(DomainModel):
    """One complete candidate itinerary with a distinct strategy."""

    plan_id: Identifier
    title: ShortText
    strategy: PlanStrategy
    days: Annotated[tuple[DailyItinerary, ...], Field(min_length=1, max_length=7)]
    total_cost: CostEstimate
    score_breakdown: ScoreBreakdown
    is_executable: bool
    unresolved_risks: tuple[NonEmptyText, ...] = ()

    @model_validator(mode="after")
    def dates_and_items_are_unique(self) -> Self:
        """Require one entry per date and globally addressable item IDs."""

        dates = [day.date for day in self.days]
        if len(dates) != len(set(dates)):
            raise ValueError("plan day dates must be unique")
        item_ids = [item.plan_item_id for day in self.days for item in day.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("plan item IDs must be unique across all days")
        if self.is_executable and self.unresolved_risks:
            raise ValueError("executable plan cannot contain unresolved risks")
        return self


class PlanCandidatesArtifact(DomainModel):
    """Structured A3 output containing one to three candidate plans."""

    schema_version: SchemaVersion = "1.0"
    artifact_id: Identifier
    run_id: Identifier
    producer_agent: Identifier
    plans: Annotated[tuple[ItineraryVersion, ...], Field(min_length=1, max_length=3)]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def plan_ids_and_strategies_are_unique(self) -> Self:
        """Ensure candidates are addressable and genuinely distinct profiles."""

        plan_ids = [plan.plan_id for plan in self.plans]
        strategies = [plan.strategy for plan in self.plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("candidate plan IDs must be unique")
        if len(strategies) != len(set(strategies)):
            raise ValueError("candidate plan strategies must be unique")
        return self


class ReviewVerdict(StrEnum):
    """A4 decision for the reviewed candidate set."""

    PASS = "pass"
    REVISE = "revise"
    REJECT = "reject"


class IssueSeverity(StrEnum):
    """Machine-actionable review severity."""

    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class HardViolation(DomainModel):
    """Evidence-linked hard-constraint problem found during review."""

    code: Identifier
    severity: IssueSeverity
    plan_id: Identifier
    plan_item_id: Identifier | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    message: NonEmptyText


class PatchRequest(DomainModel):
    """A bounded repair request routed to one registered Agent."""

    target_agent: Identifier
    action: Identifier
    plan_id: Identifier
    plan_item_id: Identifier | None = None
    constraints: dict[str, JsonValue] = Field(default_factory=dict)


class ReviewArtifact(DomainModel):
    """Structured critic output that can pass, revise, or reject candidates."""

    schema_version: SchemaVersion = "1.0"
    artifact_id: Identifier
    run_id: Identifier
    producer_agent: Identifier
    reviewed_plan_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    verdict: ReviewVerdict
    plan_scores: dict[Identifier, ScoreBreakdown]
    hard_violations: tuple[HardViolation, ...] = ()
    patch_requests: tuple[PatchRequest, ...] = ()
    created_at: AwareDatetime

    @model_validator(mode="after")
    def verdict_matches_findings(self) -> Self:
        """Prevent a passing verdict from hiding violations or repair work."""

        if self.verdict is ReviewVerdict.PASS and (self.hard_violations or self.patch_requests):
            raise ValueError("pass review cannot contain violations or patch requests")
        if self.verdict is ReviewVerdict.REVISE and not self.patch_requests:
            raise ValueError("revise review requires at least one patch request")
        if set(self.plan_scores) != set(self.reviewed_plan_ids):
            raise ValueError("plan_scores must cover exactly the reviewed plans")
        return self


class PlanComparisonEntry(DomainModel):
    """Deterministically calculated metrics for comparing one plan."""

    plan_id: Identifier
    total_cost: CostEstimate
    activity_count: Annotated[int, Field(ge=0)]
    commute_minutes: Annotated[int, Field(ge=0)]
    free_minutes: Annotated[int, Field(ge=0)]
    preference_coverage: Annotated[float, Field(ge=0, le=1)]
    risk_count: Annotated[int, Field(ge=0)]
    evidence_coverage: Annotated[float, Field(ge=0, le=1)]


class PlanComparison(DomainModel):
    """Comparable metrics for every candidate included in final output."""

    entries: Annotated[tuple[PlanComparisonEntry, ...], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def plan_ids_are_unique(self) -> Self:
        """Require exactly one comparison row per plan ID."""

        plan_ids = [entry.plan_id for entry in self.entries]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("comparison plan IDs must be unique")
        return self


class CollaborationSummary(DomainModel):
    """Safe operational summary without private reasoning or secret values."""

    participating_agents: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    completed_task_count: Annotated[int, Field(ge=0)]
    revision_rounds: Annotated[int, Field(ge=0, le=2)]
    notes: tuple[NonEmptyText, ...] = ()


class DataFreshnessSummary(DomainModel):
    """Aggregate freshness information displayed alongside final plans."""

    overall: Freshness
    oldest_retrieved_at: AwareDatetime
    stale_evidence_count: Annotated[int, Field(ge=0)] = 0
    unknown_evidence_count: Annotated[int, Field(ge=0)] = 0


class FinalPlanBundle(DomainModel):
    """Single source for JSON, Markdown, comparison, and future UI rendering."""

    schema_version: SchemaVersion = "1.0"
    run_id: Identifier
    request_summary: TripRequest
    assumptions: tuple[NonEmptyText, ...] = ()
    plans: Annotated[tuple[ItineraryVersion, ...], Field(min_length=1, max_length=3)]
    comparison: PlanComparison
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]
    collaboration_summary: CollaborationSummary
    generated_at: AwareDatetime
    data_freshness: DataFreshnessSummary

    @model_validator(mode="after")
    def plan_comparison_and_evidence_ids_are_unique(self) -> Self:
        """Keep top-level plans, comparison rows, and evidence addressable."""

        plan_ids = [plan.plan_id for plan in self.plans]
        compared_ids = [entry.plan_id for entry in self.comparison.entries]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("final plan IDs must be unique")
        if set(plan_ids) != set(compared_ids):
            raise ValueError("comparison must cover exactly the final plans")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("final evidence IDs must be unique")
        return self


CritiqueArtifact = ReviewArtifact
