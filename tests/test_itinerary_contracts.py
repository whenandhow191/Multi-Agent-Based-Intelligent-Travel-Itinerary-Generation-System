"""Tests for the C09 itinerary output contracts."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.domain import (
    CostEstimate,
    CostKind,
    FinalPlanBundle,
    HardViolation,
    IssueSeverity,
    ItineraryItem,
    ReviewArtifact,
    ReviewVerdict,
    ScoreBreakdown,
)

NOW = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)


def test_complete_example_bundle_round_trips_as_json() -> None:
    """The documented final bundle should be valid and serializable."""

    path = Path("examples/final_plan_bundle.beijing.json")
    bundle = FinalPlanBundle.model_validate_json(path.read_text(encoding="utf-8"))

    assert bundle.plans[0].score_breakdown.weighted_total == 0.955
    assert bundle.plans[0].is_executable
    assert FinalPlanBundle.model_validate_json(bundle.model_dump_json()) == bundle


def test_item_with_reversed_times_is_rejected() -> None:
    """An activity cannot end before it begins."""

    with pytest.raises(ValidationError, match="later than start"):
        ItineraryItem(
            plan_item_id="item_invalid_001",
            start_at=NOW + timedelta(hours=2),
            end_at=NOW,
            place_id="place_palace_001",
            activity="invalid synthetic activity",
            estimated_cost=CostEstimate(
                kind=CostKind.KNOWN,
                lower=Decimal("0"),
                upper=Decimal("0"),
                basis="synthetic",
            ),
            evidence_ids=("evidence_palace_001",),
        )


def test_passing_review_cannot_hide_a_hard_violation() -> None:
    """Review verdict and machine-actionable findings must agree."""

    score = ScoreBreakdown(
        feasibility=0,
        preference_match=1,
        budget_fit=1,
        risk_resilience=0,
        evidence_quality=1,
    )
    violation = HardViolation(
        code="opening_hours_conflict",
        severity=IssueSeverity.ERROR,
        plan_id="plan_balanced_001",
        plan_item_id="item_palace_001",
        evidence_ids=("evidence_palace_001",),
        message="Synthetic closure conflicts with the scheduled visit.",
    )

    with pytest.raises(ValidationError, match="pass review cannot contain"):
        ReviewArtifact(
            artifact_id="artifact_review_001",
            run_id="run_beijing_001",
            producer_agent="critic",
            reviewed_plan_ids=("plan_balanced_001",),
            verdict=ReviewVerdict.PASS,
            plan_scores={"plan_balanced_001": score},
            hard_violations=(violation,),
            created_at=NOW,
        )


def test_comparison_must_cover_every_final_plan() -> None:
    """A plan cannot be silently omitted from the comparison table."""

    bundle = FinalPlanBundle.model_validate_json(
        Path("examples/final_plan_bundle.beijing.json").read_text(encoding="utf-8")
    )
    payload = bundle.model_dump()
    payload["comparison"] = {"entries": []}

    with pytest.raises(ValidationError):
        FinalPlanBundle.model_validate(payload)
