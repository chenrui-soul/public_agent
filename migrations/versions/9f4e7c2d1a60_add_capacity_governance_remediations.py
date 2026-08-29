"""add capacity governance remediations

Revision ID: 9f4e7c2d1a60
Revises: 6b9d2f4a8c71
Create Date: 2026-08-25 18:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9f4e7c2d1a60"
down_revision: str | None = "6b9d2f4a8c71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reflection_capacity_governance_remediations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("incident_cycle", sa.Integer(), nullable=False),
        sa.Column("playbook", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=200), nullable=False),
        sa.Column("requested_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.Column("approved_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(length=200), nullable=True),
        sa.Column("rejected_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejected_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_by", sa.String(length=200), nullable=True),
        sa.Column("executed_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("executed_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_result", sa.String(length=32), nullable=True),
        sa.Column("execution_evidence", sa.String(length=64), nullable=True),
        sa.Column("incident_version_at_execution", sa.Integer(), nullable=True),
        sa.Column("verified_by", sa.String(length=200), nullable=True),
        sa.Column("verified_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("verified_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
            "playbook IN ('audit_failure_containment','alert_sla_recovery',"
            "'alert_reopen_stabilization','drill_control_repair')",
            name="ck_capacity_remediations_playbook",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_approval','approved','verification_pending',"
            "'verified','rejected','failed')",
            name="ck_capacity_remediations_status",
        ),
        sa.CheckConstraint(
            "version >= 1 AND incident_cycle >= 0",
            name="ck_capacity_remediations_versions",
        ),
        sa.CheckConstraint(
            "execution_result IS NULL OR execution_result IN ('completed','failed')",
            name="ck_capacity_remediations_result",
        ),
        sa.CheckConstraint(
            "execution_evidence IS NULL OR execution_evidence IN "
            "('containment_applied','configuration_reviewed','monitoring_extended',"
            "'schema_control_restored')",
            name="ck_capacity_remediations_evidence",
        ),
        sa.CheckConstraint(
            "(status = 'awaiting_approval' AND approved_at IS NULL "
            "AND rejected_at IS NULL AND executed_at IS NULL AND verified_at IS NULL) "
            "OR (status = 'approved' AND approved_by IS NOT NULL "
            "AND approved_at IS NOT NULL AND executed_at IS NULL "
            "AND rejected_at IS NULL AND verified_at IS NULL) "
            "OR (status = 'rejected' AND rejected_by IS NOT NULL "
            "AND rejected_at IS NOT NULL AND approved_at IS NULL "
            "AND executed_at IS NULL AND verified_at IS NULL) "
            "OR (status IN ('verification_pending','failed') "
            "AND approved_at IS NOT NULL AND executed_by IS NOT NULL "
            "AND executed_at IS NOT NULL AND execution_result IS NOT NULL "
            "AND execution_evidence IS NOT NULL AND incident_version_at_execution IS NOT NULL "
            "AND verified_at IS NULL) "
            "OR (status = 'verified' AND approved_at IS NOT NULL "
            "AND executed_at IS NOT NULL AND execution_result = 'completed' "
            "AND verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_capacity_remediations_lifecycle",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["reflection_capacity_governance_incidents.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "incident_id",
            "incident_cycle",
            name="uq_capacity_remediations_incident_cycle",
        ),
    )
    op.create_index(
        "ix_capacity_remediations_tenant_status",
        "reflection_capacity_governance_remediations",
        ["tenant_id", "handler_version", "status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_capacity_remediations_incident",
        "reflection_capacity_governance_remediations",
        ["tenant_id", "handler_version", "incident_id", "incident_cycle"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capacity_remediations_incident",
        table_name="reflection_capacity_governance_remediations",
    )
    op.drop_index(
        "ix_capacity_remediations_tenant_status",
        table_name="reflection_capacity_governance_remediations",
    )
    op.drop_table("reflection_capacity_governance_remediations")
