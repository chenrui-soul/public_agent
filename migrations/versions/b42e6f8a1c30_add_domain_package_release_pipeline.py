"""add domain package release pipeline

Revision ID: b42e6f8a1c30
Revises: a91c4e7d2b60
Create Date: 2026-08-25 06:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b42e6f8a1c30"
down_revision: str | None = "a91c4e7d2b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_package_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("domain_id", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("total_size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=True),
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
            "status IN ("
            "'draft','evaluating','awaiting_approval','approved','active','deprecated',"
            "'rolled_back','rejected'"
            ")",
            name="ck_domain_package_versions_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_domain_package_versions_revision",
        ),
        sa.CheckConstraint(
            "total_size_bytes >= 0",
            name="ck_domain_package_versions_total_size",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_domain_package_versions_content_hash",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_version_id",
            name="uq_domain_package_versions_agent_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "domain_id",
            "version",
            name="uq_domain_package_versions_scope_version",
        ),
    )
    op.create_index(
        "ix_domain_package_versions_scope_status",
        "domain_package_versions",
        ["tenant_id", "agent_id", "domain_id", "status"],
        unique=False,
    )

    op.create_table(
        "domain_package_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("package_version_id", sa.UUID(), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("asset_key", sa.String(length=100), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "asset_type IN ('instructions','skill','policy','workflow','evaluation')",
            name="ck_domain_package_assets_type",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_domain_package_assets_size"),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_domain_package_assets_content_hash",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["domain_package_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_version_id",
            "relative_path",
            name="uq_domain_package_assets_version_path",
        ),
        sa.UniqueConstraint(
            "package_version_id",
            "asset_type",
            "asset_key",
            name="uq_domain_package_assets_version_type_key",
        ),
    )
    op.create_index(
        "ix_domain_package_assets_version",
        "domain_package_assets",
        ["package_version_id"],
        unique=False,
    )

    op.create_table(
        "domain_package_evaluations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("package_version_id", sa.UUID(), nullable=False),
        sa.Column("suite", sa.String(length=200), nullable=False),
        sa.Column("dataset_version", sa.String(length=100), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("report_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_domain_package_evaluations_score",
        ),
        sa.CheckConstraint(
            "report_hash ~ '^[0-9a-f]{64}$'",
            name="ck_domain_package_evaluations_report_hash",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["domain_package_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_version_id",
            "report_hash",
            name="uq_domain_package_evaluations_version_report",
        ),
    )
    op.create_index(
        "ix_domain_package_evaluations_version_created",
        "domain_package_evaluations",
        ["package_version_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "domain_package_approvals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("package_version_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.String(length=200), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('approved','rejected')",
            name="ck_domain_package_approvals_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["domain_package_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_version_id",
            name="uq_domain_package_approvals_version",
        ),
    )
    op.create_index(
        "ix_domain_package_approvals_tenant_created",
        "domain_package_approvals",
        ["tenant_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "domain_package_releases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("package_version_id", sa.UUID(), nullable=False),
        sa.Column("domain_id", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("from_agent_version_id", sa.UUID(), nullable=True),
        sa.Column("to_agent_version_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("performed_by", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('activate','rollback')",
            name="ck_domain_package_releases_action",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["from_agent_version_id"],
            ["agent_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["domain_package_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["to_agent_version_id"],
            ["agent_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "idempotency_key",
            name="uq_domain_package_releases_scope_idempotency",
        ),
    )
    op.create_index(
        "ix_domain_package_releases_scope_created",
        "domain_package_releases",
        ["tenant_id", "agent_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_domain_package_releases_version_created",
        "domain_package_releases",
        ["package_version_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_domain_package_releases_version_created",
        table_name="domain_package_releases",
    )
    op.drop_index(
        "ix_domain_package_releases_scope_created",
        table_name="domain_package_releases",
    )
    op.drop_table("domain_package_releases")

    op.drop_index(
        "ix_domain_package_approvals_tenant_created",
        table_name="domain_package_approvals",
    )
    op.drop_table("domain_package_approvals")

    op.drop_index(
        "ix_domain_package_evaluations_version_created",
        table_name="domain_package_evaluations",
    )
    op.drop_table("domain_package_evaluations")

    op.drop_index(
        "ix_domain_package_assets_version",
        table_name="domain_package_assets",
    )
    op.drop_table("domain_package_assets")

    op.drop_index(
        "ix_domain_package_versions_scope_status",
        table_name="domain_package_versions",
    )
    op.drop_table("domain_package_versions")
