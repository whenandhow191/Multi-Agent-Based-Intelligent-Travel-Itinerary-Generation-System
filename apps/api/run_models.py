"""Public API contracts for creating and inspecting trip runs."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.domain import FinalPlanBundle, PlanComparison, TripRequest


class TripRunState(StrEnum):
    """User-visible lifecycle, independent from worker-internal task states."""

    RUNNING = "running"
    WAITING_USER = "waiting_user"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TripRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: TripRequest


class ClarificationAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: Annotated[str, Field(min_length=1, max_length=120)]
    answer: Annotated[str, Field(min_length=1, max_length=2_000)]


class TripRunResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    state: TripRunState
    fixture_mode: bool
    request: TripRequest
    created_at: AwareDatetime
    updated_at: AwareDatetime
    result_available: bool
    clarification_answers: tuple[ClarificationAnswer, ...] = ()
    current_version: Annotated[int, Field(ge=1)] = 1


class TripRunCreated(BaseModel):
    """Creation response; the anonymous token is returned only to this client."""

    model_config = ConfigDict(extra="forbid")

    run: TripRunResource
    access_token: str


class RunProgressEvent(BaseModel):
    """Public progress without prompts, private reasoning or secrets."""

    model_config = ConfigDict(extra="forbid")

    sequence: Annotated[int, Field(ge=1)]
    event_type: str
    task_id: str | None = None
    agent_id: str | None = None
    state: str
    message: str
    occurred_at: AwareDatetime
    artifact_ids: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    estimated_cost_microunits: Annotated[int, Field(ge=0)] = 0


class TripRunResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    version: Annotated[int, Field(ge=1)]
    bundle: FinalPlanBundle
    markdown: str


class PlanComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    comparison: PlanComparison
    generated_at: datetime
