"""add approval resume leases

Revision ID: c73f9a2d4e10
Revises: b42e6f8a1c30
Create Date: 2026-08-25 06:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c73f9a2d4e10"
down_revision: str | None = "b42e6f8a1c30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("resume_token", sa.UUID(), nullable=True))
    op.add_column(
        "runs",
        sa.Column("resume_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_runs_status_resume_lease",
        "runs",
        ["status", "resume_lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_approvals_run_created",
        "approvals",
        ["run_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_approvals_run_created", table_name="approvals")
    op.drop_index("ix_runs_status_resume_lease", table_name="runs")
    op.drop_column("runs", "resume_lease_expires_at")
    op.drop_column("runs", "resume_token")
