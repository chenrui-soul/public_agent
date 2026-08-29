"""add reflection outbox jobs

Revision ID: 4b8f2c6d1a30
Revises: 7e2d4f8a9c10
Create Date: 2026-08-25 10:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4b8f2c6d1a30"
down_revision: str | None = "7e2d4f8a9c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_runs_id_tenant", "runs", ["id", "tenant_id"])
    op.create_table(
        "outbox_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "result_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=200), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending','processing','retry_wait','succeeded','dead_letter')",
            name="ck_outbox_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_jobs_attempts"),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 100",
            name="ck_outbox_jobs_max_attempts",
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND worker_id IS NOT NULL) OR "
            "(status <> 'processing' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL AND worker_id IS NULL)",
            name="ck_outbox_jobs_lease_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id"],
            ["runs.id", "runs.tenant_id"],
            name="fk_outbox_jobs_run_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_type",
            "run_id",
            "handler_version",
            name="uq_outbox_jobs_run_handler",
        ),
    )
    op.create_index(
        "ix_outbox_jobs_claim",
        "outbox_jobs",
        ["status", "available_at", "lease_expires_at", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_jobs_tenant_run",
        "outbox_jobs",
        ["tenant_id", "run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_jobs_tenant_run", table_name="outbox_jobs")
    op.drop_index("ix_outbox_jobs_claim", table_name="outbox_jobs")
    op.drop_table("outbox_jobs")
    op.execute("ALTER TABLE runs DROP CONSTRAINT IF EXISTS uq_runs_id_tenant")
