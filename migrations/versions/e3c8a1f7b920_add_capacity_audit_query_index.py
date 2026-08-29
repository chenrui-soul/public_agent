"""add capacity audit query index

Revision ID: e3c8a1f7b920
Revises: 2d6f8b1c4a90
Create Date: 2026-08-25 16:55:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e3c8a1f7b920"
down_revision: str | None = "2d6f8b1c4a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_reflection_capacity_governance_audit_filter_created",
        "reflection_capacity_governance_audit_events",
        [
            "tenant_id",
            "handler_version",
            "outcome",
            "action",
            "created_at",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reflection_capacity_governance_audit_filter_created",
        table_name="reflection_capacity_governance_audit_events",
    )
