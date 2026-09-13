"""Add versioned Artifact Blackboard tables.

Revision ID: 0003_artifact_blackboard
Revises: 0002_recovery_outbox
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_artifact_blackboard"
down_revision: str | None = "0002_recovery_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_heads",
        sa.Column(
            "run_id",
            sa.String(length=120),
            sa.ForeignKey("runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "task_id",
            sa.String(length=120),
            sa.ForeignKey("tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("artifact_type", sa.String(length=120), primary_key=True),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("current_version >= 0", name="ck_artifact_heads_version_nonnegative"),
    )
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=120), primary_key=True),
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
        sa.Column("artifact_type", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("producer_agent", sa.String(length=120), nullable=False),
        sa.Column("content_hash", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version >= 1", name="ck_artifacts_version_positive"),
        sa.UniqueConstraint(
            "run_id",
            "task_id",
            "artifact_type",
            "version",
            name="uq_artifacts_logical_version",
        ),
    )
    op.create_index("ix_artifacts_run_type", "artifacts", ["run_id", "artifact_type"])
    op.create_table(
        "artifact_parents",
        sa.Column(
            "artifact_id",
            sa.String(length=120),
            sa.ForeignKey("artifacts.artifact_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "parent_artifact_id",
            sa.String(length=120),
            sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.CheckConstraint(
            "artifact_id <> parent_artifact_id", name="ck_artifact_parents_not_self"
        ),
    )


def downgrade() -> None:
    op.drop_table("artifact_parents")
    op.drop_index("ix_artifacts_run_type", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_table("artifact_heads")
