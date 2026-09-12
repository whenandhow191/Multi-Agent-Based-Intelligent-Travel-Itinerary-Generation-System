"""Create runs, tasks, dependencies, and idempotent events.

Revision ID: 0001_harness_core
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_harness_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATES = (
    "created",
    "parsing",
    "researching",
    "planning",
    "reviewing",
    "revising",
    "finalizing",
    "waiting_user",
    "completed",
    "partial",
    "failed",
    "cancelled",
    "timed_out",
)
TASK_STATES = (
    "pending",
    "leased",
    "running",
    "waiting_tool",
    "waiting_user",
    "validating",
    "waiting_retry",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
)


def sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=120), primary_key=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("request_schema_version", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(length=120), nullable=False),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"state IN ({sql_values(RUN_STATES)})", name="ck_runs_state"),
    )
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.String(length=120), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=120),
            sa.ForeignKey("runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_type", sa.String(length=120), nullable=False),
        sa.Column("recipient_agent", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=71), nullable=False),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"state IN ({sql_values(TASK_STATES)})", name="ck_tasks_state"),
        sa.CheckConstraint("attempt >= 0", name="ck_tasks_attempt_nonnegative"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 10", name="ck_tasks_max_attempts"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_tasks_run_idempotency"),
    )
    op.create_index(
        "ix_tasks_lease_candidates",
        "tasks",
        ["state", "available_at", "lease_until", "priority"],
    )
    op.create_table(
        "task_dependencies",
        sa.Column(
            "task_id",
            sa.String(length=120),
            sa.ForeignKey("tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "depends_on_task_id",
            sa.String(length=120),
            sa.ForeignKey("tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependencies_not_self"),
    )
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=120), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=120),
            sa.ForeignKey("runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(length=120),
            sa.ForeignKey("tasks.task_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_events_run_idempotency"),
    )
    op.create_index("ix_events_run_created", "events", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_events_run_created", table_name="events")
    op.drop_table("events")
    op.drop_table("task_dependencies")
    op.drop_index("ix_tasks_lease_candidates", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("runs")
