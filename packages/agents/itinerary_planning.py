"""A3 itinerary planning Agent and deterministic OR-Tools optimizer."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ortools.sat.python import cp_model

from packages.agents.destination_intelligence import DestinationIntelArtifact
from packages.agents.mobility_lodging import MobilityLodgingArtifact
from packages.domain import (
    BudgetPolicy,
    Claim,
    CostEstimate,
    CostKind,
    DailyItinerary,
    Evidence,
    ItineraryItem,
    ItineraryVersion,
    OpeningPeriod,
    Place,
    PlanCandidatesArtifact,
    PlanStrategy,
    Route,
    ScoreBreakdown,
    TripRequest,
    Weekday,
    validate_itinerary,
)
from packages.harness.agent import AgentSpec, BaseAgent
from packages.harness.model_gateway import ModelGateway, ModelPolicy
from packages.harness.tool_gateway import ToolGateway

PLANNING_TOOL_ALLOWLIST = (
    "budget.calculate",
    "optimizer.solve",
    "plan.variant",
    "schedule.validate",
)


class PlanningInfeasibleError(ValueError):
    """A stable, explicit hard-constraint failure raised before publication."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _cost_total(costs: Sequence[CostEstimate], currency: str) -> CostEstimate:
    if any(cost.kind is CostKind.UNKNOWN for cost in costs):
        return CostEstimate(kind=CostKind.UNKNOWN, currency=currency, basis="unknown item cost")
    lower = sum((cost.lower or Decimal(0) for cost in costs), start=Decimal(0))
    upper = sum((cost.upper or Decimal(0) for cost in costs), start=Decimal(0))
    kind = CostKind.KNOWN if lower == upper else CostKind.RANGE
    return CostEstimate(
        kind=kind,
        currency=currency,
        lower=lower,
        upper=upper,
        basis="deterministic sum of selected activities",
    )


def _route_order(places: Sequence[Place], routes: Sequence[Route]) -> tuple[Place, ...] | None:
    """Find a deterministic path using only supplied route-matrix edges."""

    if len(places) < 2:
        return tuple(places)
    edges = {(route.origin_place_id, route.destination_place_id) for route in routes}

    def walk(prefix: tuple[Place, ...], remaining: tuple[Place, ...]) -> tuple[Place, ...] | None:
        if not remaining:
            return prefix
        for candidate in remaining:
            if not prefix or (prefix[-1].place_id, candidate.place_id) in edges:
                result = walk(
                    (*prefix, candidate),
                    tuple(item for item in remaining if item.place_id != candidate.place_id),
                )
                if result is not None:
                    return result
        return None

    return walk((), tuple(places))


