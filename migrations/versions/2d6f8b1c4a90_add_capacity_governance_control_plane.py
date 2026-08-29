"""add capacity governance control plane

Revision ID: 2d6f8b1c4a90
Revises: f2a7d9c4e681
Create Date: 2026-08-25 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2d6f8b1c4a90"
down_revision: str | None = "f2a7d9c4e681"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reflection_capacity_governance_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("expected_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expected_policy_version", sa.Integer(), nullable=True),
        sa.Column("expected_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("observed_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("acknowledged_by", sa.String(length=200), nullable=True),
        sa.Column(
            "acknowledged_principal_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "acknowledged_token_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "alert_type IN ('policy_drift')",
            name="ck_reflection_capacity_governance_alerts_type",
        ),
        sa.CheckConstraint(
            "severity IN ('warning','critical')",
            name="ck_reflection_capacity_governance_alerts_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open','acknowledged','resolved')",
            name="ck_reflection_capacity_governance_alerts_status",
        ),
        sa.CheckConstraint(
            "version >= 1 AND sample_count >= 1 AND reopened_count >= 0",
            name="ck_reflection_capacity_governance_alerts_counts",
        ),
        sa.CheckConstraint(
            "(expected_policy_id IS NULL) = (expected_policy_version IS NULL) "
            "AND (expected_policy_version IS NULL OR expected_policy_version >= 1)",
            name="ck_reflection_capacity_governance_alerts_policy",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND acknowledged_at IS NULL AND resolved_at IS NULL) "
            "OR (status = 'acknowledged' AND acknowledged_by IS NOT NULL "
            "AND acknowledged_principal_id IS NOT NULL "
            "AND acknowledged_token_id IS NOT NULL "
            "AND acknowledged_at IS NOT NULL AND resolved_at IS NULL) "
            "OR (status = 'resolved' AND resolved_at IS NOT NULL)",
            name="ck_reflection_capacity_governance_alerts_lifecycle",
        ),
        sa.CheckConstraint(
            "octet_length(expected_fingerprint) = 64 "
            "AND octet_length(observed_fingerprint) = 64 "
            "AND octet_length(dedupe_key) = 64",
            name="ck_reflection_capacity_governance_alerts_fingerprints",
        ),
        sa.ForeignKeyConstraint(
            ["expected_policy_id"],
            ["reflection_capacity_policies.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dedupe_key",
            name="uq_reflection_capacity_governance_alerts_dedupe",
        ),
    )
    op.create_index(
        "ix_reflection_capacity_governance_alerts_handler_status",
        "reflection_capacity_governance_alerts",
        ["job_type", "handler_version", "status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_reflection_capacity_governance_alerts_expected",
        "reflection_capacity_governance_alerts",
        [
            "job_type",
            "handler_version",
            "expected_fingerprint",
            "last_observation_at",
        ],
        unique=False,
    )

    op.create_table(
        "reflection_capacity_governance_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
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
            name="ck_reflection_capacity_governance_audit_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["reflection_capacity_governance_alerts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["reflection_capacity_change_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reflection_capacity_governance_audit_tenant_created",
        "reflection_capacity_governance_audit_events",
        ["tenant_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_reflection_capacity_governance_audit_actor_created",
        "reflection_capacity_governance_audit_events",
        ["actor_principal_id", "created_at"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION prevent_reflection_capacity_governance_audit_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'capacity governance audit events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reflection_capacity_governance_audit_no_update
        BEFORE UPDATE ON reflection_capacity_governance_audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_reflection_capacity_governance_audit_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_reflection_capacity_governance_audit_no_update "
        "ON reflection_capacity_governance_audit_events"
    )
    op.execute("DROP FUNCTION prevent_reflection_capacity_governance_audit_update()")
    op.drop_index(
        "ix_reflection_capacity_governance_audit_actor_created",
        table_name="reflection_capacity_governance_audit_events",
    )
    op.drop_index(
        "ix_reflection_capacity_governance_audit_tenant_created",
        table_name="reflection_capacity_governance_audit_events",
    )
    op.drop_table("reflection_capacity_governance_audit_events")
    op.drop_index(
        "ix_reflection_capacity_governance_alerts_expected",
        table_name="reflection_capacity_governance_alerts",
    )
    op.drop_index(
        "ix_reflection_capacity_governance_alerts_handler_status",
        table_name="reflection_capacity_governance_alerts",
    )
    op.drop_table("reflection_capacity_governance_alerts")
