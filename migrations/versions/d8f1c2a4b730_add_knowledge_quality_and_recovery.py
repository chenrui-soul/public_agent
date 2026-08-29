"""add governance knowledge quality snapshots and recovery

Revision ID: d8f1c2a4b730
Revises: c7a4d2e9f610
Create Date: 2026-08-25 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8f1c2a4b730"
down_revision: str | None = "c7a4d2e9f610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reflection_capacity_governance_postmortems",
        sa.Column("last_quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reflection_capacity_governance_postmortems",
        sa.Column("quarantine_feedback_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "reflection_capacity_governance_postmortems",
        sa.Column(
            "restore_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "reflection_capacity_governance_postmortems",
        sa.Column("last_restored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE reflection_capacity_governance_postmortems AS postmortem
        SET (last_quarantined_at, quarantine_feedback_id) = (
            SELECT feedback.reviewed_at, feedback.id
            FROM reflection_capacity_governance_knowledge_feedback AS feedback
            WHERE feedback.postmortem_id = postmortem.id
              AND feedback.status = 'confirmed'
              AND feedback.signal = 'safety_concern'
            ORDER BY feedback.reviewed_at DESC, feedback.id DESC
            LIMIT 1
        )
        WHERE postmortem.status = 'quarantined'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM reflection_capacity_governance_postmortems
                WHERE status = 'quarantined'
                  AND (last_quarantined_at IS NULL OR quarantine_feedback_id IS NULL)
            ) THEN
                RAISE EXCEPTION 'quarantined governance knowledge lacks feedback lineage';
            END IF;
        END;
        $$
        """
    )
    op.create_check_constraint(
        "ck_capacity_postmortems_restore_history",
        "reflection_capacity_governance_postmortems",
        "restore_count >= 0 AND "
        "((restore_count = 0 AND last_restored_at IS NULL) OR "
        "(restore_count >= 1 AND last_restored_at IS NOT NULL "
        "AND last_quarantined_at IS NOT NULL AND quarantine_feedback_id IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_capacity_postmortems_quarantine_history",
        "reflection_capacity_governance_postmortems",
        "(status = 'quarantined' AND last_quarantined_at IS NOT NULL "
        "AND quarantine_feedback_id IS NOT NULL) OR "
        "(status IN ('awaiting_review','rejected') "
        "AND last_quarantined_at IS NULL AND quarantine_feedback_id IS NULL "
        "AND restore_count = 0 AND last_restored_at IS NULL) OR "
        "status = 'published'",
    )

    op.create_table(
        "reflection_capacity_governance_knowledge_quality_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("postmortem_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("postmortem_version", sa.Integer(), nullable=False),
        sa.Column("knowledge_version", sa.String(length=100), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("assessment", sa.String(length=32), nullable=False),
        sa.Column("total_feedback", sa.Integer(), nullable=False),
        sa.Column("awaiting_review_count", sa.Integer(), nullable=False),
        sa.Column("confirmed_helpful_count", sa.Integer(), nullable=False),
        sa.Column("confirmed_not_helpful_count", sa.Integer(), nullable=False),
        sa.Column("confirmed_safety_count", sa.Integer(), nullable=False),
        sa.Column("dismissed_count", sa.Integer(), nullable=False),
        sa.Column("superseded_count", sa.Integer(), nullable=False),
        sa.Column("captured_by", sa.String(length=200), nullable=False),
        sa.Column("captured_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "assessment IN ('insufficient','healthy','degraded','unsafe')",
            name="ck_capacity_knowledge_quality_assessment",
        ),
        sa.CheckConstraint(
            "postmortem_version >= 1 "
            "AND octet_length(content_fingerprint) = 64 "
            "AND octet_length(evidence_fingerprint) = 64",
            name="ck_capacity_knowledge_quality_versions",
        ),
        sa.CheckConstraint(
            "total_feedback >= 0 AND awaiting_review_count >= 0 "
            "AND confirmed_helpful_count >= 0 AND confirmed_not_helpful_count >= 0 "
            "AND confirmed_safety_count >= 0 AND dismissed_count >= 0 "
            "AND superseded_count >= 0 AND total_feedback = "
            "awaiting_review_count + confirmed_helpful_count + "
            "confirmed_not_helpful_count + confirmed_safety_count + "
            "dismissed_count + superseded_count",
            name="ck_capacity_knowledge_quality_counts",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["postmortem_id"],
            ["reflection_capacity_governance_postmortems.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "postmortem_id",
            "postmortem_version",
            "evidence_fingerprint",
            name="uq_capacity_knowledge_quality_evidence",
        ),
    )
    op.create_index(
        "ix_capacity_knowledge_quality_tenant_assessment",
        "reflection_capacity_governance_knowledge_quality_snapshots",
        ["tenant_id", "handler_version", "assessment", "captured_at", "id"],
    )
    op.create_index(
        "ix_capacity_knowledge_quality_postmortem",
        "reflection_capacity_governance_knowledge_quality_snapshots",
        ["tenant_id", "handler_version", "postmortem_id", "captured_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_capacity_knowledge_quality_snapshot_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'governance knowledge quality snapshots are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_capacity_knowledge_quality_snapshots_append_only
        BEFORE UPDATE
        ON reflection_capacity_governance_knowledge_quality_snapshots
        FOR EACH ROW EXECUTE FUNCTION prevent_capacity_knowledge_quality_snapshot_mutation()
        """
    )

    op.create_table(
        "reflection_capacity_governance_knowledge_recoveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("postmortem_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quarantine_feedback_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("postmortem_version", sa.Integer(), nullable=False),
        sa.Column("knowledge_version", sa.String(length=100), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=200), nullable=False),
        sa.Column("requested_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("reviewed_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_knowledge_version", sa.String(length=100), nullable=True),
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
            "reason = 'false_positive'",
            name="ck_capacity_knowledge_recoveries_reason",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_review','approved','rejected')",
            name="ck_capacity_knowledge_recoveries_status",
        ),
        sa.CheckConstraint(
            "version >= 1 AND postmortem_version >= 1 "
            "AND octet_length(content_fingerprint) = 64",
            name="ck_capacity_knowledge_recoveries_versions",
        ),
        sa.CheckConstraint(
            "(status = 'awaiting_review' AND reviewed_by IS NULL "
            "AND reviewed_principal_id IS NULL AND reviewed_token_id IS NULL "
            "AND reviewed_at IS NULL AND restored_knowledge_version IS NULL) OR "
            "(status = 'rejected' AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND restored_knowledge_version IS NULL) OR "
            "(status = 'approved' AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND restored_knowledge_version IS NOT NULL)",
            name="ck_capacity_knowledge_recoveries_lifecycle",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["postmortem_id"],
            ["reflection_capacity_governance_postmortems.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["reflection_capacity_governance_knowledge_quality_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quarantine_feedback_id"],
            ["reflection_capacity_governance_knowledge_feedback.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_capacity_knowledge_recoveries_active",
        "reflection_capacity_governance_knowledge_recoveries",
        ["postmortem_id", "postmortem_version"],
        unique=True,
        postgresql_where=sa.text("status = 'awaiting_review'"),
    )
    op.create_index(
        "ix_capacity_knowledge_recoveries_tenant_status",
        "reflection_capacity_governance_knowledge_recoveries",
        ["tenant_id", "handler_version", "status", "updated_at", "id"],
    )
    op.create_index(
        "ix_capacity_knowledge_recoveries_postmortem",
        "reflection_capacity_governance_knowledge_recoveries",
        ["tenant_id", "handler_version", "postmortem_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capacity_knowledge_recoveries_postmortem",
        table_name="reflection_capacity_governance_knowledge_recoveries",
    )
    op.drop_index(
        "ix_capacity_knowledge_recoveries_tenant_status",
        table_name="reflection_capacity_governance_knowledge_recoveries",
    )
    op.drop_index(
        "uq_capacity_knowledge_recoveries_active",
        table_name="reflection_capacity_governance_knowledge_recoveries",
    )
    op.drop_table("reflection_capacity_governance_knowledge_recoveries")
    op.execute(
        "DROP TRIGGER trg_capacity_knowledge_quality_snapshots_append_only "
        "ON reflection_capacity_governance_knowledge_quality_snapshots"
    )
    op.execute("DROP FUNCTION prevent_capacity_knowledge_quality_snapshot_mutation()")
    op.drop_index(
        "ix_capacity_knowledge_quality_postmortem",
        table_name="reflection_capacity_governance_knowledge_quality_snapshots",
    )
    op.drop_index(
        "ix_capacity_knowledge_quality_tenant_assessment",
        table_name="reflection_capacity_governance_knowledge_quality_snapshots",
    )
    op.drop_table("reflection_capacity_governance_knowledge_quality_snapshots")
    op.drop_constraint(
        "ck_capacity_postmortems_quarantine_history",
        "reflection_capacity_governance_postmortems",
        type_="check",
    )
    op.drop_constraint(
        "ck_capacity_postmortems_restore_history",
        "reflection_capacity_governance_postmortems",
        type_="check",
    )
    op.drop_column("reflection_capacity_governance_postmortems", "last_restored_at")
    op.drop_column("reflection_capacity_governance_postmortems", "restore_count")
    op.drop_column("reflection_capacity_governance_postmortems", "quarantine_feedback_id")
    op.drop_column("reflection_capacity_governance_postmortems", "last_quarantined_at")
