from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from public_agent.core.types import utc_now

KNOWLEDGE_EMBEDDING_DIMENSIONS = 384


class EmbeddingProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    dimensions: int = Field(default=KNOWLEDGE_EMBEDDING_DIMENSIONS, ge=1)


class EmbeddingProvider(Protocol):
    @property
    def profile(self) -> EmbeddingProfile:
        """Return the immutable embedding profile used by this provider."""

    async def embed(self, text: str) -> tuple[float, ...]:
        """Embed one non-empty text value."""

    async def embed_many(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        """Embed texts in input order, returning one vector per value."""


class TextSegmenter(Protocol):
    @property
    def profile(self) -> str:
        """Return the immutable segmentation profile used for lexical indexes."""

    def segment(self, text: str) -> tuple[str, ...]:
        """Return normalized safe tokens in deterministic order."""


class KnowledgeDocumentInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    agent_id: str = Field(min_length=1, max_length=100)
    domain_id: str = Field(min_length=1, max_length=100)
    namespace: str = Field(min_length=1, max_length=150)
    source_key: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=2_000_000)
    version: str = Field(default="1", min_length=1, max_length=100)
    source_uri: str | None = Field(default=None, max_length=2000)
    access_tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("access_tags")
    @classmethod
    def normalize_access_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if any(len(item) > 100 for item in normalized):
            raise ValueError("access tags must be at most 100 characters")
        return normalized


class KnowledgeChunkDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_index: int = Field(ge=0)
    content: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)


class PreparedKnowledgeChunk(KnowledgeChunkDraft):
    embedding: tuple[float, ...]


class PreparedKnowledgeDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document: KnowledgeDocumentInput
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_profile: EmbeddingProfile
    chunks: tuple[PreparedKnowledgeChunk, ...] = Field(min_length=1)


class KnowledgeDocumentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str
    agent_id: str
    domain_id: str
    namespace: str
    source_key: str
    title: str
    version: str
    content_hash: str
    chunk_count: int = Field(ge=1)
    status: str
    source_uri: str | None = None
    access_tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class KnowledgeIngestionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class KnowledgeIngestionStage(StrEnum):
    PARSING = "parsing"
    EMBEDDING = "embedding"
    PUBLISHING = "publishing"
    COMPLETED = "completed"


class KnowledgeIngestionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str
    agent_id: str
    domain_id: str
    namespace: str
    source_key: str
    version: str
    filename: str
    media_type: str
    status: KnowledgeIngestionStatus
    stage: KnowledgeIngestionStage
    processed_chunks: int = Field(ge=0)
    total_chunks: int = Field(ge=0)
    attempts: int = Field(ge=0)
    document_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def has_more(self) -> bool:
        return self.status in {
            KnowledgeIngestionStatus.QUEUED,
            KnowledgeIngestionStatus.RUNNING,
        }


class KnowledgeDocumentPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[KnowledgeDocumentRecord, ...]
    next_cursor: str | None = None


class KnowledgeWriter(Protocol):
    async def publish(self, document: PreparedKnowledgeDocument) -> KnowledgeDocumentRecord:
        """Publish one immutable document version and its chunks atomically."""


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    agent_id: str = Field(min_length=1, max_length=100)
    domain_id: str = Field(min_length=1, max_length=100)
    namespace: str = Field(min_length=1, max_length=150)
    text: str = Field(min_length=1, max_length=20_000)
    limit: int = Field(default=5, ge=1, le=20)
    access_tags: tuple[str, ...] = ()

    @field_validator("access_tags")
    @classmethod
    def normalize_access_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if any(len(item) > 100 for item in normalized):
            raise ValueError("access tags must be at most 100 characters")
        return normalized


class KnowledgeHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    citation_id: str
    document_id: UUID
    chunk_id: UUID
    source_key: str
    title: str
    source_uri: str | None = None
    version: str
    chunk_index: int = Field(ge=0)
    content: str
    score: float = Field(ge=0)
    lexical_score: float | None = Field(default=None, ge=0)
    semantic_similarity: float | None = Field(default=None, ge=-1, le=1)
    reranker_score: float | None = Field(default=None, ge=0, le=1)
    reranker_profile: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeRetriever(Protocol):
    async def retrieve(self, query: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        """Return ranked and scope-filtered external knowledge."""


class KnowledgeReranker(Protocol):
    @property
    def profile(self) -> str:
        """Return the immutable reranker profile used for evaluation and audit."""

    async def rerank(
        self,
        query: KnowledgeQuery,
        candidates: tuple[KnowledgeHit, ...],
        *,
        limit: int,
    ) -> tuple[KnowledgeHit, ...]:
        """Return at most limit candidates ordered by descending relevance."""
