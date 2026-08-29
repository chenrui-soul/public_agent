"""add growth management indexes

Revision ID: 1c7e9a4b6d20
Revises: fa6c3d9e2b40
Create Date: 2026-08-25 09:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1c7e9a4b6d20"
down_revision: str | None = "fa6c3d9e2b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_memories_management_scan",
        "memories",
        ["tenant_id", "agent_id", "domain_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_approvals_candidate_created",
        "approvals",
        ["candidate_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_approvals_candidate_created", table_name="approvals")
    op.drop_index("ix_memories_management_scan", table_name="memories")
