"""add capacity governance incidents

Revision ID: 6b9d2f4a8c71
Revises: e3c8a1f7b920
Create Date: 2026-08-25 17:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6b9d2f4a8c71"
down_revision: str | None = "e3c8a1f7b920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reflection_capacity_governance_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("signal", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("reopened_count", sa.Integer(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("acknowledged_by", sa.String(length=200), nullable=True),
        sa.Column("acknowledged_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("acknowledged_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            "signal IN ('audit_failure_spike','alert_sla_breached',"
            "'alert_reopen_repeat','drill_check_failed')",
            name="ck_reflection_capacity_governance_incidents_signal",
        ),
        sa.CheckConstraint(
            "severity IN ('warning','critical')",
            name="ck_reflection_capacity_governance_incidents_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open','acknowledged','resolved')",
            name="ck_reflection_capacity_governance_incidents_status",
        ),
        sa.CheckConstraint(
            "version >= 1 AND occurrence_count >= 1 AND reopened_count >= 0",
            name="ck_reflection_capacity_governance_incidents_counts",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND acknowledged_at IS NULL AND resolved_at IS NULL) "
            "OR (status = 'acknowledged' AND acknowledged_by IS NOT NULL "
            "AND acknowledged_principal_id IS NOT NULL "
            "AND acknowledged_token_id IS NOT NULL "
            "AND acknowledged_at IS NOT NULL AND resolved_at IS NULL) "
            "OR (status = 'resolved' AND resolved_at IS NOT NULL)",
            name="ck_reflection_capacity_governance_incidents_lifecycle",
        ),
        sa.CheckConstraint(
            "octet_length(fingerprint) = 64 "
            "AND octet_length(evidence_fingerprint) = 64",
            name="ck_reflection_capacity_governance_incidents_fingerprints",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fingerprint",
            name="uq_reflection_capacity_governance_incidents_fingerprint",
        ),
    )
    op.create_index(
        "ix_reflection_capacity_governance_incidents_tenant_status",
        "reflection_capacity_governance_incidents",
        ["tenant_id", "handler_version", "status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_reflection_capacity_governance_incidents_source",
        "reflection_capacity_governance_incidents",
        ["tenant_id", "handler_version", "signal", "source_id", "updated_at"],
        unique=False,
    )
    op.add_column(
        "reflection_capacity_governance_audit_events",
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_reflection_capacity_governance_audit_incident",
        "reflection_capacity_governance_audit_events",
        "reflection_capacity_governance_incidents",
        ["incident_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_reflection_capacity_governance_audit_incident",
        "reflection_capacity_governance_audit_events",
        type_="foreignkey",
    )
    op.drop_column("reflection_capacity_governance_audit_events", "incident_id")
    op.drop_index(
        "ix_reflection_capacity_governance_incidents_source",
        table_name="reflection_capacity_governance_incidents",
    )
    op.drop_index(
        "ix_reflection_capacity_governance_incidents_tenant_status",
        table_name="reflection_capacity_governance_incidents",
    )
    op.drop_table("reflection_capacity_governance_incidents")
