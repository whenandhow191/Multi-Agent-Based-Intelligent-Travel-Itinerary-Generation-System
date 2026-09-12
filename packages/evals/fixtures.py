"""Deterministic, purely synthetic domain fixtures for offline tests and demos."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from packages.domain import (
    Availability,
    BudgetPolicy,
    Claim,
    ClaimStatus,
    CollaborationSummary,
    Coordinate,
    CoordinateSystem,
    CostEstimate,
    CostKind,
    DailyItinerary,
    DataFreshnessSummary,
    Evidence,
    FinalPlanBundle,
    Freshness,
    HardConstraints,
    IntercityMode,
    IntercityTransport,
    ItineraryItem,
    ItineraryVersion,
    Lodging,
    OpeningPeriod,
    OpeningSchedule,
    Place,
    PlanCandidatesArtifact,
    PlanComparison,
    PlanComparisonEntry,
    PlanStrategy,
    ReviewArtifact,
    ReviewVerdict,
    Route,
    RouteMode,
    RouteQuality,
    ScoreBreakdown,
    SoftPreferences,
    StoragePolicy,
    TravelPace,
    TravelParty,
    TripBudget,
    TripRequest,
    VisitDurationEstimate,
    WeatherForecast,
    Weekday,
)
from packages.domain.common import DomainModel

FIXTURE_ZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
FIXTURE_NOW = datetime(2026, 9, 12, 8, 0, tzinfo=FIXTURE_ZONE)
TRIP_DATE = date(2026, 10, 1)


class SyntheticScenario(DomainModel):
    """Complete deterministic input/output snapshot used across later checkpoints."""

    request: TripRequest
    evidence: tuple[Evidence, ...]
    claims: tuple[Claim, ...]
    places: tuple[Place, ...]
    routes: tuple[Route, ...]
    weather: tuple[WeatherForecast, ...]
    lodging: tuple[Lodging, ...]
    transport: tuple[IntercityTransport, ...]
    candidates: PlanCandidatesArtifact
    review: ReviewArtifact
    final_bundle: FinalPlanBundle


def known_cost(amount: str, basis: str) -> CostEstimate:
    """Build an exact synthetic cost without binary floating-point conversion."""

    value = Decimal(amount)
    return CostEstimate(
        kind=CostKind.KNOWN,
        lower=value,
        upper=value,
        basis=basis,
    )


def build_trip_request() -> TripRequest:
    """Build a stable one-day Beijing request with a hard budget."""

    return TripRequest(
        request_id="request_beijing_fixture",
        origin="上海",
        destination="北京",
        start_date=TRIP_DATE,
        end_date=TRIP_DATE,
        days=1,
        timezone="Asia/Shanghai",
        party=TravelParty(adults=2),
        budget=TripBudget(
            total=Decimal("1000.00"),
            policy=BudgetPolicy.HARD,
            flexibility_percent=0,
        ),
        hard_constraints=HardConstraints(must_visit=("故宫博物院",)),
        soft_preferences=SoftPreferences(
            interests=("历史", "公园"),
            pace=TravelPace.BALANCED,
            transport_modes=("public_transit", "walking"),
        ),
        natural_language_notes="纯合成 Fixture，不代表真实价格或开放时间。",
    )


def build_evidence() -> tuple[Evidence, ...]:
    """Build licensed synthetic evidence for every referenced fact."""

    valid_until = datetime(2026, 10, 2, 8, 0, tzinfo=FIXTURE_ZONE)
    specifications = (
        ("evidence_palace_fixture", "fixture:palace", ("places[0]",)),
        ("evidence_park_fixture", "fixture:park", ("places[1]",)),
        ("evidence_route_fixture", "fixture:route", ("routes[0]",)),
        ("evidence_weather_fixture", "fixture:weather", ("weather[0]",)),
        ("evidence_lodging_fixture", "fixture:lodging", ("lodging[0]",)),
        ("evidence_transport_fixture", "fixture:transport", ("transport[0]",)),
    )
    return tuple(
        Evidence(
            evidence_id=evidence_id,
            source_name="project synthetic fixture",
            source_url_or_provider_id=source_id,
            provider="fixture",
            retrieved_at=FIXTURE_NOW,
            valid_until=valid_until,
            field_paths=field_paths,
            freshness=Freshness.FRESH,
            confidence=1,
            storage_policy=StoragePolicy.FULL_LICENSED,
        )
        for evidence_id, source_id, field_paths in specifications
    )


def build_claims() -> tuple[Claim, ...]:
    """Build evidence-linked synthetic opening-hour claims."""

    return (
        Claim(
            claim_id="claim_palace_open_fixture",
            subject_ref="place_palace_fixture",
            predicate="opening_hours",
            value="09:00-17:00",
            status=ClaimStatus.VERIFIED,
            evidence_ids=("evidence_palace_fixture",),
            required_hard_constraint=True,
        ),
        Claim(
            claim_id="claim_park_open_fixture",
            subject_ref="place_park_fixture",
            predicate="opening_hours",
            value="06:00-21:00",
            status=ClaimStatus.VERIFIED,
            evidence_ids=("evidence_park_fixture",),
            required_hard_constraint=True,
        ),
    )


def build_places() -> tuple[Place, ...]:
    """Build two normalized places sharing the GCJ-02 canonical system."""

    palace_coordinate = Coordinate(
        longitude=116.397026,
        latitude=39.918058,
        system=CoordinateSystem.GCJ02,
    )
    park_coordinate = Coordinate(
        longitude=116.410829,
        latitude=39.881913,
        system=CoordinateSystem.GCJ02,
    )
    palace_schedule = OpeningSchedule(
        timezone="Asia/Shanghai",
        periods=(
            OpeningPeriod(
                weekday=Weekday.THURSDAY,
                opens=time(9, 0),
                closes=time(17, 0),
            ),
        ),
    )
    park_schedule = OpeningSchedule(
        timezone="Asia/Shanghai",
        periods=(
            OpeningPeriod(
                weekday=Weekday.THURSDAY,
                opens=time(6, 0),
                closes=time(21, 0),
            ),
        ),
    )
    return (
        Place(
            place_id="place_palace_fixture",
            provider="fixture",
            provider_place_id="fixture-palace",
            name="故宫博物院",
            category="museum",
            address="合成地址：北京市东城区",
            raw_coordinate=palace_coordinate,
            canonical_coordinate=palace_coordinate,
            opening_hours=palace_schedule,
            price=known_cost("120.00", "two-adult synthetic fixture"),
            visit_duration_estimate=VisitDurationEstimate(
                minimum_minutes=120,
                maximum_minutes=180,
                method="synthetic category baseline",
                confidence=1,
            ),
            evidence_ids=("evidence_palace_fixture",),
        ),
        Place(
            place_id="place_park_fixture",
            provider="fixture",
            provider_place_id="fixture-park",
            name="天坛公园",
            category="park",
            address="合成地址：北京市东城区",
            raw_coordinate=park_coordinate,
            canonical_coordinate=park_coordinate,
            opening_hours=park_schedule,
            price=known_cost("20.00", "two-adult synthetic fixture"),
            visit_duration_estimate=VisitDurationEstimate(
                minimum_minutes=90,
                maximum_minutes=150,
                method="synthetic category baseline",
                confidence=1,
            ),
            evidence_ids=("evidence_park_fixture",),
        ),
    )


def build_routes() -> tuple[Route, ...]:
    """Build a measured synthetic route between the fixture places."""

    return (
        Route(
            route_id="route_palace_park_fixture",
            provider="fixture",
            origin_place_id="place_palace_fixture",
            destination_place_id="place_park_fixture",
            mode=RouteMode.PUBLIC_TRANSIT,
            distance_meters=5100,
            duration_minutes=30,
            quality=RouteQuality.MEASURED,
            coordinate_system=CoordinateSystem.GCJ02,
            retrieved_at=FIXTURE_NOW,
            evidence_ids=("evidence_route_fixture",),
        ),
    )


def build_weather() -> tuple[WeatherForecast, ...]:
    """Build one synthetic forecast with an explicit validity window."""

    return (
        WeatherForecast(
            weather_id="weather_beijing_fixture",
            provider="fixture",
            location=Coordinate(
                longitude=116.4,
                latitude=39.9,
                system=CoordinateSystem.GCJ02,
            ),
            forecast_date=TRIP_DATE,
            condition="晴（合成）",
            temperature_min_c=12,
            temperature_max_c=24,
            precipitation_probability=0.1,
            retrieved_at=FIXTURE_NOW,
            valid_until=datetime(2026, 10, 2, 8, 0, tzinfo=FIXTURE_ZONE),
            evidence_ids=("evidence_weather_fixture",),
        ),
    )


def build_lodging() -> tuple[Lodging, ...]:
    """Build a lodging candidate whose price is explicitly synthetic."""

    return (
        Lodging(
            lodging_id="lodging_center_fixture",
            provider="fixture",
            provider_lodging_id="fixture-lodging-center",
            name="中轴线合成酒店",
            area="东城区",
            coordinate=Coordinate(
                longitude=116.407,
                latitude=39.904,
                system=CoordinateSystem.GCJ02,
            ),
            nightly_price=known_cost("380.00", "one-room synthetic fixture"),
            evidence_ids=("evidence_lodging_fixture",),
        ),
    )


def build_transport() -> tuple[IntercityTransport, ...]:
    """Build one reference-only synthetic rail option."""

    return (
        IntercityTransport(
            transport_id="transport_rail_fixture",
            provider="fixture",
            mode=IntercityMode.RAIL,
            service_number="G-SYNTHETIC",
            origin_name="上海虹桥站",
            destination_name="北京南站",
            departure_at=datetime(2026, 10, 1, 6, 0, tzinfo=FIXTURE_ZONE),
            arrival_at=datetime(2026, 10, 1, 10, 30, tzinfo=FIXTURE_ZONE),
            price=known_cost("1100.00", "two-adult synthetic fixture"),
            availability=Availability.REFERENCE_ONLY,
            retrieved_at=FIXTURE_NOW,
            evidence_ids=("evidence_transport_fixture",),
        ),
    )


def build_score() -> ScoreBreakdown:
    """Build a stable explainable score for the synthetic plan."""

    return ScoreBreakdown(
        feasibility=1,
        preference_match=1,
        budget_fit=1,
        risk_resilience=0.8,
        evidence_quality=1,
    )


def build_plan() -> ItineraryVersion:
    """Build a two-activity plan whose totals can be recalculated exactly."""

    palace_item = ItineraryItem(
        plan_item_id="item_palace_fixture",
        start_at=datetime(2026, 10, 1, 9, 30, tzinfo=FIXTURE_ZONE),
        end_at=datetime(2026, 10, 1, 12, 0, tzinfo=FIXTURE_ZONE),
        place_id="place_palace_fixture",
        activity="参观合成故宫候选点",
        estimated_cost=known_cost("120.00", "synthetic fixture"),
        claim_ids=("claim_palace_open_fixture",),
        evidence_ids=("evidence_palace_fixture",),
    )
    park_item = ItineraryItem(
        plan_item_id="item_park_fixture",
        start_at=datetime(2026, 10, 1, 13, 0, tzinfo=FIXTURE_ZONE),
        end_at=datetime(2026, 10, 1, 15, 0, tzinfo=FIXTURE_ZONE),
        place_id="place_park_fixture",
        activity="游览合成天坛公园候选点",
        estimated_cost=known_cost("20.00", "synthetic fixture"),
        route_from_previous_id="route_palace_park_fixture",
        travel_mode_from_previous=RouteMode.PUBLIC_TRANSIT,
        travel_minutes_from_previous=30,
        claim_ids=("claim_park_open_fixture",),
        evidence_ids=("evidence_park_fixture", "evidence_route_fixture"),
    )
    total = known_cost("140.00", "deterministic fixture sum")
    return ItineraryVersion(
        plan_id="plan_balanced_fixture",
        title="北京一日合成均衡方案",
        strategy=PlanStrategy.BALANCED,
        days=(
            DailyItinerary(
                date=TRIP_DATE,
                timezone="Asia/Shanghai",
                items=(palace_item, park_item),
                daily_cost=total,
            ),
        ),
        total_cost=total,
        score_breakdown=build_score(),
        is_executable=True,
    )


def build_candidates() -> PlanCandidatesArtifact:
    """Build the deterministic A3 artifact used by offline workflows."""

    return PlanCandidatesArtifact(
        artifact_id="artifact_candidates_fixture",
        run_id="run_beijing_fixture",
        producer_agent="itinerary_planner",
        plans=(build_plan(),),
        created_at=datetime(2026, 9, 12, 8, 4, tzinfo=FIXTURE_ZONE),
    )


def build_review() -> ReviewArtifact:
    """Build a passing deterministic A4 artifact for the valid fixture."""

    return ReviewArtifact(
        artifact_id="artifact_review_fixture",
        run_id="run_beijing_fixture",
        producer_agent="critic",
        reviewed_plan_ids=("plan_balanced_fixture",),
        verdict=ReviewVerdict.PASS,
        plan_scores={"plan_balanced_fixture": build_score()},
        created_at=datetime(2026, 9, 12, 8, 5, tzinfo=FIXTURE_ZONE),
    )


def build_final_bundle() -> FinalPlanBundle:
    """Build final JSON from the same structured objects used in tests."""

    plan = build_plan()
    return FinalPlanBundle(
        run_id="run_beijing_fixture",
        request_summary=build_trip_request(),
        assumptions=("所有地点、价格、天气和交通均为项目合成数据。",),
        plans=(plan,),
        comparison=PlanComparison(
            entries=(
                PlanComparisonEntry(
                    plan_id=plan.plan_id,
                    total_cost=plan.total_cost,
                    activity_count=2,
                    commute_minutes=30,
                    free_minutes=240,
                    preference_coverage=1,
                    risk_count=0,
                    evidence_coverage=1,
                ),
            ),
        ),
        evidence=build_evidence(),
        collaboration_summary=CollaborationSummary(
            participating_agents=(
                "coordinator",
                "destination_research",
                "mobility_lodging",
                "itinerary_planner",
                "critic",
            ),
            completed_task_count=5,
            revision_rounds=0,
            notes=("结构化离线演示",),
        ),
        generated_at=datetime(2026, 9, 12, 8, 6, tzinfo=FIXTURE_ZONE),
        data_freshness=DataFreshnessSummary(
            overall=Freshness.FRESH,
            oldest_retrieved_at=FIXTURE_NOW,
        ),
    )


def build_synthetic_scenario() -> SyntheticScenario:
    """Return a complete scenario without reading network, environment, or user data."""

    return SyntheticScenario(
        request=build_trip_request(),
        evidence=build_evidence(),
        claims=build_claims(),
        places=build_places(),
        routes=build_routes(),
        weather=build_weather(),
        lodging=build_lodging(),
        transport=build_transport(),
        candidates=build_candidates(),
        review=build_review(),
        final_bundle=build_final_bundle(),
    )
