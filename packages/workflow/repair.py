"""C25 targeted repair, dependency invalidation, and clarification recovery."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue

from packages.domain import ArtifactRef, PatchRequest, ReviewArtifact, ReviewVerdict
from packages.domain.common import DomainModel, Identifier
from packages.harness import ArtifactRecord, Blackboard

TASK_ORDER = (
    "destination_intelligence",
    "mobility_lodging",
    "build_route_matrix",
    "itinerary_planning",
    "critic_review",
)
TASK_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "destination_intelligence": (),
    "mobility_lodging": (),
    "build_route_matrix": ("destination_intelligence", "mobility_lodging"),
    "itinerary_planning": ("build_route_matrix",),
    "critic_review": ("itinerary_planning",),
}
TASK_ARTIFACT_TYPES = {
    "destination_intelligence": "destination_intel",
    "mobility_lodging": "mobility_lodging",
    "build_route_matrix": "route_matrix",
    "itinerary_planning": "plan_candidates",
    "critic_review": "review",
}
AGENT_TASKS = {
    "destination_intelligence": "destination_intelligence",
    "mobility_lodging": "mobility_lodging",
    "itinerary_planner": "itinerary_planning",
    "critic": "critic_review",
}


class RepairStatus(StrEnum):
    """Whether a repair can run or must wait for a human answer."""

    READY = "ready"
    WAITING_USER = "waiting_user"


class RevisionLimitExceededError(RuntimeError):
    """Raised instead of silently starting a third revision round."""


class RepairPlan(DomainModel):
    """Machine-executable invalidation closure for one bounded repair round."""

    run_id: Identifier
    status: RepairStatus
    completed_revision_rounds: Annotated[int, Field(ge=0, le=2)]
    next_revision_round: Annotated[int | None, Field(ge=1, le=2)] = None
    invalidated_task_ids: tuple[Identifier, ...] = ()
    patch_requests: tuple[PatchRequest, ...]
    clarification_fields: tuple[str, ...] = ()


class RepairExecutionContext(DomainModel):
    """Inputs visible to one repair handler without other Agent conversations."""

    run_id: Identifier
    task_id: Identifier
    revision_round: Annotated[int, Field(ge=1, le=2)]
    patch_requests: tuple[PatchRequest, ...]
    upstream_refs: tuple[ArtifactRef, ...]


class RepairExecutionResult(DomainModel):
    """Published replacement versions in deterministic dependency order."""

    plan: RepairPlan
    published_refs: tuple[ArtifactRef, ...]


RepairHandler = Callable[[RepairExecutionContext], Awaitable[ArtifactRecord]]


@dataclass(frozen=True, slots=True)
class TargetedRepairController:
    """Compute invalidation closure and publish only affected Artifact versions."""

    blackboard: Blackboard
    max_revision_rounds: int = 2

    def plan(self, review: ReviewArtifact, *, completed_revision_rounds: int) -> RepairPlan:
        if review.verdict is not ReviewVerdict.REVISE or not review.patch_requests:
            raise ValueError("targeted repair requires a revise review with patch requests")
        if completed_revision_rounds >= self.max_revision_rounds:
            raise RevisionLimitExceededError("maximum of two revision rounds reached")

        if any(patch.target_agent == "coordinator" for patch in review.patch_requests):
            fields = tuple(
                dict.fromkeys(
                    str(patch.constraints.get("field_path", "trip_request"))
                    for patch in review.patch_requests
                    if patch.target_agent == "coordinator"
                )
            )
            return RepairPlan(
                run_id=review.run_id,
                status=RepairStatus.WAITING_USER,
                completed_revision_rounds=completed_revision_rounds,
                patch_requests=review.patch_requests,
                clarification_fields=fields,
            )

        seeds = {
            AGENT_TASKS[patch.target_agent]
            for patch in review.patch_requests
            if patch.target_agent in AGENT_TASKS
        }
        if not seeds:
            raise ValueError("review contains no registered repair target")
        invalidated = self._dependency_closure(seeds)
        return RepairPlan(
            run_id=review.run_id,
            status=RepairStatus.READY,
            completed_revision_rounds=completed_revision_rounds,
            next_revision_round=completed_revision_rounds + 1,
            invalidated_task_ids=invalidated,
            patch_requests=review.patch_requests,
        )

    def resume_after_clarification(
        self,
        waiting: RepairPlan,
        *,
        resolved_fields: Mapping[str, JsonValue],
    ) -> RepairPlan:
        if waiting.status is not RepairStatus.WAITING_USER:
            raise ValueError("only a waiting repair can be resumed")
        if not resolved_fields:
            raise ValueError("clarification answers cannot be empty")
        if waiting.completed_revision_rounds >= self.max_revision_rounds:
            raise RevisionLimitExceededError("maximum of two revision rounds reached")

        field_names = tuple(resolved_fields)
        if all(name.startswith("lodging") or name.startswith("transport") for name in field_names):
            seeds = {"mobility_lodging"}
        elif all(name.startswith("destination") for name in field_names):
            seeds = {"destination_intelligence"}
        else:
            seeds = {"destination_intelligence", "mobility_lodging"}
        return RepairPlan(
            run_id=waiting.run_id,
            status=RepairStatus.READY,
            completed_revision_rounds=waiting.completed_revision_rounds,
            next_revision_round=waiting.completed_revision_rounds + 1,
            invalidated_task_ids=self._dependency_closure(seeds),
            patch_requests=waiting.patch_requests,
        )

    async def execute(
        self,
        plan: RepairPlan,
        handlers: Mapping[str, RepairHandler],
    ) -> RepairExecutionResult:
        if plan.status is not RepairStatus.READY or plan.next_revision_round is None:
            raise ValueError("repair plan is not ready")
        published: list[ArtifactRef] = []
        latest_by_task: dict[str, ArtifactRef] = {}
        for task_id in plan.invalidated_task_ids:
            handler = handlers.get(task_id)
            if handler is None:
                raise ValueError(f"missing repair handler for {task_id}")
            dependencies = TASK_DEPENDENCIES[task_id]
            upstream_refs: list[ArtifactRef] = []
            for dependency in dependencies:
                if dependency in latest_by_task:
                    upstream_refs.append(latest_by_task[dependency])
                    continue
                current = await self.blackboard.latest(
                    run_id=plan.run_id,
                    task_id=dependency,
                    artifact_type=TASK_ARTIFACT_TYPES[dependency],
                )
                if current is None:
                    raise ValueError(f"missing upstream Artifact for {dependency}")
                upstream_refs.append(current.to_ref())
            context = RepairExecutionContext(
                run_id=plan.run_id,
                task_id=task_id,
                revision_round=plan.next_revision_round,
                patch_requests=tuple(
                    patch
                    for patch in plan.patch_requests
                    if AGENT_TASKS.get(patch.target_agent) == task_id
                ),
                upstream_refs=tuple(upstream_refs),
            )
            artifact = await handler(context)
            if artifact.run_id != plan.run_id or artifact.task_id != task_id:
                raise ValueError("repair handler returned an Artifact for the wrong run or task")
            latest = await self.blackboard.latest(
                run_id=artifact.run_id,
                task_id=artifact.task_id,
                artifact_type=artifact.artifact_type,
            )
            current_version = 0 if latest is None else latest.version
            if artifact.version != current_version + 1:
                raise ValueError("repair Artifact version must follow the Blackboard head")
            reference = await self.blackboard.write(
                artifact,
                expected_latest_version=current_version,
            )
            latest_by_task[task_id] = reference
            published.append(reference)
        return RepairExecutionResult(plan=plan, published_refs=tuple(published))

    @staticmethod
    def _dependency_closure(seeds: set[str]) -> tuple[str, ...]:
        invalidated = set(seeds)
        changed = True
        while changed:
            changed = False
            for task_id, dependencies in TASK_DEPENDENCIES.items():
                if task_id not in invalidated and invalidated.intersection(dependencies):
                    invalidated.add(task_id)
                    changed = True
        return tuple(task_id for task_id in TASK_ORDER if task_id in invalidated)
