"""add governance knowledge recertification facts

Revision ID: f1b3c7d9e2a4
Revises: e9a2f4c6b810
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1b3c7d9e2a4"
down_revision: str | None = "e9a2f4c6b810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reflection_capacity_governance_postmortems", sa.Column("last_certified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reflection_capacity_governance_postmortems", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reflection_capacity_governance_postmortems", sa.Column("retired_by", sa.String(length=200), nullable=True))
    op.add_column("reflection_capacity_governance_postmortems", sa.Column("retired_principal_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("reflection_capacity_governance_postmortems", sa.Column("retired_token_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.drop_constraint("ck_capacity_postmortems_status", "reflection_capacity_governance_postmortems", type_="check")
    op.create_check_constraint(
        "ck_capacity_postmortems_status", "reflection_capacity_governance_postmortems",
        "status IN ('awaiting_review','published','quarantined','rejected','retired')",
    )
    op.drop_constraint("ck_capacity_postmortems_quarantine_history", "reflection_capacity_governance_postmortems", type_="check")
    op.create_check_constraint(
        "ck_capacity_postmortems_quarantine_history", "reflection_capacity_governance_postmortems",
        "(status = 'quarantined' AND last_quarantined_at IS NOT NULL AND quarantine_feedback_id IS NOT NULL) OR "
        "(status IN ('awaiting_review','rejected') AND last_quarantined_at IS NULL AND quarantine_feedback_id IS NULL AND restore_count = 0 AND last_restored_at IS NULL) OR "
        "status IN ('published','retired')",
    )
    op.drop_constraint("ck_capacity_postmortems_lifecycle", "reflection_capacity_governance_postmortems", type_="check")
    op.create_check_constraint(
        "ck_capacity_postmortems_lifecycle", "reflection_capacity_governance_postmortems",
        "(status = 'awaiting_review' AND reviewed_at IS NULL AND knowledge_namespace IS NULL AND knowledge_source_key IS NULL AND knowledge_version IS NULL AND published_content IS NULL AND lexical_text IS NULL AND lexical_profile IS NULL AND embedding_profile IS NULL AND embedding_dimensions IS NULL AND embedding IS NULL AND published_at IS NULL) OR "
        "(status = 'rejected' AND reviewed_by IS NOT NULL AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL AND reviewed_at IS NOT NULL AND knowledge_namespace IS NULL AND knowledge_source_key IS NULL AND knowledge_version IS NULL AND published_content IS NULL AND lexical_text IS NULL AND lexical_profile IS NULL AND embedding_profile IS NULL AND embedding_dimensions IS NULL AND embedding IS NULL AND published_at IS NULL) OR "
        "(status IN ('published','quarantined','retired') AND reviewed_by IS NOT NULL AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL AND reviewed_at IS NOT NULL AND knowledge_namespace IS NOT NULL AND knowledge_source_key IS NOT NULL AND knowledge_version IS NOT NULL AND published_content IS NOT NULL AND lexical_text IS NOT NULL AND lexical_profile IS NOT NULL AND embedding_profile IS NOT NULL AND embedding_dimensions IS NOT NULL AND embedding IS NOT NULL AND published_at IS NOT NULL)",
    )
    op.create_table(
        "reflection_capacity_governance_knowledge_recertifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("postmortem_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quality_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("postmortem_version", sa.Integer(), nullable=False),
        sa.Column("knowledge_version", sa.String(length=100), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("quality_evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("requested_by", sa.String(length=200), nullable=False),
        sa.Column("requested_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("reviewed_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('awaiting_review','certified','rejected','retired')", name="ck_capacity_knowledge_recertifications_status"),
        sa.CheckConstraint("decision IN ('certify','reject','retire')", name="ck_capacity_knowledge_recertifications_decision"),
        sa.CheckConstraint("reason IN ('validation_passed','stale_evidence','quality_risk','replaced','scope_ended')", name="ck_capacity_knowledge_recertifications_reason"),
        sa.CheckConstraint("version >= 1 AND postmortem_version >= 1 AND octet_length(content_fingerprint) = 64 AND octet_length(quality_evidence_fingerprint) = 64", name="ck_capacity_knowledge_recertifications_versions"),
        sa.CheckConstraint("(status = 'awaiting_review' AND reviewed_by IS NULL AND reviewed_principal_id IS NULL AND reviewed_token_id IS NULL AND reviewed_at IS NULL) OR (status IN ('certified','rejected','retired') AND reviewed_by IS NOT NULL AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL AND reviewed_at IS NOT NULL)", name="ck_capacity_knowledge_recertifications_lifecycle"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["postmortem_id"], ["reflection_capacity_governance_postmortems.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quality_snapshot_id"], ["reflection_capacity_governance_knowledge_quality_snapshots.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_capacity_knowledge_recertifications_idempotency"),
    )
    op.create_index("uq_capacity_knowledge_recertifications_active", "reflection_capacity_governance_knowledge_recertifications", ["postmortem_id", "postmortem_version"], unique=True, postgresql_where=sa.text("status = 'awaiting_review'"))
    op.create_index("ix_capacity_knowledge_recertifications_tenant_status", "reflection_capacity_governance_knowledge_recertifications", ["tenant_id", "handler_version", "status", "updated_at", "id"])
    op.create_index("ix_capacity_knowledge_recertifications_postmortem", "reflection_capacity_governance_knowledge_recertifications", ["tenant_id", "handler_version", "postmortem_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_capacity_knowledge_recertifications_postmortem", table_name="reflection_capacity_governance_knowledge_recertifications")
    op.drop_index("ix_capacity_knowledge_recertifications_tenant_status", table_name="reflection_capacity_governance_knowledge_recertifications")
    op.drop_index("uq_capacity_knowledge_recertifications_active", table_name="reflection_capacity_governance_knowledge_recertifications")
    op.drop_table("reflection_capacity_governance_knowledge_recertifications")
    op.drop_constraint("ck_capacity_postmortems_lifecycle", "reflection_capacity_governance_postmortems", type_="check")
    op.create_check_constraint("ck_capacity_postmortems_lifecycle", "reflection_capacity_governance_postmortems", "(status = 'awaiting_review' AND reviewed_at IS NULL AND knowledge_namespace IS NULL AND knowledge_source_key IS NULL AND knowledge_version IS NULL AND published_content IS NULL AND lexical_text IS NULL AND lexical_profile IS NULL AND embedding_profile IS NULL AND embedding_dimensions IS NULL AND embedding IS NULL AND published_at IS NULL) OR (status = 'rejected' AND reviewed_by IS NOT NULL AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL AND reviewed_at IS NOT NULL AND knowledge_namespace IS NULL AND knowledge_source_key IS NULL AND knowledge_version IS NULL AND published_content IS NULL AND lexical_text IS NULL AND lexical_profile IS NULL AND embedding_profile IS NULL AND embedding_dimensions IS NULL AND embedding IS NULL AND published_at IS NULL) OR (status IN ('published','quarantined') AND reviewed_by IS NOT NULL AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL AND reviewed_at IS NOT NULL AND knowledge_namespace IS NOT NULL AND knowledge_source_key IS NOT NULL AND knowledge_version IS NOT NULL AND published_content IS NOT NULL AND lexical_text IS NOT NULL AND lexical_profile IS NOT NULL AND embedding_profile IS NOT NULL AND embedding_dimensions IS NOT NULL AND embedding IS NOT NULL AND published_at IS NOT NULL)")
    op.drop_constraint("ck_capacity_postmortems_quarantine_history", "reflection_capacity_governance_postmortems", type_="check")
    op.create_check_constraint("ck_capacity_postmortems_quarantine_history", "reflection_capacity_governance_postmortems", "(status = 'quarantined' AND last_quarantined_at IS NOT NULL AND quarantine_feedback_id IS NOT NULL) OR (status IN ('awaiting_review','rejected') AND last_quarantined_at IS NULL AND quarantine_feedback_id IS NULL AND restore_count = 0 AND last_restored_at IS NULL) OR status = 'published'")
    op.drop_constraint("ck_capacity_postmortems_status", "reflection_capacity_governance_postmortems", type_="check")
    op.create_check_constraint("ck_capacity_postmortems_status", "reflection_capacity_governance_postmortems", "status IN ('awaiting_review','published','quarantined','rejected')")
    for column in ("retired_token_id", "retired_principal_id", "retired_by", "retired_at", "last_certified_at"):
        op.drop_column("reflection_capacity_governance_postmortems", column)