class DeterministicItineraryOptimizer:
    """Select candidates with CP-SAT, then schedule against verified routes and hours."""

    def solve(
        self,
        *,
        request: TripRequest,
        destination: DestinationIntelArtifact,
        mobility: MobilityLodgingArtifact,
        route_matrix: Sequence[Route],
        run_id: str,
        created_at: datetime,
    ) -> PlanCandidatesArtifact:
        if destination.run_id != run_id or mobility.run_id != run_id:
            raise PlanningInfeasibleError("run_mismatch", "input artifacts belong to another run")
        excluded = set(request.hard_constraints.excluded_places)
        candidates = tuple(place for place in destination.places if place.name not in excluded)
        appointment_names = {
            appointment.place_name for appointment in request.hard_constraints.fixed_appointments
        }
        if len(appointment_names) != len(request.hard_constraints.fixed_appointments):
            raise PlanningInfeasibleError(
                "duplicate_fixed_place",
                "multiple fixed appointments at the same place are not supported in one run",
            )
        required = set(request.hard_constraints.must_visit) | appointment_names
        available_names = {place.name for place in candidates}
        if missing := required - available_names:
            raise PlanningInfeasibleError(
                "missing_must_visit",
                f"required places are unavailable: {sorted(missing)}",
            )
        if len(candidates) < request.duration_days:
            raise PlanningInfeasibleError(
                "insufficient_candidates",
                "at least one normalized candidate is required for each travel day",
            )

        plans = tuple(
            self._build_variant(
                strategy=strategy,
                request=request,
                candidates=candidates,
                routes=tuple(route_matrix),
                claims=destination.claims,
                evidence=destination.evidence,
            )
            for strategy in PlanStrategy
        )
        return PlanCandidatesArtifact(
            artifact_id="artifact_plan_candidates",
            run_id=run_id,
            producer_agent="itinerary_planner",
            plans=plans,
            created_at=created_at,
        )

    def _build_variant(
        self,
        *,
        strategy: PlanStrategy,
        request: TripRequest,
        candidates: tuple[Place, ...],
        routes: tuple[Route, ...],
        claims: tuple[Claim, ...],
        evidence: Sequence[Evidence],
    ) -> ItineraryVersion:
        model = cp_model.CpModel()
        selected = [model.new_bool_var(f"select_{index}") for index in range(len(candidates))]
        required_names = set(request.hard_constraints.must_visit) | {
            appointment.place_name for appointment in request.hard_constraints.fixed_appointments
        }
        required_count = sum(place.name in required_names for place in candidates)
        minimum_count = max(request.duration_days, required_count)
        if strategy in (PlanStrategy.ECONOMY, PlanStrategy.RELAXED):
            model.add(sum(selected) == minimum_count)
        else:
            model.add(sum(selected) >= minimum_count)

        for index, place in enumerate(candidates):
            if place.name in required_names:
                model.add(selected[index] == 1)

        known_upper_cents: list[int] = []
        for place in candidates:
            if place.price.kind is CostKind.UNKNOWN:
                if request.budget.policy is BudgetPolicy.HARD:
                    raise PlanningInfeasibleError(
                        "budget_unverifiable",
                        f"hard budget cannot accept unknown price for {place.name}",
                    )
                known_upper_cents.append(0)
            else:
                assert place.price.upper is not None
                known_upper_cents.append(int(place.price.upper * 100))
        if request.budget.policy is BudgetPolicy.HARD:
            model.add(
                sum(selected[index] * cost for index, cost in enumerate(known_upper_cents))
                <= int(request.budget.total * 100)
            )

        if strategy is PlanStrategy.BALANCED:
            weights = [10_000 - index for index in range(len(candidates))]
        elif strategy is PlanStrategy.ECONOMY:
            weights = [-cost for cost in known_upper_cents]
        else:
            weights = [-place.visit_duration_estimate.minimum_minutes for place in candidates]
        model.maximize(sum(selected[index] * weight for index, weight in enumerate(weights)))
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise PlanningInfeasibleError(
                "hard_constraints_infeasible",
                f"{strategy.value} variant cannot satisfy the hard constraints",
            )
        chosen = tuple(
            place for index, place in enumerate(candidates) if solver.value(selected[index])
        )
        day_places = self._partition(chosen, request)
        days = tuple(
            self._schedule_day(
                trip_date=request.start_date + timedelta(days=day_index),
                places=places,
                routes=routes,
                claims=claims,
                request=request,
                strategy=strategy,
            )
            for day_index, places in enumerate(day_places)
        )
        total = _cost_total([day.daily_cost for day in days], request.budget.currency)
        unknown = total.kind is CostKind.UNKNOWN
        plan = ItineraryVersion(
            plan_id=f"plan_{strategy.value}",
            title={
                PlanStrategy.BALANCED: "均衡覆盖方案",
                PlanStrategy.ECONOMY: "预算优先方案",
                PlanStrategy.RELAXED: "轻松节奏方案",
            }[strategy],
            strategy=strategy,
            days=days,
            total_cost=total,
            score_breakdown=self._score(strategy, unknown),
            is_executable=not unknown,
            unresolved_risks=("存在未知费用，执行前必须确认。",) if unknown else (),
        )
        report = validate_itinerary(
            plan,
            request,
            places=candidates,
            routes=routes,
            claims=claims,
            evidence=evidence,
        )
        if not report.passed:
            codes = ", ".join(issue.code.value for issue in report.errors)
            raise PlanningInfeasibleError(
                "deterministic_validation_failed",
                f"generated {strategy.value} variant failed: {codes}",
            )
        return plan

    @staticmethod
    def _partition(
        places: tuple[Place, ...], request: TripRequest
    ) -> tuple[tuple[Place, ...], ...]:
        buckets: list[list[Place]] = [[] for _ in range(request.duration_days)]
        by_name = {place.name: place for place in places}
        assigned: set[str] = set()
        for appointment in request.hard_constraints.fixed_appointments:
            day_index = (appointment.date - request.start_date).days
            place = by_name[appointment.place_name]
            buckets[day_index].append(place)
            assigned.add(place.place_id)
        for place in (item for item in places if item.place_id not in assigned):
            target = min(
                range(request.duration_days),
                key=lambda index: (len(buckets[index]), index),
            )
            buckets[target].append(place)
        return tuple(tuple(bucket) for bucket in buckets)

    def _schedule_day(
        self,
        *,
        trip_date: date,
        places: tuple[Place, ...],
        routes: tuple[Route, ...],
        claims: tuple[Claim, ...],
        request: TripRequest,
        strategy: PlanStrategy,
    ) -> DailyItinerary:
        ordered = _route_order(places, routes)
        if ordered is None:
            raise PlanningInfeasibleError(
                "route_matrix_incomplete",
                "selected places cannot be connected by the supplied route matrix",
            )
        route_by_edge = {
            (route.origin_place_id, route.destination_place_id): route for route in routes
        }
        timezone = ZoneInfo(request.timezone)
        window = request.hard_constraints.daily_time_window
        cursor = datetime.combine(trip_date, window.start, tzinfo=timezone)
        items: list[ItineraryItem] = []
        previous: Place | None = None
        for place in ordered:
            route = None if previous is None else route_by_edge[(previous.place_id, place.place_id)]
            if route is not None:
                cursor += timedelta(minutes=route.duration_minutes)
            opening = self._opening_period(place, trip_date)
            if opening is None:
                raise PlanningInfeasibleError(
                    "closed_place",
                    f"{place.name} has no verified opening interval on {trip_date}",
                )
            opens_at = datetime.combine(trip_date, opening.opens, tzinfo=timezone)
            closes_at = datetime.combine(trip_date, opening.closes, tzinfo=timezone)
            appointment = next(
                (
                    item
                    for item in request.hard_constraints.fixed_appointments
                    if item.date == trip_date and item.place_name == place.name
                ),
                None,
            )
            if appointment is not None:
                start_at = datetime.combine(trip_date, appointment.start, tzinfo=timezone)
                end_at = datetime.combine(trip_date, appointment.end, tzinfo=timezone)
                if start_at < cursor:
                    raise PlanningInfeasibleError(
                        "fixed_appointment_conflict",
                        f"travel or an earlier activity overlaps {appointment.title}",
                    )
            else:
                start_at = max(cursor, opens_at)
                duration = (
                    place.visit_duration_estimate.maximum_minutes
                    if strategy is PlanStrategy.BALANCED
                    else place.visit_duration_estimate.minimum_minutes
                )
                end_at = start_at + timedelta(minutes=duration)
            day_end = datetime.combine(trip_date, window.end, tzinfo=timezone)
            if start_at < opens_at or end_at > closes_at or end_at > day_end:
                raise PlanningInfeasibleError(
                    "time_window_infeasible",
                    f"{place.name} cannot fit its opening and daily time windows",
                )
            related_claims = tuple(claim for claim in claims if claim.subject_ref == place.place_id)
            evidence_ids = list(place.evidence_ids)
            if route is not None:
                evidence_ids.extend(route.evidence_ids)
            items.append(
                ItineraryItem(
                    plan_item_id=f"item_{strategy.value}_{place.place_id}",
                    start_at=start_at,
                    end_at=end_at,
                    place_id=place.place_id,
                    activity=f"游览{place.name}",
                    estimated_cost=place.price,
                    route_from_previous_id=None if route is None else route.route_id,
                    travel_mode_from_previous=None if route is None else route.mode,
                    travel_minutes_from_previous=None if route is None else route.duration_minutes,
                    claim_ids=tuple(claim.claim_id for claim in related_claims),
                    evidence_ids=tuple(dict.fromkeys(evidence_ids)),
                )
            )
            cursor = end_at
            previous = place
        activity_minutes = sum(
            int((item.end_at - item.start_at).total_seconds() // 60) for item in items
        )
        if activity_minutes > request.hard_constraints.max_activity_minutes_per_day:
            raise PlanningInfeasibleError(
                "daily_activity_limit_exceeded",
                f"{trip_date} exceeds the maximum activity minutes",
            )
        daily_cost = _cost_total([item.estimated_cost for item in items], request.budget.currency)
        return DailyItinerary(
            date=trip_date,
            timezone=request.timezone,
            items=tuple(items),
            daily_cost=daily_cost,
        )

    @staticmethod
    def _opening_period(place: Place, trip_date: date) -> OpeningPeriod | None:
        if place.opening_hours is None or trip_date in place.opening_hours.special_closures:
            return None
        weekday = Weekday(trip_date.strftime("%A").lower())
        return next(
            (period for period in place.opening_hours.periods if period.weekday is weekday),
            None,
        )

    @staticmethod
    def _score(strategy: PlanStrategy, unknown_cost: bool) -> ScoreBreakdown:
        preference = {
            PlanStrategy.BALANCED: 0.9,
            PlanStrategy.ECONOMY: 0.75,
            PlanStrategy.RELAXED: 0.8,
        }[strategy]
        return ScoreBreakdown(
            feasibility=1,
            preference_match=preference,
            budget_fit=0.5 if unknown_cost else 1,
            risk_resilience=0.7 if unknown_cost else 0.9,
            evidence_quality=1,
        )


class ItineraryPlanningAgent(BaseAgent[PlanCandidatesArtifact]):
    """Use only deterministic planning tools and publish typed plan candidates."""

    def __init__(self, model_gateway: ModelGateway, tool_gateway: ToolGateway) -> None:
        super().__init__(
            AgentSpec(
                agent_id="itinerary_planner",
                system_prompt=(
                    "Create two or three genuinely different itinerary variants by calling only "
                    "deterministic optimizer, budget, schedule and variant tools. Never browse the "
                    "internet or invent a place, route, claim, evidence reference, cost or opening."
                ),
                output_model=PlanCandidatesArtifact,
                model_policy=ModelPolicy(
                    model_alias="itinerary-planning",
                    temperature=0,
                    max_tool_calls=6,
                ),
                tool_allowlist=PLANNING_TOOL_ALLOWLIST,
                max_steps=8,
                timeout_seconds=120,
            ),
            model_gateway,
            tool_gateway,
        )
