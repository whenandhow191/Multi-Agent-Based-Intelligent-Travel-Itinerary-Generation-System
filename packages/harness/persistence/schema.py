"""SQLAlchemy Core schema for durable Harness state."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
)

from packages.domain import RunState, TaskState

metadata = MetaData()


def _allowed_values(enum_type: type[RunState] | type[TaskState]) -> str:
    return ", ".join(f"'{item.value}'" for item in enum_type)


runs = Table(
    "runs",
    metadata,
    Column("run_id", String(120), primary_key=True),
    Column("state", String(32), nullable=False),
    Column("request_schema_version", String(16), nullable=False, server_default="1.0"),
    Column("revision", Integer, nullable=False, server_default="0"),
    Column("trace_id", String(120), nullable=False),
    Column("cancellation_requested", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(f"state IN ({_allowed_values(RunState)})", name="ck_runs_state"),
)

tasks = Table(
    "tasks",
    metadata,
    Column("task_id", String(120), primary_key=True),
    Column("run_id", String(120), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
    Column("task_type", String(120), nullable=False),
    Column("recipient_agent", String(120), nullable=False),
    Column("state", String(32), nullable=False),
    Column("priority", Integer, nullable=False, server_default="0"),
    Column("attempt", Integer, nullable=False, server_default="0"),
    Column("max_attempts", Integer, nullable=False, server_default="3"),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("deadline", DateTime(timezone=True), nullable=False),
    Column("lease_owner", String(120), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("heartbeat_at", DateTime(timezone=True), nullable=True),
    Column("idempotency_key", String(71), nullable=False),
    Column("last_error_code", String(120), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(f"state IN ({_allowed_values(TaskState)})", name="ck_tasks_state"),
    CheckConstraint("attempt >= 0", name="ck_tasks_attempt_nonnegative"),
    CheckConstraint("max_attempts BETWEEN 1 AND 10", name="ck_tasks_max_attempts"),
    UniqueConstraint("run_id", "idempotency_key", name="uq_tasks_run_idempotency"),
)

task_dependencies = Table(
    "task_dependencies",
    metadata,
    Column(
        "task_id",
        String(120),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "depends_on_task_id",
        String(120),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependencies_not_self"),
)

events = Table(
    "events",
    metadata,
    Column("event_id", String(120), primary_key=True),
    Column("run_id", String(120), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
    Column(
        "task_id",
        String(120),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=True,
    ),
    Column("event_type", String(64), nullable=False),
    Column("idempotency_key", String(71), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("run_id", "idempotency_key", name="uq_events_run_idempotency"),
)

Index(
    "ix_tasks_lease_candidates",
    tasks.c.state,
    tasks.c.available_at,
    tasks.c.lease_until,
    tasks.c.priority,
)
Index("ix_events_run_created", events.c.run_id, events.c.created_at)

checkpoints = Table(
    "checkpoints",
    metadata,
    Column("checkpoint_id", String(120), primary_key=True),
    Column("run_id", String(120), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
    Column(
        "task_id",
        String(120),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("step", Integer, nullable=False),
    Column("context", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("step >= 0", name="ck_checkpoints_step_nonnegative"),
    UniqueConstraint("task_id", "step", name="uq_checkpoints_task_step"),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("outbox_id", String(120), primary_key=True),
    Column(
        "event_id",
        String(120),
        ForeignKey("events.event_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("topic", String(120), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("lease_owner", String(120), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("attempt", Integer, nullable=False, server_default="0"),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("attempt >= 0", name="ck_outbox_attempt_nonnegative"),
)

Index(
    "ix_outbox_publish_candidates",
    outbox_events.c.published_at,
    outbox_events.c.available_at,
    outbox_events.c.lease_until,
)
