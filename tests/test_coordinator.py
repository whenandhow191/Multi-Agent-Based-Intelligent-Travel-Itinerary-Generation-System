"""C18 acceptance tests for the deterministic Coordinator brain."""

from packages.agents import CoordinatorAction, CoordinatorBrain, DispatchBudget
from packages.domain import ClarificationQuestion
from packages.evals.fixtures import build_trip_request


def test_coordinator_builds_fixed_allowlisted_dag() -> None:
    decision = CoordinatorBrain().decide(build_trip_request())

    assert decision.action is CoordinatorAction.DISPATCH
    assert decision.graph is not None
    tasks = {task.task_id: task for task in decision.graph.tasks}
    assert tasks["destination_intelligence"].dependency_ids == ()
    assert tasks["mobility_lodging"].dependency_ids == ()
    assert tasks["merge_candidates"].dependency_ids == (
        "destination_intelligence",
        "mobility_lodging",
    )
    assert tasks["build_route_matrix"].recipient_agent == "harness"
    assert tasks["itinerary_planning"].dependency_ids == ("build_route_matrix",)
    assert tasks["critic_review"].dependency_ids == ("itinerary_planning",)
    assert tasks["finalize_plan"].dependency_ids == ("critic_review",)

    serialized = decision.model_dump_json()
    assert '"places"' not in serialized
    assert '"routes"' not in serialized
    assert '"claims"' not in serialized


def test_blocking_question_pauses_without_dispatching() -> None:
    question = ClarificationQuestion(
        question_id="question_origin_station",
        field_path="origin",
        question="请确认具体出发车站。",
        reason="城际交通为硬约束，城市级起点不足以锁定班次。",
        suggestions=("上海站", "上海虹桥站"),
    )

    decision = CoordinatorBrain().decide(build_trip_request(), questions=(question,))

    assert decision.action is CoordinatorAction.WAITING_USER
    assert decision.graph is None
    assert decision.questions == (question,)


def test_dispatch_budget_rejects_oversized_fixed_graph() -> None:
    try:
        CoordinatorBrain().decide(build_trip_request(), budget=DispatchBudget(max_tasks=6))
    except ValueError as exc:
        assert "exceeds dispatch budget" in str(exc)
    else:
        raise AssertionError("expected an oversized graph to be rejected")
