"""harden resume lease invariants

Revision ID: d84e1b6f5a20
Revises: c73f9a2d4e10
Create Date: 2026-08-25 06:53:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d84e1b6f5a20"
down_revision: str | None = "c73f9a2d4e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_runs_resume_lease_consistent",
        "runs",
        "(resume_token IS NULL AND resume_lease_expires_at IS NULL) OR "
        "(resume_token IS NOT NULL AND resume_lease_expires_at IS NOT NULL "
        "AND status = 'running')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_runs_resume_lease_consistent", "runs", type_="check")
