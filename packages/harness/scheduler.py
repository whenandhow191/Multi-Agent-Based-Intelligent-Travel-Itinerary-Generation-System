"""Validated DAG scheduling with bounded concurrency, retries, cancellation, and timeouts."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, JsonValue, TypeAdapter

from packages.domain import TaskState
from packages.domain.common import DomainModel, Identifier, NonEmptyText


class TaskExecutionError(RuntimeError):
    """Base operation failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RetryableTaskError(TaskExecutionError):
    """Failure that may be attempted again within the node limit."""


class PermanentTaskError(TaskExecutionError):
    """Failure that must not be retried."""


class TaskAttemptContext(DomainModel):
    """Deterministic metadata supplied to one task operation attempt."""

    run_id: Identifier
    task_id: Identifier
    attempt: Annotated[int, Field(ge=1)]


TaskOperation = Callable[[TaskAttemptContext], Awaitable[JsonValue]]
TASK_ID_ADAPTER = TypeAdapter(Identifier)


class _TaskCancelledError(RuntimeError):
    """Internal signal used to turn an interrupted attempt into a terminal outcome."""


@dataclass(frozen=True, slots=True)
class TaskNode:
    """One executable DAG node and its local failure limits."""

    task_id: str
    operation: TaskOperation
    dependency_ids: tuple[str, ...] = ()
    timeout_seconds: float = 60
    max_attempts: int = 1

    def __post_init__(self) -> None:
        TASK_ID_ADAPTER.validate_python(self.task_id)
        if self.timeout_seconds <= 0 or self.timeout_seconds > 1800:
            raise ValueError("task timeout_seconds must be between 0 and 1800")
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise ValueError("task max_attempts must be between 1 and 10")
        if len(self.dependency_ids) != len(set(self.dependency_ids)):
            raise ValueError("task dependencies must be unique")


class TaskDAG:
    """Validated acyclic graph with registered and bounded nodes."""

    def __init__(self, nodes: Sequence[TaskNode], *, max_nodes: int = 100) -> None:
        if not nodes:
            raise ValueError("task DAG requires at least one node")
        if len(nodes) > max_nodes:
            raise ValueError(f"task DAG exceeds maximum of {max_nodes} nodes")
        self.nodes = {node.task_id: node for node in nodes}
        if len(self.nodes) != len(nodes):
            raise ValueError("task DAG task IDs must be unique")
        self._validate_dependencies()
        self._validate_acyclic()

    def _validate_dependencies(self) -> None:
        for node in self.nodes.values():
            missing = set(node.dependency_ids) - self.nodes.keys()
            if missing:
                raise ValueError(f"task {node.task_id} has missing dependencies: {sorted(missing)}")
            if node.task_id in node.dependency_ids:
                raise ValueError(f"task {node.task_id} cannot depend on itself")

    def _validate_acyclic(self) -> None:
        remaining = {task_id: set(node.dependency_ids) for task_id, node in self.nodes.items()}
        while remaining:
            ready = {task_id for task_id, dependencies in remaining.items() if not dependencies}
            if not ready:
                raise ValueError("task DAG contains a cycle")
            for task_id in ready:
                remaining.pop(task_id)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)


