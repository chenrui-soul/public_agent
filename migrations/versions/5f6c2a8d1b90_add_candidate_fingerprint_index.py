"""add candidate fingerprint index

Revision ID: 5f6c2a8d1b90
Revises: edf6648c8894
Create Date: 2026-08-25 02:35:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5f6c2a8d1b90"
down_revision: str | Sequence[str] | None = "edf6648c8894"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "learning_candidates",
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE learning_candidates
        SET fingerprint = COALESCE(
            NULLIF(proposed_change ->> 'fingerprint', ''),
            md5(id::text || proposed_change::text)
                || md5('public-agent:' || id::text || proposed_change::text)
        )
        WHERE fingerprint IS NULL
        """
    )
    op.alter_column(
        "learning_candidates",
        "fingerprint",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_index(
        "ix_learning_candidates_scope_fingerprint_status",
        "learning_candidates",
        ["tenant_id", "agent_id", "domain_id", "fingerprint", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_learning_candidates_scope_fingerprint_status",
        table_name="learning_candidates",
    )
    op.drop_column("learning_candidates", "fingerprint")
