"""add authentication management audit

Revision ID: 7e2d4f8a9c10
Revises: 1c7e9a4b6d20
Create Date: 2026-08-25 10:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7e2d4f8a9c10"
down_revision: str | None = "1c7e9a4b6d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authentication_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('success','denied','conflict')",
            name="ck_authentication_audit_events_outcome",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_authentication_audit_events_tenant_created",
        "authentication_audit_events",
        ["tenant_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_authentication_audit_events_actor_created",
        "authentication_audit_events",
        ["actor_principal_id", "created_at"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION prevent_authentication_audit_event_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'authentication audit events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_authentication_audit_events_no_update
        BEFORE UPDATE ON authentication_audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_authentication_audit_event_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_authentication_audit_events_no_update "
        "ON authentication_audit_events"
    )
    op.execute("DROP FUNCTION prevent_authentication_audit_event_update()")
    op.drop_index(
        "ix_authentication_audit_events_actor_created",
        table_name="authentication_audit_events",
    )
    op.drop_index(
        "ix_authentication_audit_events_tenant_created",
        table_name="authentication_audit_events",
    )
    op.drop_table("authentication_audit_events")
