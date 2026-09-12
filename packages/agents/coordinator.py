"""Deterministic Coordinator decisions and allowlisted workflow blueprints."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from packages.domain.common import DomainModel, Identifier
from packages.domain.trip_request import ClarificationQuestion, TripRequest


class CoordinatorAction(StrEnum):
    """Top-level decisions the Harness can apply to a run."""

    DISPATCH = "dispatch"
    WAITING_USER = "waiting_user"


class DispatchBudget(DomainModel):
    """Finite orchestration limits selected before work is dispatched."""

    max_tasks: Annotated[int, Field(ge=1, le=20)] = 8
    max_parallel_tasks: Annotated[int, Field(ge=1, le=8)] = 2
    max_model_calls: Annotated[int, Field(ge=0, le=100)] = 12
    max_tool_calls: Annotated[int, Field(ge=0, le=200)] = 30
    max_revision_rounds: Annotated[int, Field(ge=0, le=2)] = 2


class TaskBlueprint(DomainModel):
    """A declarative task the Harness later materializes with an operation."""

    task_id: Identifier
    task_type: Identifier
    recipient_agent: Identifier
    dependency_ids: tuple[Identifier, ...] = ()


class CoordinatorTaskGraph(DomainModel):
    """An acyclic, allowlisted workflow proposal without executable callbacks."""

    tasks: Annotated[tuple[TaskBlueprint, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def graph_is_closed_and_acyclic(self) -> Self:
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("coordinator task IDs must be unique")
        known = set(task_ids)
        for task in self.tasks:
            missing = set(task.dependency_ids) - known
            if missing:
                raise ValueError(f"task {task.task_id} has unknown dependencies: {sorted(missing)}")
            if task.task_id in task.dependency_ids:
                raise ValueError("task cannot depend on itself")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                raise ValueError("coordinator task graph must be acyclic")
            visiting.add(task_id)
            task = next(item for item in self.tasks if item.task_id == task_id)
            for dependency_id in task.dependency_ids:
                visit(dependency_id)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)
        return self


class CoordinatorDecision(DomainModel):
    """Either a bounded dispatch graph or explicit blocking questions."""

    action: CoordinatorAction
    request_id: str
    graph: CoordinatorTaskGraph | None = None
    questions: tuple[ClarificationQuestion, ...] = ()
    budget: DispatchBudget

    @model_validator(mode="after")
    def action_matches_payload(self) -> Self:
        blocking = tuple(question for question in self.questions if question.blocking)
        if self.action is CoordinatorAction.WAITING_USER:
            if not blocking or self.graph is not None:
                raise ValueError("waiting_user requires blocking questions and no task graph")
        elif self.graph is None or blocking:
            raise ValueError("dispatch requires a task graph and no blocking questions")
        if self.graph is not None and len(self.graph.tasks) > self.budget.max_tasks:
            raise ValueError("task graph exceeds dispatch budget")
        return self


class CoordinatorBrain:
    """Build the fixed MVP workflow while leaving execution to the Harness."""

    _ALLOWED_TASKS: dict[str, str] = {
        "destination_intelligence": "destination_intelligence",
        "mobility_lodging": "mobility_lodging",
        "merge_candidates": "harness",
        "build_route_matrix": "harness",
        "itinerary_planning": "itinerary_planner",
        "critic_review": "critic",
        "finalize_plan": "coordinator",
    }

    def decide(
        self,
        request: TripRequest,
        *,
        questions: tuple[ClarificationQuestion, ...] = (),
        budget: DispatchBudget | None = None,
    ) -> CoordinatorDecision:
        """Return a deterministic decision and never synthesize travel facts."""

        dispatch_budget = budget or DispatchBudget()
        if any(question.blocking for question in questions):
            return CoordinatorDecision(
                action=CoordinatorAction.WAITING_USER,
                request_id=request.request_id,
                questions=questions,
                budget=dispatch_budget,
            )

        dependency_map = {
            "destination_intelligence": (),
            "mobility_lodging": (),
            "merge_candidates": ("destination_intelligence", "mobility_lodging"),
            "build_route_matrix": ("merge_candidates",),
            "itinerary_planning": ("build_route_matrix",),
            "critic_review": ("itinerary_planning",),
            "finalize_plan": ("critic_review",),
        }
        tasks = tuple(
            TaskBlueprint(
                task_id=task_type,
                task_type=task_type,
                recipient_agent=recipient,
                dependency_ids=dependency_map[task_type],
            )
            for task_type, recipient in self._ALLOWED_TASKS.items()
        )
        return CoordinatorDecision(
            action=CoordinatorAction.DISPATCH,
            request_id=request.request_id,
            graph=CoordinatorTaskGraph(tasks=tasks),
            questions=questions,
            budget=dispatch_budget,
        )
