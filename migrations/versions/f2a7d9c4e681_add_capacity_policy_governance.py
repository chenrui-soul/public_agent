"""add capacity policy governance

Revision ID: f2a7d9c4e681
Revises: c9f4e2a7b613
Create Date: 2026-08-25 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a7d9c4e681"
down_revision: str | None = "c9f4e2a7b613"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reflection_capacity_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column(
            "source_calibration_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "previous_policy_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','superseded','rolled_back')",
            name="ck_reflection_capacity_policies_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('settings_baseline','calibration')",
            name="ck_reflection_capacity_policies_source_type",
        ),
        sa.CheckConstraint(
            "policy_version >= 1",
            name="ck_reflection_capacity_policies_version",
        ),
        sa.ForeignKeyConstraint(
            ["previous_policy_id"],
            ["reflection_capacity_policies.id"],
            name="fk_reflection_capacity_policies_previous",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_calibration_id"],
            ["reflection_capacity_calibrations.id"],
            name="fk_reflection_capacity_policies_calibration",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_type",
            "handler_version",
            "policy_version",
            name="uq_reflection_capacity_policies_version",
        ),
    )
    op.create_index(
        "uq_reflection_capacity_policies_active",
        "reflection_capacity_policies",
        ["job_type", "handler_version"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_reflection_capacity_policies_handler_created",
        "reflection_capacity_policies",
        ["job_type", "handler_version", "created_at"],
        unique=False,
    )

    op.create_table(
        "reflection_capacity_change_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("calibration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "published_policy_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "proposed_thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_required_seconds", sa.Integer(), nullable=False),
        sa.Column("window_minimum_observations", sa.Integer(), nullable=False),
        sa.Column("window_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "window_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("requested_by", sa.String(length=200), nullable=False),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(length=200), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(length=200), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "effect_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("rolled_back_by", sa.String(length=200), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollback_reason", sa.Text(), nullable=True),
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
            "'pending_window','awaiting_approval','approved','rejected',"
            "'cooling_down','effective','ineffective','rolled_back'"
            ")",
            name="ck_reflection_capacity_change_requests_status",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_reflection_capacity_change_requests_version",
        ),
        sa.CheckConstraint(
            "window_required_seconds BETWEEN 60 AND 2592000",
            name="ck_reflection_capacity_change_requests_window_seconds",
        ),
        sa.CheckConstraint(
            "window_minimum_observations BETWEEN 2 AND 100000",
            name="ck_reflection_capacity_change_requests_window_samples",
        ),
        sa.ForeignKeyConstraint(
            ["base_policy_id"],
            ["reflection_capacity_policies.id"],
            name="fk_reflection_capacity_change_requests_base_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["calibration_id"],
            ["reflection_capacity_calibrations.id"],
            name="fk_reflection_capacity_change_requests_calibration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_policy_id"],
            ["reflection_capacity_policies.id"],
            name="fk_reflection_capacity_change_requests_published_policy",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "calibration_id",
            name="uq_reflection_capacity_change_requests_calibration",
        ),
    )
    op.create_index(
        "ix_reflection_capacity_change_requests_handler_status",
        "reflection_capacity_change_requests",
        ["job_type", "handler_version", "status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reflection_capacity_change_requests_handler_status",
        table_name="reflection_capacity_change_requests",
    )
    op.drop_table("reflection_capacity_change_requests")
    op.drop_index(
        "ix_reflection_capacity_policies_handler_created",
        table_name="reflection_capacity_policies",
    )
    op.drop_index(
        "uq_reflection_capacity_policies_active",
        table_name="reflection_capacity_policies",
    )
    op.drop_table("reflection_capacity_policies")
