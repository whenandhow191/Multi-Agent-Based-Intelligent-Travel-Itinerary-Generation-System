"""C25 targeted repair, invalidation, revision limit, and clarification tests."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from packages.domain import ArtifactRef, PatchRequest, ReviewArtifact, ReviewVerdict
from packages.evals.fixtures import build_score
from packages.harness import ArtifactRecord, MemoryBlackboard
from packages.workflow import (
    RepairStatus,
    RevisionLimitExceededError,
    TargetedRepairController,
)
from packages.workflow.repair import TASK_ARTIFACT_TYPES

NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
RUN_ID = "run_repair_fixture"


def review_for(target_agent: str, *, field_path: str | None = None) -> ReviewArtifact:
    constraints: dict[str, JsonValue] = {} if field_path is None else {"field_path": field_path}
    patch = PatchRequest(
        target_agent=target_agent,
        action="repair_fixture",
        plan_id="plan_repair_fixture",
        constraints=constraints,
    )
    return ReviewArtifact(
        artifact_id=f"artifact_review_{target_agent}",
        run_id=RUN_ID,
        producer_agent="critic",
        reviewed_plan_ids=("plan_repair_fixture",),
        verdict=ReviewVerdict.REVISE,
        plan_scores={"plan_repair_fixture": build_score()},
        patch_requests=(patch,),
        created_at=NOW,
    )


async def seed_blackboard(board: MemoryBlackboard) -> None:
    references: dict[str, ArtifactRef] = {}
    for task_id in (
        "destination_intelligence",
        "mobility_lodging",
        "build_route_matrix",
        "itinerary_planning",
        "critic_review",
    ):
        dependencies = {
            "destination_intelligence": (),
            "mobility_lodging": (),
            "build_route_matrix": (
                "destination_intelligence",
                "mobility_lodging",
            ),
            "itinerary_planning": ("build_route_matrix",),
            "critic_review": ("itinerary_planning",),
        }[task_id]
        record = ArtifactRecord.create(
            artifact_id=f"artifact_{task_id}_v1",
            run_id=RUN_ID,
            task_id=task_id,
            artifact_type=TASK_ARTIFACT_TYPES[task_id],
            version=1,
            producer_agent={
                "destination_intelligence": "destination_intelligence",
                "mobility_lodging": "mobility_lodging",
                "build_route_matrix": "harness",
                "itinerary_planning": "itinerary_planner",
                "critic_review": "critic",
            }[task_id],
            parent_artifact_ids=tuple(references[item].artifact_id for item in dependencies),
            payload={"task": task_id, "version": 1},
            created_at=NOW,
        )
        references[task_id] = await board.write(record, expected_latest_version=0)


def test_lodging_repair_only_recomputes_affected_dependency_chain() -> None:
    async def scenario() -> None:
        board = MemoryBlackboard()
        await seed_blackboard(board)
        controller = TargetedRepairController(board)
        plan = controller.plan(review_for("mobility_lodging"), completed_revision_rounds=0)

        assert plan.invalidated_task_ids == (
            "mobility_lodging",
            "build_route_matrix",
            "itinerary_planning",
            "critic_review",
        )
        assert "destination_intelligence" not in plan.invalidated_task_ids

        calls: list[str] = []

        def handler_for(task_id: str):  # type: ignore[no-untyped-def]
            async def handler(context):  # type: ignore[no-untyped-def]
                calls.append(task_id)
                return ArtifactRecord.create(
                    artifact_id=f"artifact_{task_id}_v2",
                    run_id=RUN_ID,
                    task_id=task_id,
                    artifact_type=TASK_ARTIFACT_TYPES[task_id],
                    version=2,
                    producer_agent="repair_worker",
                    parent_artifact_ids=tuple(
                        reference.artifact_id for reference in context.upstream_refs
                    ),
                    payload={"task": task_id, "version": 2},
                    created_at=NOW,
                )

            return handler

        result = await controller.execute(
            plan,
            {task_id: handler_for(task_id) for task_id in plan.invalidated_task_ids},
        )

        assert calls == list(plan.invalidated_task_ids)
        assert [reference.version for reference in result.published_refs] == [2, 2, 2, 2]
        route_lineage = await board.lineage("artifact_build_route_matrix_v2")
        lineage_ids = {record.artifact_id for record in route_lineage}
        assert "artifact_destination_intelligence_v1" in lineage_ids
        assert "artifact_mobility_lodging_v2" in lineage_ids

    asyncio.run(scenario())


def test_clarification_pauses_and_resumes_from_affected_domain() -> None:
    controller = TargetedRepairController(MemoryBlackboard())
    waiting = controller.plan(
        review_for("coordinator", field_path="lodging.area"),
        completed_revision_rounds=0,
    )

    assert waiting.status is RepairStatus.WAITING_USER
    assert waiting.invalidated_task_ids == ()

    resumed = controller.resume_after_clarification(
        waiting,
        resolved_fields={"lodging.area": "东城区"},
    )

    assert resumed.status is RepairStatus.READY
    assert resumed.next_revision_round == 1
    assert resumed.invalidated_task_ids == (
        "mobility_lodging",
        "build_route_matrix",
        "itinerary_planning",
        "critic_review",
    )


def test_third_revision_round_is_rejected() -> None:
    controller = TargetedRepairController(MemoryBlackboard())
    with pytest.raises(RevisionLimitExceededError, match="maximum of two"):
        controller.plan(review_for("itinerary_planner"), completed_revision_rounds=2)
