"""add reflection job operations

Revision ID: a4d6f8b2c510
Revises: 9c3e5a7b1d40
Create Date: 2026-08-25 11:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d6f8b2c510"
down_revision: str | None = "9c3e5a7b1d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_jobs",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "outbox_jobs",
        sa.Column("attempts_in_cycle", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute("UPDATE outbox_jobs SET attempts_in_cycle = attempts")
    op.create_check_constraint(
        "ck_outbox_jobs_version",
        "outbox_jobs",
        "version >= 1",
    )
    op.create_check_constraint(
        "ck_outbox_jobs_attempts_in_cycle",
        "outbox_jobs",
        "attempts_in_cycle >= 0",
    )
    op.create_unique_constraint(
        "uq_outbox_jobs_id_tenant",
        "outbox_jobs",
        ["id", "tenant_id"],
    )

    op.create_table(
        "reflection_job_retry_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("result_status", sa.String(length=32), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('success','conflict')",
            name="ck_reflection_job_retry_requests_outcome",
        ),
        sa.CheckConstraint(
            "expected_version >= 1 AND result_version >= 1",
            name="ck_reflection_job_retry_requests_versions",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["outbox_jobs.id", "outbox_jobs.tenant_id"],
            name="fk_reflection_job_retry_requests_job_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id"],
            ["runs.id", "runs.tenant_id"],
            name="fk_reflection_job_retry_requests_run_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["agents.id", "agents.tenant_id"],
            name="fk_reflection_job_retry_requests_agent_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key_hash",
            name="uq_reflection_job_retry_requests_idempotency",
        ),
    )
    op.create_index(
        "ix_reflection_job_retry_requests_job_created",
        "reflection_job_retry_requests",
        ["job_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "reflection_job_operation_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=True),
        sa.Column("target_status", sa.String(length=32), nullable=True),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('success','denied','conflict')",
            name="ck_reflection_job_operation_audit_events_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reflection_job_operation_audit_tenant_created",
        "reflection_job_operation_audit_events",
        ["tenant_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_reflection_job_operation_audit_job_created",
        "reflection_job_operation_audit_events",
        ["job_id", "created_at"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION prevent_reflection_job_operation_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'reflection job operation records are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "reflection_job_retry_requests",
        "reflection_job_operation_audit_events",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_reflection_job_operation_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "reflection_job_operation_audit_events",
        "reflection_job_retry_requests",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_append_only ON {table_name}")
    op.execute("DROP FUNCTION prevent_reflection_job_operation_mutation()")
    op.drop_index(
        "ix_reflection_job_operation_audit_job_created",
        table_name="reflection_job_operation_audit_events",
    )
    op.drop_index(
        "ix_reflection_job_operation_audit_tenant_created",
        table_name="reflection_job_operation_audit_events",
    )
    op.drop_table("reflection_job_operation_audit_events")
    op.drop_index(
        "ix_reflection_job_retry_requests_job_created",
        table_name="reflection_job_retry_requests",
    )
    op.drop_table("reflection_job_retry_requests")
    op.drop_constraint("uq_outbox_jobs_id_tenant", "outbox_jobs", type_="unique")
    op.drop_constraint(
        "ck_outbox_jobs_attempts_in_cycle",
        "outbox_jobs",
        type_="check",
    )
    op.drop_constraint("ck_outbox_jobs_version", "outbox_jobs", type_="check")
    op.drop_column("outbox_jobs", "attempts_in_cycle")
    op.drop_column("outbox_jobs", "version")
