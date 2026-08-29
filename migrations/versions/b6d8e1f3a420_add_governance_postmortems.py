"""add governance postmortems

Revision ID: b6d8e1f3a420
Revises: 9f4e7c2d1a60
Create Date: 2026-08-25 19:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from public_agent.knowledge.base import KNOWLEDGE_EMBEDDING_DIMENSIONS

revision: str = "b6d8e1f3a420"
down_revision: str | None = "9f4e7c2d1a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reflection_capacity_governance_postmortems",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("remediation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("incident_cycle", sa.Integer(), nullable=False),
        sa.Column("incident_version", sa.Integer(), nullable=False),
        sa.Column("remediation_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("root_cause", sa.String(length=64), nullable=False),
        sa.Column("impact", sa.String(length=64), nullable=False),
        sa.Column("prevention", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=200), nullable=False),
        sa.Column("requested_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("reviewed_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("knowledge_namespace", sa.String(length=150), nullable=True),
        sa.Column("knowledge_source_key", sa.String(length=300), nullable=True),
        sa.Column("knowledge_version", sa.String(length=100), nullable=True),
        sa.Column("published_content", sa.Text(), nullable=True),
        sa.Column("lexical_text", sa.Text(), nullable=True),
        sa.Column("lexical_profile", sa.String(length=100), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', coalesce(lexical_text, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("embedding_profile", sa.String(length=100), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column(
            "embedding",
            Vector(KNOWLEDGE_EMBEDDING_DIMENSIONS),
            nullable=True,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('awaiting_review','published','rejected')",
            name="ck_capacity_postmortems_status",
        ),
        sa.CheckConstraint(
            "version >= 1 AND incident_cycle >= 0 "
            "AND incident_version >= 1 AND remediation_version >= 1",
            name="ck_capacity_postmortems_versions",
        ),
        sa.CheckConstraint(
            "root_cause IN ('authorization_control_gap','policy_drift',"
            "'operational_process_gap','observability_gap','schema_control_gap')",
            name="ck_capacity_postmortems_root_cause",
        ),
        sa.CheckConstraint(
            "impact IN ('governance_delay','control_degradation','repeated_alerting',"
            "'access_disruption','no_external_impact')",
            name="ck_capacity_postmortems_impact",
        ),
        sa.CheckConstraint(
            "prevention IN ('access_review','policy_validation','process_hardening',"
            "'monitoring_expansion','schema_verification')",
            name="ck_capacity_postmortems_prevention",
        ),
        sa.CheckConstraint(
            "char_length(summary) BETWEEN 10 AND 1000 "
            "AND octet_length(content_fingerprint) = 64",
            name="ck_capacity_postmortems_content",
        ),
        sa.CheckConstraint(
            "embedding_dimensions IS NULL "
            f"OR embedding_dimensions = {KNOWLEDGE_EMBEDDING_DIMENSIONS}",
            name="ck_capacity_postmortems_embedding_dimensions",
        ),
        sa.CheckConstraint(
            "knowledge_namespace IS NULL "
            "OR knowledge_namespace = 'operations.governance.postmortems'",
            name="ck_capacity_postmortems_namespace",
        ),
        sa.CheckConstraint(
            "(status = 'awaiting_review' AND reviewed_at IS NULL "
            "AND knowledge_namespace IS NULL AND knowledge_source_key IS NULL "
            "AND knowledge_version IS NULL AND published_content IS NULL "
            "AND lexical_text IS NULL AND lexical_profile IS NULL "
            "AND embedding_profile IS NULL AND embedding_dimensions IS NULL "
            "AND embedding IS NULL AND published_at IS NULL) "
            "OR (status = 'rejected' AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND knowledge_namespace IS NULL "
            "AND knowledge_source_key IS NULL AND knowledge_version IS NULL "
            "AND published_content IS NULL AND lexical_text IS NULL "
            "AND lexical_profile IS NULL AND embedding_profile IS NULL "
            "AND embedding_dimensions IS NULL AND embedding IS NULL "
            "AND published_at IS NULL) "
            "OR (status = 'published' AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND knowledge_namespace IS NOT NULL "
            "AND knowledge_source_key IS NOT NULL AND knowledge_version IS NOT NULL "
            "AND published_content IS NOT NULL AND lexical_text IS NOT NULL "
            "AND lexical_profile IS NOT NULL AND embedding_profile IS NOT NULL "
            "AND embedding_dimensions IS NOT NULL AND embedding IS NOT NULL "
            "AND published_at IS NOT NULL)",
            name="ck_capacity_postmortems_lifecycle",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["reflection_capacity_governance_incidents.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["remediation_id"],
            ["reflection_capacity_governance_remediations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "remediation_id",
            name="uq_capacity_postmortems_remediation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "content_fingerprint",
            name="uq_capacity_postmortems_tenant_fingerprint",
        ),
    )
    op.create_index(
        "ix_capacity_postmortems_tenant_status",
        "reflection_capacity_governance_postmortems",
        ["tenant_id", "handler_version", "status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_capacity_postmortems_source",
        "reflection_capacity_governance_postmortems",
        ["tenant_id", "handler_version", "incident_id", "remediation_id"],
        unique=False,
    )
    op.create_index(
        "ix_capacity_postmortems_search_vector_gin",
        "reflection_capacity_governance_postmortems",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_capacity_postmortems_embedding_hnsw",
        "reflection_capacity_governance_postmortems",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.add_column(
        "reflection_capacity_governance_audit_events",
        sa.Column("postmortem_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_capacity_governance_audit_postmortem",
        "reflection_capacity_governance_audit_events",
        "reflection_capacity_governance_postmortems",
        ["postmortem_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_capacity_governance_audit_postmortem",
        "reflection_capacity_governance_audit_events",
        type_="foreignkey",
    )
    op.drop_column("reflection_capacity_governance_audit_events", "postmortem_id")
    op.drop_index(
        "ix_capacity_postmortems_embedding_hnsw",
        table_name="reflection_capacity_governance_postmortems",
    )
    op.drop_index(
        "ix_capacity_postmortems_search_vector_gin",
        table_name="reflection_capacity_governance_postmortems",
    )
    op.drop_index(
        "ix_capacity_postmortems_source",
        table_name="reflection_capacity_governance_postmortems",
    )
    op.drop_index(
        "ix_capacity_postmortems_tenant_status",
        table_name="reflection_capacity_governance_postmortems",
    )
    op.drop_table("reflection_capacity_governance_postmortems")
