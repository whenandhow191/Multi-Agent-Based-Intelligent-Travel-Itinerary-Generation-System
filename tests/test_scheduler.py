"""C15 DAG validation, concurrency, dependency, cancellation, timeout, and retry tests."""

import asyncio

import pytest

from packages.domain import TaskState
from packages.harness.scheduler import (
    CancellationToken,
    DagScheduler,
    RetryableTaskError,
    TaskAttemptContext,
    TaskDAG,
    TaskNode,
)


def test_independent_tasks_run_concurrently_before_dependent_task() -> None:
    async def scenario() -> None:
        both_started = asyncio.Event()
        active = 0
        order: list[str] = []

        async def independent(context: TaskAttemptContext) -> str:
            nonlocal active
            active += 1
            order.append(f"start:{context.task_id}")
            if active == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.2)
            order.append(f"end:{context.task_id}")
            active -= 1
            return context.task_id

        async def dependent(context: TaskAttemptContext) -> str:
            assert order.count("end:task_first_fixture") == 1
            assert order.count("end:task_second_fixture") == 1
            order.append(f"start:{context.task_id}")
            return "complete"

        dag = TaskDAG(
            (
                TaskNode("task_first_fixture", independent),
                TaskNode("task_second_fixture", independent),
                TaskNode(
                    "task_dependent_fixture",
                    dependent,
                    dependency_ids=("task_first_fixture", "task_second_fixture"),
                ),
            )
        )
        result = await DagScheduler(max_concurrency=2).run("run_dag_fixture", dag)

        assert result.succeeded is True
        assert order[-1] == "start:task_dependent_fixture"

    asyncio.run(scenario())


def test_retry_is_finite_and_eventually_succeeds() -> None:
    attempts: list[int] = []

    async def flaky(context: TaskAttemptContext) -> str:
        attempts.append(context.attempt)
        if context.attempt < 3:
            raise RetryableTaskError("provider_busy", "try again")
        return "recovered"

    result = asyncio.run(
        DagScheduler().run(
            "run_retry_fixture",
            TaskDAG((TaskNode("task_retry_fixture", flaky, max_attempts=3),)),
        )
    )

    assert attempts == [1, 2, 3]
    assert result.outcomes["task_retry_fixture"].state is TaskState.SUCCEEDED


def test_timeout_is_terminal_after_the_configured_attempts() -> None:
    async def slow(context: TaskAttemptContext) -> None:
        del context
        await asyncio.sleep(0.1)

    result = asyncio.run(
        DagScheduler().run(
            "run_timeout_fixture",
            TaskDAG(
                (
                    TaskNode(
                        "task_timeout_fixture",
                        slow,
                        timeout_seconds=0.01,
                        max_attempts=2,
                    ),
                )
            ),
        )
    )

    outcome = result.outcomes["task_timeout_fixture"]
    assert outcome.state is TaskState.TIMED_OUT
    assert outcome.attempts == 2


def test_cancellation_interrupts_an_in_flight_task() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        token = CancellationToken()

        async def waiting(context: TaskAttemptContext) -> None:
            del context
            started.set()
            await asyncio.sleep(10)

        scheduler_task = asyncio.create_task(
            DagScheduler().run(
                "run_cancel_fixture",
                TaskDAG((TaskNode("task_cancel_fixture", waiting),)),
                cancellation=token,
            )
        )
        await started.wait()
        token.cancel()
        result = await asyncio.wait_for(scheduler_task, timeout=0.2)

        assert result.outcomes["task_cancel_fixture"].state is TaskState.CANCELLED
        assert result.outcomes["task_cancel_fixture"].error_code == "run_cancelled"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "nodes",
    (
        (
            TaskNode("task_a_fixture", lambda context: asyncio.sleep(0)),
            TaskNode(
                "task_b_fixture",
                lambda context: asyncio.sleep(0),
                dependency_ids=("task_missing_fixture",),
            ),
        ),
        (
            TaskNode(
                "task_a_fixture",
                lambda context: asyncio.sleep(0),
                dependency_ids=("task_b_fixture",),
            ),
            TaskNode(
                "task_b_fixture",
                lambda context: asyncio.sleep(0),
                dependency_ids=("task_a_fixture",),
            ),
        ),
    ),
)
def test_invalid_dag_is_rejected(nodes: tuple[TaskNode, ...]) -> None:
    with pytest.raises(ValueError):
        TaskDAG(nodes)
