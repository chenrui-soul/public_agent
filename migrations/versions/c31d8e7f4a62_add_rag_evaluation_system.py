"""add RAG evaluation system

Revision ID: c31d8e7f4a62
Revises: 8a4f1c2d9e70
Create Date: 2026-08-25 03:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c31d8e7f4a62"
down_revision: str | None = "8a4f1c2d9e70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_evaluation_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("domain_id", sa.String(length=100), nullable=False),
        sa.Column("namespace", sa.String(length=150), nullable=False),
        sa.Column("dataset_name", sa.String(length=200), nullable=False),
        sa.Column("dataset_version", sa.String(length=100), nullable=False),
        sa.Column("dataset_hash", sa.String(length=64), nullable=False),
        sa.Column("report_hash", sa.String(length=64), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("embedding_profile", sa.String(length=100), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column(
            "retriever_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "regression_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("baseline_run_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            "status IN ('passed','failed')",
            name="ck_rag_evaluation_runs_status",
        ),
        sa.CheckConstraint(
            "top_k >= 1 AND top_k <= 20",
            name="ck_rag_evaluation_runs_top_k",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="ck_rag_evaluation_runs_duration",
        ),
        sa.CheckConstraint(
            "embedding_dimensions > 0",
            name="ck_rag_evaluation_runs_embedding_dimensions",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rag_evaluation_runs_scope_dataset_created",
        "rag_evaluation_runs",
        ["tenant_id", "agent_id", "dataset_hash", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_rag_evaluation_runs_profile_status_created",
        "rag_evaluation_runs",
        ["embedding_profile", "embedding_dimensions", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "rag_evaluation_case_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.String(length=150), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column(
            "expected_source_keys",
            postgresql.ARRAY(sa.String(length=300)),
            nullable=False,
        ),
        sa.Column(
            "retrieved_source_keys",
            postgresql.ARRAY(sa.String(length=300)),
            nullable=False,
        ),
        sa.Column("retrieved_hits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "retrieval_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "citation_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=300)), nullable=False),
        sa.Column("difficulty", sa.String(length=16), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            "difficulty IN ('easy','medium','hard')",
            name="ck_rag_evaluation_case_results_difficulty",
        ),
        sa.CheckConstraint(
            "latency_ms >= 0",
            name="ck_rag_evaluation_case_results_latency",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["rag_evaluation_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "case_id",
            name="uq_rag_evaluation_case_results_run_case",
        ),
    )
    op.create_index(
        "ix_rag_evaluation_case_results_run_passed",
        "rag_evaluation_case_results",
        ["run_id", "passed"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rag_evaluation_case_results_run_passed",
        table_name="rag_evaluation_case_results",
    )
    op.drop_table("rag_evaluation_case_results")
    op.drop_index(
        "ix_rag_evaluation_runs_profile_status_created",
        table_name="rag_evaluation_runs",
    )
    op.drop_index(
        "ix_rag_evaluation_runs_scope_dataset_created",
        table_name="rag_evaluation_runs",
    )
    op.drop_table("rag_evaluation_runs")
