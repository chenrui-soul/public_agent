"""add hybrid RAG knowledge store

Revision ID: 8a4f1c2d9e70
Revises: 5f6c2a8d1b90
Create Date: 2026-08-25 03:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "8a4f1c2d9e70"
down_revision: str | None = "5f6c2a8d1b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("domain_id", sa.String(length=100), nullable=False),
        sa.Column("namespace", sa.String(length=150), nullable=False),
        sa.Column("source_key", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("source_uri", sa.String(length=2000), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "access_tags",
            postgresql.ARRAY(sa.String(length=100)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
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
            "status IN ('active','superseded','archived')",
            name="ck_knowledge_documents_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            "domain_id",
            "namespace",
            "source_key",
            "version",
            name="uq_knowledge_documents_scope_source_version",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_scope_status",
        "knowledge_documents",
        ["tenant_id", "agent_id", "domain_id", "namespace", "status"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_access_tags_gin",
        "knowledge_documents",
        ["access_tags"],
        unique=False,
        postgresql_using="gin",
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("domain_id", sa.String(length=100), nullable=False),
        sa.Column("namespace", sa.String(length=150), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('pg_catalog.simple', coalesce(content, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("embedding_profile", sa.String(length=100), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(dim=384), nullable=False),
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
        sa.CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index"),
        sa.CheckConstraint("start_char >= 0", name="ck_knowledge_chunks_start_char"),
        sa.CheckConstraint("end_char > start_char", name="ck_knowledge_chunks_char_range"),
        sa.CheckConstraint(
            "embedding_dimensions = 384",
            name="ck_knowledge_chunks_embedding_dimensions",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunks_document_index",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_scope",
        "knowledge_chunks",
        ["tenant_id", "agent_id", "domain_id", "namespace"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_chunks_search_vector_gin",
        "knowledge_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_search_vector_gin", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_scope", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index(
        "ix_knowledge_documents_access_tags_gin",
        table_name="knowledge_documents",
    )
    op.drop_index(
        "ix_knowledge_documents_scope_status",
        table_name="knowledge_documents",
    )
    op.drop_table("knowledge_documents")
