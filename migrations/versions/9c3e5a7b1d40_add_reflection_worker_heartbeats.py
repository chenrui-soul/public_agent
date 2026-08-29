"""add reflection worker heartbeats

Revision ID: 9c3e5a7b1d40
Revises: 4b8f2c6d1a30
Create Date: 2026-08-25 11:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9c3e5a7b1d40"
down_revision: str | None = "4b8f2c6d1a30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reflection_worker_heartbeats",
        sa.Column("worker_id", sa.String(length=200), nullable=False),
        sa.Column("instance_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("processed_jobs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "processed_jobs >= 0",
            name="ck_reflection_worker_heartbeats_processed_jobs",
        ),
        sa.CheckConstraint(
            "status IN ('idle','running','stopping','stopped')",
            name="ck_reflection_worker_heartbeats_status",
        ),
        sa.ForeignKeyConstraint(
            ["last_job_id"],
            ["outbox_jobs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "ix_reflection_worker_heartbeats_status_seen",
        "reflection_worker_heartbeats",
        ["status", "last_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reflection_worker_heartbeats_status_seen",
        table_name="reflection_worker_heartbeats",
    )
    op.drop_table("reflection_worker_heartbeats")
