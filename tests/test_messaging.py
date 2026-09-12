"""Tests for the C07 Harness message contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.domain import (
    ArtifactRef,
    Claim,
    ClaimStatus,
    Event,
    EventType,
    Evidence,
    Freshness,
    StoragePolicy,
    Task,
)

HASH = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)


def test_task_and_event_accept_only_registered_states_and_types() -> None:
    """A valid assignment can be serialized as a versioned event payload."""

    reference = ArtifactRef(
        artifact_id="artifact_trip_brief",
        run_id="run_beijing_001",
        artifact_type="trip_brief",
        content_hash=HASH,
    )
    task = Task(
        run_id="run_beijing_001",
        task_id="task_destination_001",
        task_type="research_destination",
        recipient_agent="destination_research",
        input_artifact_refs=(reference,),
        deadline=NOW,
        idempotency_key=HASH,
        trace_id="trace_beijing_001",
    )
    event = Event(
        event_id="event_assignment_001",
        event_type=EventType.TASK_ASSIGNED,
        run_id=task.run_id,
        task_id=task.task_id,
        correlation_id=task.run_id,
        sender="orchestrator",
        recipient=task.recipient_agent,
        created_at=NOW,
        payload={"objective": "collect_destination_evidence"},
        trace_id=task.trace_id,
    )

    assert event.event_type is EventType.TASK_ASSIGNED
    assert Event.model_validate_json(event.model_dump_json()) == event


def test_invalid_task_state_is_rejected() -> None:
    """Free-form lifecycle states must not enter persistence."""

    with pytest.raises(ValidationError, match="Input should be"):
        Task(
            run_id="run_beijing_001",
            task_id="task_destination_001",
            task_type="research_destination",
            recipient_agent="destination_research",
            state="almost_done",  # type: ignore[arg-type]
            deadline=NOW,
            idempotency_key=HASH,
            trace_id="trace_beijing_001",
        )


def test_missing_required_event_field_is_rejected() -> None:
    """Events without trace identifiers are not auditable and must fail."""

    with pytest.raises(ValidationError, match="trace_id"):
        Event.model_validate(
            {
                "event_id": "event_assignment_001",
                "event_type": "task.assigned",
                "run_id": "run_beijing_001",
                "correlation_id": "run_beijing_001",
                "sender": "orchestrator",
                "created_at": NOW,
            }
        )


def test_claim_resolution_requires_evidence() -> None:
    """A verified claim cannot exist without at least one evidence reference."""

    with pytest.raises(ValidationError, match="requires evidence"):
        Claim(
            claim_id="claim_opening_001",
            subject_ref="place_palace_001",
            predicate="opening_hours",
            value="08:30-17:00",
            status=ClaimStatus.VERIFIED,
        )


def test_evidence_requires_timezone_and_field_paths() -> None:
    """Evidence timestamps and field coverage must be explicit."""

    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="evidence_palace_001",
            source_name="synthetic official fixture",
            source_url_or_provider_id="fixture:palace",
            provider="fixture",
            retrieved_at=datetime(2026, 9, 12, 8, 0),
            field_paths=(),
            freshness=Freshness.FRESH,
            confidence=1,
            storage_policy=StoragePolicy.FULL_LICENSED,
        )
