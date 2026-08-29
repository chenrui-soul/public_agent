"""add reflection capacity indexes

Revision ID: b7e2c4a9d610
Revises: a4d6f8b2c510
Create Date: 2026-08-25 13:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7e2c4a9d610"
down_revision: str | None = "a4d6f8b2c510"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_outbox_jobs_handler_status_available",
        "outbox_jobs",
        ["job_type", "handler_version", "status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_reflection_worker_heartbeats_handler_seen",
        "reflection_worker_heartbeats",
        ["job_type", "handler_version", "last_seen_at", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reflection_worker_heartbeats_handler_seen",
        table_name="reflection_worker_heartbeats",
    )
    op.drop_index(
        "ix_outbox_jobs_handler_status_available",
        table_name="outbox_jobs",
    )
