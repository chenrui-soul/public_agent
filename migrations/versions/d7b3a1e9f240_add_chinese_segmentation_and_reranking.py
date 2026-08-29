"""add Chinese segmentation and reranking indexes

Revision ID: d7b3a1e9f240
Revises: c31d8e7f4a62
Create Date: 2026-08-25 05:00:00.000000

"""

import logging
import re
import unicodedata
from collections.abc import Sequence
from uuid import UUID

import jieba  # type: ignore[import-untyped]
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7b3a1e9f240"
down_revision: str | None = "c31d8e7f4a62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_LEXICAL_PROFILE = "jieba-search-v1:5b3d20762f73"
_SAFE_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_MAX_TERMS = 4_096
_BACKFILL_BATCH_SIZE = 500


def upgrade() -> None:
    op.add_column(
        "knowledge_chunks",
        sa.Column("lexical_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("lexical_profile", sa.String(length=100), nullable=True),
    )
    _backfill_lexical_columns()
    op.alter_column("knowledge_chunks", "lexical_text", nullable=False)
    op.alter_column("knowledge_chunks", "lexical_profile", nullable=False)

    op.drop_index("ix_knowledge_chunks_search_vector_gin", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "search_vector")
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('pg_catalog.simple', coalesce(lexical_text, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_search_vector_gin",
        "knowledge_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_knowledge_chunks_lexical_profile",
        "knowledge_chunks",
        ["tenant_id", "agent_id", "lexical_profile"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_lexical_profile", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_search_vector_gin", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "search_vector")
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('pg_catalog.simple', coalesce(content, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_search_vector_gin",
        "knowledge_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.drop_column("knowledge_chunks", "lexical_profile")
    op.drop_column("knowledge_chunks", "lexical_text")


def _backfill_lexical_columns() -> None:
    tokenizer = _build_tokenizer()
    connection = op.get_bind()
    chunks = sa.table(
        "knowledge_chunks",
        sa.column("id", sa.UUID()),
        sa.column("content", sa.Text()),
    )
    update_statement = sa.text(
        "UPDATE knowledge_chunks "
        "SET lexical_text = :lexical_text, lexical_profile = :lexical_profile "
        "WHERE id = :id"
    )
    last_id: UUID | None = None
    while True:
        query = sa.select(chunks.c.id, chunks.c.content).order_by(chunks.c.id).limit(
            _BACKFILL_BATCH_SIZE
        )
        if last_id is not None:
            query = query.where(chunks.c.id > last_id)
        rows = connection.execute(query).all()
        if not rows:
            break
        connection.execute(
            update_statement,
            [
                {
                    "id": row.id,
                    "lexical_text": _segment_text(tokenizer, row.content),
                    "lexical_profile": _DEFAULT_LEXICAL_PROFILE,
                }
                for row in rows
            ],
        )
        last_id = rows[-1].id


def _build_tokenizer() -> jieba.Tokenizer:
    previous_level = jieba.default_logger.level
    jieba.default_logger.setLevel(logging.WARNING)
    try:
        tokenizer = jieba.Tokenizer()
        tokenizer.initialize()
    finally:
        jieba.default_logger.setLevel(previous_level)
    return tokenizer


def _segment_text(tokenizer: jieba.Tokenizer, text: str) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    normalized_text = unicodedata.normalize("NFKC", text).lower()
    for raw_token in tokenizer.cut_for_search(normalized_text, HMM=False):
        for token in _SAFE_TOKEN_PATTERN.findall(raw_token):
            if not token.strip("_") or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
            if len(tokens) >= _MAX_TERMS:
                return " ".join(tokens)
    return " ".join(tokens)
