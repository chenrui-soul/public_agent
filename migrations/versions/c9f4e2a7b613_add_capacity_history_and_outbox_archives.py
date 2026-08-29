"""add capacity history and partitioned outbox archives

Revision ID: c9f4e2a7b613
Revises: b7e2c4a9d610
Create Date: 2026-08-25 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9f4e2a7b613"
down_revision: str | None = "b7e2c4a9d610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_jobs",
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbox_jobs",
        sa.Column("last_processing_duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outbox_jobs",
        sa.Column(
            "total_processing_duration_ms",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE outbox_jobs "
        "SET last_started_at = COALESCE(last_started_at, updated_at) "
        "WHERE status = 'processing'"
    )
    op.create_check_constraint(
        "ck_outbox_jobs_last_processing_duration",
        "outbox_jobs",
        "last_processing_duration_ms IS NULL OR last_processing_duration_ms >= 0",
    )
    op.create_check_constraint(
        "ck_outbox_jobs_total_processing_duration",
        "outbox_jobs",
        "total_processing_duration_ms >= 0",
    )
    op.create_index(
        "ix_outbox_jobs_handler_completed_duration",
        "outbox_jobs",
        [
            "job_type",
            "handler_version",
            "completed_at",
            "total_processing_duration_ms",
        ],
        unique=False,
    )

    op.create_table(
        "outbox_job_archives",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "result_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("attempts_in_cycle", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_processing_duration_ms", sa.Integer(), nullable=True),
        sa.Column("total_processing_duration_ms", sa.Integer(), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded','dead_letter')",
            name="ck_outbox_job_archives_terminal_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_outbox_job_archives_version"),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_job_archives_attempts"),
        sa.CheckConstraint(
            "attempts_in_cycle >= 0",
            name="ck_outbox_job_archives_attempts_in_cycle",
        ),
        sa.CheckConstraint(
            "total_processing_duration_ms >= 0",
            name="ck_outbox_job_archives_total_processing_duration",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            "completed_at",
            "version",
            name="pk_outbox_job_archives",
        ),
        postgresql_partition_by="RANGE (completed_at)",
    )
    op.execute(
        "CREATE TABLE outbox_job_archives_pre_2020 "
        "PARTITION OF outbox_job_archives FOR VALUES FROM (MINVALUE) TO ('2020-01-01')"
    )
    op.execute(
        "CREATE TABLE outbox_job_archives_2020_2030 "
        "PARTITION OF outbox_job_archives FOR VALUES FROM ('2020-01-01') TO ('2030-01-01')"
    )
    op.execute(
        "CREATE TABLE outbox_job_archives_2030_2040 "
        "PARTITION OF outbox_job_archives FOR VALUES FROM ('2030-01-01') TO ('2040-01-01')"
    )
    op.execute(
        "CREATE TABLE outbox_job_archives_post_2040 "
        "PARTITION OF outbox_job_archives FOR VALUES FROM ('2040-01-01') TO (MAXVALUE)"
    )
    op.create_index(
        "ix_outbox_job_archives_handler_completed",
        "outbox_job_archives",
        ["job_type", "handler_version", "completed_at"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_job_archives_tenant_run",
        "outbox_job_archives",
        ["tenant_id", "run_id"],
        unique=False,
    )

    op.create_table(
        "reflection_capacity_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ready", sa.Integer(), nullable=False),
        sa.Column("processing", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("dead_letter", sa.Integer(), nullable=False),
        sa.Column("oldest_ready_age_seconds", sa.Float(), nullable=False),
        sa.Column("active_workers", sa.Integer(), nullable=False),
        sa.Column("stale_workers", sa.Integer(), nullable=False),
        sa.Column("errored_workers", sa.Integer(), nullable=False),
        sa.Column("processed_jobs", sa.Integer(), nullable=False),
        sa.Column("recommended_workers", sa.Integer(), nullable=False),
        sa.Column("scale_delta", sa.Integer(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('healthy','warning','critical')",
            name="ck_reflection_capacity_observations_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_type",
            "handler_version",
            "observed_at",
            name="uq_reflection_capacity_observations_sample",
        ),
    )
    op.create_index(
        "ix_reflection_capacity_observations_handler_observed",
        "reflection_capacity_observations",
        ["job_type", "handler_version", "observed_at"],
        unique=False,
    )

    op.create_table(
        "reflection_capacity_calibrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False),
        sa.Column("dead_letter_count", sa.Integer(), nullable=False),
        sa.Column("p50_processing_ms", sa.Float(), nullable=False),
        sa.Column("p95_processing_ms", sa.Float(), nullable=False),
        sa.Column("p99_processing_ms", sa.Float(), nullable=False),
        sa.Column("observed_jobs_per_hour", sa.Float(), nullable=False),
        sa.Column(
            "recommendation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sample_count > 0",
            name="ck_reflection_capacity_calibrations_sample_count",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reflection_capacity_calibrations_handler_created",
        "reflection_capacity_calibrations",
        ["job_type", "handler_version", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reflection_capacity_calibrations_handler_created",
        table_name="reflection_capacity_calibrations",
    )
    op.drop_table("reflection_capacity_calibrations")
    op.drop_index(
        "ix_reflection_capacity_observations_handler_observed",
        table_name="reflection_capacity_observations",
    )
    op.drop_table("reflection_capacity_observations")
    op.execute("DROP TABLE outbox_job_archives CASCADE")
    op.drop_index(
        "ix_outbox_jobs_handler_completed_duration",
        table_name="outbox_jobs",
    )
    op.drop_constraint(
        "ck_outbox_jobs_total_processing_duration",
        "outbox_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_outbox_jobs_last_processing_duration",
        "outbox_jobs",
        type_="check",
    )
    op.drop_column("outbox_jobs", "total_processing_duration_ms")
    op.drop_column("outbox_jobs", "last_processing_duration_ms")
    op.drop_column("outbox_jobs", "last_started_at")
