"""add api token authentication

Revision ID: fa6c3d9e2b40
Revises: e95f2c7a6b31
Create Date: 2026-08-25 07:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "fa6c3d9e2b40"
down_revision: str | None = "e95f2c7a6b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_agents_id_tenant", "agents", ["id", "tenant_id"])
    op.create_table(
        "api_principals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column(
            "permissions",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=False,
        ),
        sa.Column("all_agents", sa.Boolean(), server_default=sa.false(), nullable=False),
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
            "status IN ('active','disabled')",
            name="ck_api_principals_status",
        ),
        sa.CheckConstraint(
            "cardinality(permissions) BETWEEN 1 AND 100",
            name="ck_api_principals_permissions",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_api_principals_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "subject",
            name="uq_api_principals_tenant_subject",
        ),
    )
    op.create_index(
        "ix_api_principals_tenant_status",
        "api_principals",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_table(
        "api_principal_agent_grants",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            name="fk_api_principal_agent_grants_agent_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id", "tenant_id"],
            ["api_principals.id", "api_principals.tenant_id"],
            name="fk_api_principal_agent_grants_principal_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("principal_id", "agent_id"),
    )
    op.create_index(
        "ix_api_principal_agent_grants_agent",
        "api_principal_agent_grants",
        ["tenant_id", "agent_id"],
        unique=False,
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prefix", sa.String(length=12), nullable=False),
        sa.Column("secret_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
            "octet_length(secret_digest) = 32",
            name="ck_api_tokens_digest_size",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_api_tokens_expiry",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id", "tenant_id"],
            ["api_principals.id", "api_principals.tenant_id"],
            name="fk_api_tokens_principal_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prefix", name="uq_api_tokens_prefix"),
    )
    op.create_index(
        "ix_api_tokens_principal_active",
        "api_tokens",
        ["principal_id", "revoked_at", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_api_tokens_principal_active", table_name="api_tokens")
    op.drop_table("api_tokens")
    op.drop_index(
        "ix_api_principal_agent_grants_agent",
        table_name="api_principal_agent_grants",
    )
    op.drop_table("api_principal_agent_grants")
    op.drop_index("ix_api_principals_tenant_status", table_name="api_principals")
    op.drop_table("api_principals")
    op.drop_constraint("uq_agents_id_tenant", "agents", type_="unique")
