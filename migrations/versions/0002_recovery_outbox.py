"""Add checkpoints and transactional outbox.

Revision ID: 0002_recovery_outbox
Revises: 0001_harness_core
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_recovery_outbox"
down_revision: str | None = "0001_harness_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "checkpoints",
        sa.Column("checkpoint_id", sa.String(length=120), primary_key=True),
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
            nullable=False,
        ),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("step >= 0", name="ck_checkpoints_step_nonnegative"),
        sa.UniqueConstraint("task_id", "step", name="uq_checkpoints_task_step"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("outbox_id", sa.String(length=120), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(length=120),
            sa.ForeignKey("events.event_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_outbox_attempt_nonnegative"),
    )
    op.create_index(
        "ix_outbox_publish_candidates",
        "outbox_events",
        ["published_at", "available_at", "lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_publish_candidates", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("checkpoints")