class CancellationToken:
    """Cooperative token that can interrupt in-flight asyncio operations."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


class TaskOutcome(DomainModel):
    """Final status and accounting for one DAG node."""

    task_id: Identifier
    state: TaskState
    attempts: Annotated[int, Field(ge=0)]
    result: JsonValue = None
    error_code: NonEmptyText | None = None


class ScheduleResult(DomainModel):
    """Final outcomes keyed by task ID."""

    outcomes: dict[Identifier, TaskOutcome]

    @property
    def succeeded(self) -> bool:
        return all(outcome.state is TaskState.SUCCEEDED for outcome in self.outcomes.values())


class DagScheduler:
    """Run ready nodes concurrently and dependent nodes only after success."""

    def __init__(self, *, max_concurrency: int = 4, retry_delay_seconds: float = 0) -> None:
        if max_concurrency < 1 or max_concurrency > 100:
            raise ValueError("max_concurrency must be between 1 and 100")
        if retry_delay_seconds < 0 or retry_delay_seconds > 60:
            raise ValueError("retry_delay_seconds must be between 0 and 60")
        self.max_concurrency = max_concurrency
        self.retry_delay_seconds = retry_delay_seconds

    async def run(
        self,
        run_id: str,
        dag: TaskDAG,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ScheduleResult:
        """Execute a DAG to terminal outcomes without scheduling blocked descendants."""

        token = cancellation or CancellationToken()
        pending = set(dag.nodes)
        outcomes: dict[str, TaskOutcome] = {}
        semaphore = asyncio.Semaphore(self.max_concurrency)

        while pending:
            if token.cancelled:
                for task_id in sorted(pending):
                    outcomes[task_id] = TaskOutcome(
                        task_id=task_id,
                        state=TaskState.CANCELLED,
                        attempts=0,
                        error_code="run_cancelled",
                    )
                break

            blocked = {
                task_id
                for task_id in pending
                if any(
                    dependency_id in outcomes
                    and outcomes[dependency_id].state is not TaskState.SUCCEEDED
                    for dependency_id in dag.nodes[task_id].dependency_ids
                )
            }
            for task_id in sorted(blocked):
                outcomes[task_id] = TaskOutcome(
                    task_id=task_id,
                    state=TaskState.CANCELLED,
                    attempts=0,
                    error_code="dependency_failed",
                )
            pending.difference_update(blocked)

            ready = sorted(
                task_id
                for task_id in pending
                if all(
                    dependency_id in outcomes
                    and outcomes[dependency_id].state is TaskState.SUCCEEDED
                    for dependency_id in dag.nodes[task_id].dependency_ids
                )
            )
            if not ready:
                if pending:
                    raise RuntimeError("validated DAG reached an impossible scheduling deadlock")
                break

            async def execute(task_id: str) -> TaskOutcome:
                async with semaphore:
                    return await self._run_node(run_id, dag.nodes[task_id], token)

            completed = await asyncio.gather(*(execute(task_id) for task_id in ready))
            for outcome in completed:
                outcomes[outcome.task_id] = outcome
                pending.remove(outcome.task_id)

        return ScheduleResult(outcomes=outcomes)

    async def _run_node(self, run_id: str, node: TaskNode, token: CancellationToken) -> TaskOutcome:
        last_error_code = "task_failed"
        for attempt in range(1, node.max_attempts + 1):
            if token.cancelled:
                return TaskOutcome(
                    task_id=node.task_id,
                    state=TaskState.CANCELLED,
                    attempts=attempt - 1,
                    error_code="run_cancelled",
                )
            try:
                result = await self._run_attempt(run_id, node, attempt, token)
            except TimeoutError:
                last_error_code = "task_timed_out"
                if attempt == node.max_attempts:
                    return TaskOutcome(
                        task_id=node.task_id,
                        state=TaskState.TIMED_OUT,
                        attempts=attempt,
                        error_code=last_error_code,
                    )
            except RetryableTaskError as exc:
                last_error_code = exc.code
                if attempt == node.max_attempts:
                    return TaskOutcome(
                        task_id=node.task_id,
                        state=TaskState.FAILED,
                        attempts=attempt,
                        error_code=last_error_code,
                    )
            except PermanentTaskError as exc:
                return TaskOutcome(
                    task_id=node.task_id,
                    state=TaskState.FAILED,
                    attempts=attempt,
                    error_code=exc.code,
                )
            except _TaskCancelledError:
                return TaskOutcome(
                    task_id=node.task_id,
                    state=TaskState.CANCELLED,
                    attempts=attempt,
                    error_code="run_cancelled",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return TaskOutcome(
                    task_id=node.task_id,
                    state=TaskState.FAILED,
                    attempts=attempt,
                    error_code="unexpected_task_error",
                )
            else:
                return TaskOutcome(
                    task_id=node.task_id,
                    state=TaskState.SUCCEEDED,
                    attempts=attempt,
                    result=result,
                )
            if self.retry_delay_seconds:
                await asyncio.sleep(self.retry_delay_seconds)
        raise RuntimeError(f"retry loop ended unexpectedly: {last_error_code}")

    async def _run_attempt(
        self,
        run_id: str,
        node: TaskNode,
        attempt: int,
        token: CancellationToken,
    ) -> JsonValue:
        operation_task: asyncio.Future[JsonValue] = asyncio.ensure_future(
            node.operation(TaskAttemptContext(run_id=run_id, task_id=node.task_id, attempt=attempt))
        )
        cancellation_task = asyncio.create_task(token.wait())
        try:
            done, _ = await asyncio.wait(
                (operation_task, cancellation_task),
                timeout=node.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation_task in done:
                return operation_task.result()
            operation_task.cancel()
            await asyncio.gather(operation_task, return_exceptions=True)
            if cancellation_task in done:
                raise _TaskCancelledError
            raise TimeoutError
        finally:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)
