"""C26 deterministic final aggregation and fail-closed reference tests."""

from datetime import datetime

import pytest

from packages.agents import (
    DestinationIntelArtifact,
    DeterministicCritic,
    DeterministicItineraryOptimizer,
    MobilityLodgingArtifact,
)
from packages.domain import FinalPlanBundle, PlanCandidatesArtifact, ReviewArtifact, ReviewVerdict
from packages.evals.fixtures import FIXTURE_NOW, SyntheticScenario, build_synthetic_scenario
from packages.workflow import FinalAggregationError, FinalAggregator


def build_outputs() -> tuple[
    SyntheticScenario,
    DestinationIntelArtifact,
    MobilityLodgingArtifact,
    PlanCandidatesArtifact,
    ReviewArtifact,
]:
    scenario = build_synthetic_scenario()
    destination = DestinationIntelArtifact(
        artifact_id="artifact_destination_final",
        run_id="run_final_fixture",
        producer_agent="destination_intelligence",
        destination="北京",
        places=scenario.places,
        weather=scenario.weather,
        claims=scenario.claims,
        evidence=scenario.evidence[:4],
        created_at=FIXTURE_NOW,
    )
    mobility = MobilityLodgingArtifact(
        artifact_id="artifact_mobility_final",
        run_id="run_final_fixture",
        producer_agent="mobility_lodging",
        transport_options=scenario.transport,
        lodging_candidates=scenario.lodging,
        evidence=scenario.evidence[4:6],
        unknowns=("参考车次需要在官方渠道确认。",),
        manual_confirmation_required=True,
        created_at=FIXTURE_NOW,
    )
    candidates = DeterministicItineraryOptimizer().solve(
        request=scenario.request,
        destination=destination,
        mobility=mobility,
        route_matrix=scenario.routes,
        run_id="run_final_fixture",
        created_at=FIXTURE_NOW,
    )
    review = DeterministicCritic().review(
        request=scenario.request,
        candidates=candidates,
        destination=destination,
        mobility=mobility,
        route_matrix=scenario.routes,
        created_at=FIXTURE_NOW,
    )
    return scenario, destination, mobility, candidates, review


def test_final_aggregator_renders_three_plans_without_polishing_model() -> None:
    scenario, destination, mobility, candidates, review = build_outputs()
    aggregator = FinalAggregator()
    generated_at = datetime.fromisoformat("2026-09-13T11:00:00+08:00")

    first = aggregator.aggregate(
        request=scenario.request,
        candidates=candidates,
        review=review,
        destination=destination,
        mobility=mobility,
        routes=scenario.routes,
        generated_at=generated_at,
    )
    second = aggregator.aggregate(
        request=scenario.request,
        candidates=candidates,
        review=review,
        destination=destination,
        mobility=mobility,
        routes=scenario.routes,
        generated_at=generated_at,
    )

    parsed = FinalPlanBundle.model_validate_json(first.json_text)
    assert parsed == first.bundle
    assert first.json_text == second.json_text
    assert first.markdown == second.markdown
    assert len(first.bundle.plans) == 3
    assert len(first.bundle.comparison.entries) == 3
    assert "## 方案比较" in first.markdown
    assert "均衡覆盖方案" in first.markdown
    assert "预算优先方案" in first.markdown
    assert "轻松节奏方案" in first.markdown
    assert "未调用润色模型" in first.bundle.collaboration_summary.notes[0]


def test_final_aggregator_rejects_missing_evidence_even_after_claimed_pass() -> None:
    scenario, destination, mobility, candidates, review = build_outputs()
    plan = candidates.plans[0]
    day = plan.days[0]
    corrupted_item = day.items[0].model_copy(update={"evidence_ids": ("evidence_missing_final",)})
    corrupted_day = day.model_copy(update={"items": (corrupted_item, *day.items[1:])})
    corrupted_plan = plan.model_copy(update={"days": (corrupted_day,)})
    corrupted_candidates = PlanCandidatesArtifact(
        artifact_id="artifact_corrupted_final",
        run_id=candidates.run_id,
        producer_agent="itinerary_planner",
        plans=(corrupted_plan,),
        created_at=FIXTURE_NOW,
    )
    dishonest_review = ReviewArtifact(
        artifact_id="artifact_dishonest_review",
        run_id=candidates.run_id,
        producer_agent="critic",
        reviewed_plan_ids=(corrupted_plan.plan_id,),
        verdict=ReviewVerdict.PASS,
        plan_scores={corrupted_plan.plan_id: review.plan_scores[corrupted_plan.plan_id]},
        created_at=FIXTURE_NOW,
    )

    with pytest.raises(FinalAggregationError) as raised:
        FinalAggregator().aggregate(
            request=scenario.request,
            candidates=corrupted_candidates,
            review=dishonest_review,
            destination=destination,
            mobility=mobility,
            routes=scenario.routes,
            generated_at=FIXTURE_NOW,
        )

    assert raised.value.code == "deterministic_validation_failed"


def test_final_aggregator_refuses_non_passing_review() -> None:
    scenario, destination, mobility, candidates, review = build_outputs()
    revise = review.model_copy(
        update={
            "verdict": ReviewVerdict.REVISE,
            "patch_requests": (
                {
                    "target_agent": "itinerary_planner",
                    "action": "replan_fixture",
                    "plan_id": candidates.plans[0].plan_id,
                    "constraints": {},
                },
            ),
        }
    )
    with pytest.raises(FinalAggregationError, match="passing review"):
        FinalAggregator().aggregate(
            request=scenario.request,
            candidates=candidates,
            review=revise,
            destination=destination,
            mobility=mobility,
            routes=scenario.routes,
            generated_at=FIXTURE_NOW,
        )
