"""add knowledge ingestion jobs

Revision ID: e95f2c7a6b31
Revises: d84e1b6f5a20
Create Date: 2026-08-25 07:12:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "e95f2c7a6b31"
down_revision: str | None = "d84e1b6f5a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain_id", sa.String(length=100), nullable=False),
        sa.Column("namespace", sa.String(length=150), nullable=False),
        sa.Column("source_key", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("source_uri", sa.String(length=2000), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=False),
        sa.Column("source_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(length=32), server_default="parsing", nullable=False),
        sa.Column("parsed_text", sa.Text(), nullable=True),
        sa.Column(
            "parser_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "access_tags",
            postgresql.ARRAY(sa.String(length=100)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("processed_chunks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_chunks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("step_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("step_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
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
            "status IN ('queued','running','succeeded','failed','canceled')",
            name="ck_knowledge_ingestion_jobs_status",
        ),
        sa.CheckConstraint(
            "stage IN ('parsing','embedding','publishing','completed')",
            name="ck_knowledge_ingestion_jobs_stage",
        ),
        sa.CheckConstraint(
            "processed_chunks >= 0 AND total_chunks >= 0 "
            "AND processed_chunks <= total_chunks",
            name="ck_knowledge_ingestion_jobs_progress",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_knowledge_ingestion_jobs_attempts"),
        sa.CheckConstraint(
            "source_bytes IS NULL OR "
            "(octet_length(source_bytes) > 0 AND octet_length(source_bytes) <= 8388608)",
            name="ck_knowledge_ingestion_jobs_source_size",
        ),
        sa.CheckConstraint(
            "source_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_ingestion_jobs_hashes",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND step_token IS NOT NULL "
            "AND step_lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND step_token IS NULL "
            "AND step_lease_expires_at IS NULL)",
            name="ck_knowledge_ingestion_jobs_lease",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND stage = 'completed' "
            "AND document_id IS NOT NULL AND source_bytes IS NULL) OR "
            "(status <> 'succeeded' AND stage <> 'completed' "
            "AND document_id IS NULL)",
            name="ck_knowledge_ingestion_jobs_terminal",
        ),
        sa.CheckConstraint(
            "parsed_text IS NULL OR char_length(parsed_text) <= 2000000",
            name="ck_knowledge_ingestion_jobs_parsed_text_size",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "idempotency_key",
            name="uq_knowledge_ingestion_jobs_scope_idempotency",
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_scope_status_created",
        "knowledge_ingestion_jobs",
        ["tenant_id", "agent_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_status_lease",
        "knowledge_ingestion_jobs",
        ["status", "step_lease_expires_at"],
        unique=False,
    )
    op.create_table(
        "knowledge_ingestion_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.CheckConstraint(
            "start_char >= 0 AND end_char > start_char",
            name="ck_knowledge_ingestion_chunks_char_range",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_knowledge_ingestion_chunks_index",
        ),
        sa.CheckConstraint(
            "char_length(content) > 0",
            name="ck_knowledge_ingestion_chunks_content",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["knowledge_ingestion_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "chunk_index",
            name="uq_knowledge_ingestion_chunks_job_index",
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_chunks_job",
        "knowledge_ingestion_chunks",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_ingestion_chunks_job",
        table_name="knowledge_ingestion_chunks",
    )
    op.drop_table("knowledge_ingestion_chunks")
    op.drop_index(
        "ix_knowledge_ingestion_jobs_status_lease",
        table_name="knowledge_ingestion_jobs",
    )
    op.drop_index(
        "ix_knowledge_ingestion_jobs_scope_status_created",
        table_name="knowledge_ingestion_jobs",
    )
    op.drop_table("knowledge_ingestion_jobs")
