"""C24 fan-out/fan-in orchestration over typed Artifacts and Blackboard refs."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, cast

from pydantic import AwareDatetime, BaseModel, Field, JsonValue, model_validator

from packages.agents import (
    DestinationIntelArtifact,
    MobilityLodgingArtifact,
)
from packages.domain import ArtifactRef, PlanCandidatesArtifact, ReviewArtifact, Route, TripRequest
from packages.domain.common import DomainModel, Identifier, SchemaVersion
from packages.harness import ArtifactRecord, Blackboard

DestinationRunner = Callable[[TripRequest], Awaitable[DestinationIntelArtifact]]
MobilityRunner = Callable[[TripRequest], Awaitable[MobilityLodgingArtifact]]
RouteBuilder = Callable[
    [DestinationIntelArtifact, MobilityLodgingArtifact], Awaitable[tuple[Route, ...]]
]
PlanningRunner = Callable[
    [TripRequest, DestinationIntelArtifact, MobilityLodgingArtifact, tuple[Route, ...]],
    Awaitable[PlanCandidatesArtifact],
]
CriticRunner = Callable[
    [
        TripRequest,
        PlanCandidatesArtifact,
        DestinationIntelArtifact,
        MobilityLodgingArtifact,
        tuple[Route, ...],
    ],
    Awaitable[ReviewArtifact],
]
Clock = Callable[[], datetime]


class RouteMatrixArtifact(DomainModel):
    """Harness-owned post-merge route matrix consumed by A3 and A4."""

    schema_version: SchemaVersion = "1.0"
    artifact_id: Identifier
    run_id: Identifier
    producer_agent: Identifier = "harness"
    candidate_place_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    routes: tuple[Route, ...]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def routes_reference_merged_candidates(self) -> "RouteMatrixArtifact":
        candidates = set(self.candidate_place_ids)
        if len(candidates) != len(self.candidate_place_ids):
            raise ValueError("candidate_place_ids must be unique")
        route_ids = [route.route_id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("route matrix route IDs must be unique")
        for route in self.routes:
            if (
                route.origin_place_id not in candidates
                or route.destination_place_id not in candidates
            ):
                raise ValueError("route matrix endpoint is not a merged destination candidate")
        return self


class WorkflowSpan(DomainModel):
    """Public trace span proving timing and Artifact hand-off without private reasoning."""

    task_id: Identifier
    started_at: AwareDatetime
    ended_at: AwareDatetime
    input_artifact_refs: tuple[ArtifactRef, ...] = ()
    output_artifact_ref: ArtifactRef

    @model_validator(mode="after")
    def duration_is_positive(self) -> "WorkflowSpan":
        if self.ended_at < self.started_at:
            raise ValueError("workflow span cannot end before it starts")
        return self


class WorkflowTrace(DomainModel):
    """Stable ordered trace of the C24 task graph."""

    run_id: Identifier
    spans: Annotated[tuple[WorkflowSpan, ...], Field(min_length=5)]

    @model_validator(mode="after")
    def task_ids_are_unique(self) -> "WorkflowTrace":
        task_ids = [span.task_id for span in self.spans]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("workflow trace task IDs must be unique")
        return self


class WorkflowResult(DomainModel):
    """Typed outputs and public trace produced before final aggregation."""

    destination: DestinationIntelArtifact
    mobility: MobilityLodgingArtifact
    route_matrix: RouteMatrixArtifact
    candidates: PlanCandidatesArtifact
    review: ReviewArtifact
    trace: WorkflowTrace


@dataclass(frozen=True, slots=True)
class WorkflowServices:
    """Injected Agent/tool callables keep orchestration independent of providers."""

    destination: DestinationRunner
    mobility: MobilityRunner
    build_route_matrix: RouteBuilder
    planning: PlanningRunner
    critic: CriticRunner


class OnePlusFourWorkflow:
    """Run A1/A2 concurrently, then route matrix, A3, and A4 in dependency order."""

    def __init__(
        self,
        blackboard: Blackboard,
        services: WorkflowServices,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.blackboard = blackboard
        self.services = services
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run(self, request: TripRequest, *, run_id: str) -> WorkflowResult:
        spans: list[WorkflowSpan] = []

        async def destination_task() -> tuple[DestinationIntelArtifact, ArtifactRef, WorkflowSpan]:
            started = self.clock()
            output = await self.services.destination(request)
            self._assert_run(output.run_id, run_id, "destination")
            reference = await self._publish(
                output,
                task_id="destination_intelligence",
                artifact_type="destination_intel",
            )
            return (
                output,
                reference,
                WorkflowSpan(
                    task_id="destination_intelligence",
                    started_at=started,
                    ended_at=self.clock(),
                    output_artifact_ref=reference,
                ),
            )

        async def mobility_task() -> tuple[MobilityLodgingArtifact, ArtifactRef, WorkflowSpan]:
            started = self.clock()
            output = await self.services.mobility(request)
            self._assert_run(output.run_id, run_id, "mobility")
            reference = await self._publish(
                output,
                task_id="mobility_lodging",
                artifact_type="mobility_lodging",
            )
            return (
                output,
                reference,
                WorkflowSpan(
                    task_id="mobility_lodging",
                    started_at=started,
                    ended_at=self.clock(),
                    output_artifact_ref=reference,
                ),
            )

        destination_result, mobility_result = await asyncio.gather(
            destination_task(), mobility_task()
        )
        destination, destination_ref, destination_span = destination_result
        mobility, mobility_ref, mobility_span = mobility_result
        spans.extend((destination_span, mobility_span))

        route_started = self.clock()
        routes = await self.services.build_route_matrix(destination, mobility)
        route_matrix = RouteMatrixArtifact(
            artifact_id="artifact_route_matrix",
            run_id=run_id,
            candidate_place_ids=tuple(place.place_id for place in destination.places),
            routes=routes,
            created_at=self.clock(),
        )
        route_ref = await self._publish(
            route_matrix,
            task_id="build_route_matrix",
            artifact_type="route_matrix",
            parents=(destination_ref, mobility_ref),
        )
        spans.append(
            WorkflowSpan(
                task_id="build_route_matrix",
                started_at=route_started,
                ended_at=self.clock(),
                input_artifact_refs=(destination_ref, mobility_ref),
                output_artifact_ref=route_ref,
            )
        )

        planning_started = self.clock()
        candidates = await self.services.planning(request, destination, mobility, routes)
        self._assert_run(candidates.run_id, run_id, "planning")
        candidates_ref = await self._publish(
            candidates,
            task_id="itinerary_planning",
            artifact_type="plan_candidates",
            parents=(destination_ref, mobility_ref, route_ref),
        )
        spans.append(
            WorkflowSpan(
                task_id="itinerary_planning",
                started_at=planning_started,
                ended_at=self.clock(),
                input_artifact_refs=(destination_ref, mobility_ref, route_ref),
                output_artifact_ref=candidates_ref,
            )
        )

        critic_started = self.clock()
        review = await self.services.critic(request, candidates, destination, mobility, routes)
        self._assert_run(review.run_id, run_id, "critic")
        review_ref = await self._publish(
            review,
            task_id="critic_review",
            artifact_type="review",
            parents=(candidates_ref, destination_ref, mobility_ref, route_ref),
        )
        spans.append(
            WorkflowSpan(
                task_id="critic_review",
                started_at=critic_started,
                ended_at=self.clock(),
                input_artifact_refs=(candidates_ref, destination_ref, mobility_ref, route_ref),
                output_artifact_ref=review_ref,
            )
        )
        return WorkflowResult(
            destination=destination,
            mobility=mobility,
            route_matrix=route_matrix,
            candidates=candidates,
            review=review,
            trace=WorkflowTrace(run_id=run_id, spans=tuple(spans)),
        )

    async def _publish(
        self,
        output: BaseModel,
        *,
        task_id: str,
        artifact_type: str,
        parents: tuple[ArtifactRef, ...] = (),
    ) -> ArtifactRef:
        values = output.model_dump(mode="json")
        payload = cast(dict[str, JsonValue], values)
        record = ArtifactRecord.create(
            artifact_id=str(values["artifact_id"]),
            run_id=str(values["run_id"]),
            task_id=task_id,
            artifact_type=artifact_type,
            version=1,
            producer_agent=str(values["producer_agent"]),
            parent_artifact_ids=tuple(parent.artifact_id for parent in parents),
            payload=payload,
            created_at=self.clock(),
        )
        return await self.blackboard.write(record, expected_latest_version=0)

    @staticmethod
    def _assert_run(actual: str, expected: str, stage: str) -> None:
        if actual != expected:
            raise ValueError(f"{stage} output belongs to run {actual}, expected {expected}")
