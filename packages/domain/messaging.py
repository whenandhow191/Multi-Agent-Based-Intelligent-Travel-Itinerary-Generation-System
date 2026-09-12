"""Versioned contracts for Harness tasks, events, artifacts, claims, and evidence."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from packages.domain.common import (
    DomainModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    Sha256Digest,
    ShortText,
    ensure_unique,
)


class RunState(StrEnum):
    """Allowed lifecycle states for an itinerary planning run."""

    CREATED = "created"
    PARSING = "parsing"
    RESEARCHING = "researching"
    PLANNING = "planning"
    REVIEWING = "reviewing"
    REVISING = "revising"
    FINALIZING = "finalizing"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class TaskState(StrEnum):
    """Allowed lifecycle states for a task leased by the Harness."""

    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_USER = "waiting_user"
    VALIDATING = "validating"
    WAITING_RETRY = "waiting_retry"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class EventType(StrEnum):
    """Auditable events emitted by the Harness and its workers."""

    RUN_CREATED = "run.created"
    PLAN_CREATED = "plan.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TOOL_CALL_PLANNED = "tool_call.planned"
    TOOL_CALL_COMPLETED = "tool_call.completed"
    ARTIFACT_PUBLISHED = "artifact.published"
    ARTIFACT_REJECTED = "artifact.rejected"
    REVIEW_VERDICT = "review.verdict"
    TASK_RETRY_SCHEDULED = "task.retry_scheduled"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TASK_TIMED_OUT = "task.timed_out"
    CLARIFICATION_REQUESTED = "clarification.requested"
    CLARIFICATION_RESOLVED = "clarification.resolved"
    RUN_COMPLETED = "run.completed"
    RUN_PARTIAL = "run.partial"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMED_OUT = "run.timed_out"


class ClaimStatus(StrEnum):
    """Three-state verification result for a factual claim."""

    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class Freshness(StrEnum):
    """Coarse freshness classification for evidence shown to users."""

    LIVE = "live"
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class StoragePolicy(StrEnum):
    """How much provider-derived data may be persisted."""

    MEMORY_ONLY = "memory_only"
    IDS_AND_METADATA = "ids_and_metadata"
    ALLOWED_FIELDS = "allowed_fields"
    FULL_LICENSED = "full_licensed"


class ArtifactRef(DomainModel):
    """Small immutable reference passed between tasks instead of large payloads."""

    artifact_id: Identifier
    run_id: Identifier
    artifact_type: ShortText
    schema_version: SchemaVersion = "1.0"
    version: Annotated[int, Field(ge=1)] = 1
    content_hash: Sha256Digest


class Task(DomainModel):
    """A versioned unit of work assigned to one registered Agent."""

    schema_version: SchemaVersion = "1.0"
    run_id: Identifier
    task_id: Identifier
    task_type: Identifier
    recipient_agent: Identifier
    state: TaskState = TaskState.PENDING
    dependency_ids: tuple[Identifier, ...] = ()
    input_artifact_refs: tuple[ArtifactRef, ...] = ()
    attempt: Annotated[int, Field(ge=0)] = 0
    deadline: AwareDatetime
    idempotency_key: Sha256Digest
    trace_id: Identifier

    @model_validator(mode="after")
    def dependencies_are_valid(self) -> Self:
        """Reject duplicate and self-referential task dependencies."""

        ensure_unique(self.dependency_ids, "dependency_ids")
        if self.task_id in self.dependency_ids:
            raise ValueError("task cannot depend on itself")
        return self


class Event(DomainModel):
    """Append-only fact used for progress, audit, replay, and projections."""

    schema_version: SchemaVersion = "1.0"
    event_id: Identifier
    event_type: EventType
    run_id: Identifier
    task_id: Identifier | None = None
    correlation_id: Identifier
    causation_id: Identifier | None = None
    sender: Identifier
    recipient: Identifier | None = None
    attempt: Annotated[int, Field(ge=0)] = 0
    created_at: AwareDatetime
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    trace_id: Identifier


class Evidence(DomainModel):
    """Source metadata supporting one or more externally visible fields."""

    evidence_id: Identifier
    source_name: ShortText
    source_url_or_provider_id: NonEmptyText
    provider: Identifier
    retrieved_at: AwareDatetime
    valid_until: AwareDatetime | None = None
    field_paths: tuple[NonEmptyText, ...]
    freshness: Freshness
    confidence: Annotated[float, Field(ge=0, le=1)]
    storage_policy: StoragePolicy = StoragePolicy.IDS_AND_METADATA

    @model_validator(mode="after")
    def validate_evidence_window_and_paths(self) -> Self:
        """Require field coverage and a validity end after retrieval."""

        if not self.field_paths:
            raise ValueError("evidence must cover at least one field path")
        if self.valid_until is not None and self.valid_until <= self.retrieved_at:
            raise ValueError("valid_until must be later than retrieved_at")
        return self


class Claim(DomainModel):
    """A factual statement separated from the evidence used to verify it."""

    claim_id: Identifier
    subject_ref: Identifier
    predicate: Identifier
    value: JsonValue
    status: ClaimStatus
    evidence_ids: tuple[Identifier, ...] = ()
    required_hard_constraint: bool = False

    @model_validator(mode="after")
    def resolved_claims_have_evidence(self) -> Self:
        """Verified and contradicted claims must explain their conclusion."""

        ensure_unique(self.evidence_ids, "evidence_ids")
        if self.status is not ClaimStatus.UNKNOWN and not self.evidence_ids:
            raise ValueError("verified or contradicted claim requires evidence")
        return self


def utc_now() -> datetime:
    """Compatibility helper reserved for callers that need explicit event timestamps."""

    return datetime.now().astimezone()
