"""add governance knowledge feedback

Revision ID: c7a4d2e9f610
Revises: b6d8e1f3a420
Create Date: 2026-08-25 20:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7a4d2e9f610"
down_revision: str | None = "b6d8e1f3a420"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PUBLISHED_LIFECYCLE = (
    "status IN ('published','quarantined') AND reviewed_by IS NOT NULL "
    "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
    "AND reviewed_at IS NOT NULL AND knowledge_namespace IS NOT NULL "
    "AND knowledge_source_key IS NOT NULL AND knowledge_version IS NOT NULL "
    "AND published_content IS NOT NULL AND lexical_text IS NOT NULL "
    "AND lexical_profile IS NOT NULL AND embedding_profile IS NOT NULL "
    "AND embedding_dimensions IS NOT NULL AND embedding IS NOT NULL "
    "AND published_at IS NOT NULL"
)

_AWAITING_LIFECYCLE = (
    "status = 'awaiting_review' AND reviewed_at IS NULL "
    "AND knowledge_namespace IS NULL AND knowledge_source_key IS NULL "
    "AND knowledge_version IS NULL AND published_content IS NULL "
    "AND lexical_text IS NULL AND lexical_profile IS NULL "
    "AND embedding_profile IS NULL AND embedding_dimensions IS NULL "
    "AND embedding IS NULL AND published_at IS NULL"
)

_REJECTED_LIFECYCLE = (
    "status = 'rejected' AND reviewed_by IS NOT NULL "
    "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
    "AND reviewed_at IS NOT NULL AND knowledge_namespace IS NULL "
    "AND knowledge_source_key IS NULL AND knowledge_version IS NULL "
    "AND published_content IS NULL AND lexical_text IS NULL "
    "AND lexical_profile IS NULL AND embedding_profile IS NULL "
    "AND embedding_dimensions IS NULL AND embedding IS NULL "
    "AND published_at IS NULL"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_capacity_postmortems_lifecycle",
        "reflection_capacity_governance_postmortems",
        type_="check",
    )
    op.drop_constraint(
        "ck_capacity_postmortems_status",
        "reflection_capacity_governance_postmortems",
        type_="check",
    )
    op.create_check_constraint(
        "ck_capacity_postmortems_status",
        "reflection_capacity_governance_postmortems",
        "status IN ('awaiting_review','published','quarantined','rejected')",
    )
    op.create_check_constraint(
        "ck_capacity_postmortems_lifecycle",
        "reflection_capacity_governance_postmortems",
        f"({_AWAITING_LIFECYCLE}) OR ({_REJECTED_LIFECYCLE}) OR ({_PUBLISHED_LIFECYCLE})",
    )
    op.create_table(
        "reflection_capacity_governance_knowledge_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("postmortem_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("postmortem_version", sa.Integer(), nullable=False),
        sa.Column("knowledge_version", sa.String(length=100), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("signal", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reported_by", sa.String(length=200), nullable=False),
        sa.Column("reported_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reported_token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("reviewed_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('awaiting_review','confirmed','dismissed','superseded')",
            name="ck_capacity_knowledge_feedback_status",
        ),
        sa.CheckConstraint(
            "signal IN ('helpful','not_helpful','safety_concern')",
            name="ck_capacity_knowledge_feedback_signal",
        ),
        sa.CheckConstraint(
            "reason IN ('relevance','accuracy','staleness','unsafe_content')",
            name="ck_capacity_knowledge_feedback_reason",
        ),
        sa.CheckConstraint(
            "(signal = 'safety_concern') = (reason = 'unsafe_content')",
            name="ck_capacity_knowledge_feedback_safety_pair",
        ),
        sa.CheckConstraint(
            "version >= 1 AND postmortem_version >= 1 "
            "AND octet_length(content_fingerprint) = 64",
            name="ck_capacity_knowledge_feedback_versions",
        ),
        sa.CheckConstraint(
            "(status IN ('awaiting_review','superseded') AND reviewed_by IS NULL "
            "AND reviewed_principal_id IS NULL AND reviewed_token_id IS NULL "
            "AND reviewed_at IS NULL) OR "
            "(status IN ('confirmed','dismissed') AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL)",
            name="ck_capacity_knowledge_feedback_lifecycle",
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
            "reported_principal_id",
            "postmortem_version",
            name="uq_capacity_knowledge_feedback_reporter_version",
        ),
    )
    op.create_index(
        "ix_capacity_knowledge_feedback_tenant_status",
        "reflection_capacity_governance_knowledge_feedback",
        ["tenant_id", "handler_version", "status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_capacity_knowledge_feedback_postmortem",
        "reflection_capacity_governance_knowledge_feedback",
        ["tenant_id", "handler_version", "postmortem_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capacity_knowledge_feedback_postmortem",
        table_name="reflection_capacity_governance_knowledge_feedback",
    )
    op.drop_index(
        "ix_capacity_knowledge_feedback_tenant_status",
        table_name="reflection_capacity_governance_knowledge_feedback",
    )
    op.drop_table("reflection_capacity_governance_knowledge_feedback")
    op.drop_constraint(
        "ck_capacity_postmortems_lifecycle",
        "reflection_capacity_governance_postmortems",
        type_="check",
    )
    op.drop_constraint(
        "ck_capacity_postmortems_status",
        "reflection_capacity_governance_postmortems",
        type_="check",
    )
    op.create_check_constraint(
        "ck_capacity_postmortems_status",
        "reflection_capacity_governance_postmortems",
        "status IN ('awaiting_review','published','rejected')",
    )
    published = _PUBLISHED_LIFECYCLE.replace(
        "status IN ('published','quarantined')",
        "status = 'published'",
    )
    op.create_check_constraint(
        "ck_capacity_postmortems_lifecycle",
        "reflection_capacity_governance_postmortems",
        f"({_AWAITING_LIFECYCLE}) OR ({_REJECTED_LIFECYCLE}) OR ({published})",
    )
