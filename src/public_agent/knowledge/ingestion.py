from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from public_agent.knowledge.base import (
    EmbeddingProvider,
    KnowledgeDocumentInput,
    KnowledgeDocumentRecord,
    KnowledgeWriter,
    PreparedKnowledgeChunk,
    PreparedKnowledgeDocument,
)
from public_agent.knowledge.chunking import TextChunker
from public_agent.knowledge.parsing import DocumentParser, DocumentSource


class KnowledgeFileInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    agent_id: str = Field(min_length=1, max_length=100)
    domain_id: str = Field(min_length=1, max_length=100)
    namespace: str = Field(min_length=1, max_length=150)
    source_key: str = Field(min_length=1, max_length=300)
    source: DocumentSource
    title: str | None = Field(default=None, min_length=1, max_length=500)
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


class KnowledgeIngestionService:
    def __init__(
        self,
        *,
        writer: KnowledgeWriter,
        embeddings: EmbeddingProvider,
        chunker: TextChunker | None = None,
        parser: DocumentParser | None = None,
        max_chunks: int = 500,
        embedding_concurrency: int = 8,
        embedding_batch_size: int = 64,
    ) -> None:
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        if embedding_concurrency < 1:
            raise ValueError("embedding_concurrency must be positive")
        if embedding_batch_size < 1:
            raise ValueError("embedding_batch_size must be positive")
        self._writer = writer
        self._embeddings = embeddings
        self._chunker = chunker or TextChunker()
        self._parser = parser or DocumentParser()
        self._max_chunks = max_chunks
        self._embedding_concurrency = embedding_concurrency
        self._embedding_batch_size = embedding_batch_size

    async def ingest(self, document: KnowledgeDocumentInput) -> KnowledgeDocumentRecord:
        try:
            json.dumps(document.metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("knowledge document metadata must be JSON serializable") from exc
        chunks = self._chunker.chunk(document.content, max_chunks=self._max_chunks)
        semaphore = asyncio.Semaphore(self._embedding_concurrency)

        async def embed_batch(contents: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            async with semaphore:
                return await self._embeddings.embed_many(contents)

        batches = tuple(
            tuple(chunk.content for chunk in chunks[start : start + self._embedding_batch_size])
            for start in range(0, len(chunks), self._embedding_batch_size)
        )
        embedded_batches = await asyncio.gather(*(embed_batch(batch) for batch in batches))
        vectors = tuple(vector for batch in embedded_batches for vector in batch)
        if len(vectors) != len(chunks):
            raise ValueError("embedding provider returned an unexpected vector count")
        prepared_chunks: list[PreparedKnowledgeChunk] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._validate_embedding(vector)
            prepared_chunks.append(
                PreparedKnowledgeChunk(
                    **chunk.model_dump(),
                    embedding=vector,
                )
            )

        prepared = PreparedKnowledgeDocument(
            document=document,
            content_hash=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
            embedding_profile=self._embeddings.profile,
            chunks=tuple(prepared_chunks),
        )
        return await self._writer.publish(prepared)

    async def ingest_file(self, request: KnowledgeFileInput) -> KnowledgeDocumentRecord:
        parsed = self._parser.parse(request.source)
        title = request.title or parsed.title or PurePath(parsed.filename).stem
        parser_metadata = {
            **parsed.metadata,
            "filename": parsed.filename,
            "media_type": parsed.media_type,
            "source_hash": parsed.source_hash,
            "parser_profile": parsed.parser_profile,
        }
        metadata = {
            **request.metadata,
            "document_parser": parser_metadata,
        }
        return await self.ingest(
            KnowledgeDocumentInput(
                tenant_id=request.tenant_id,
                agent_id=request.agent_id,
                domain_id=request.domain_id,
                namespace=request.namespace,
                source_key=request.source_key,
                title=title,
                content=parsed.text,
                version=request.version,
                source_uri=request.source_uri,
                access_tags=request.access_tags,
                metadata=metadata,
            )
        )

    def _validate_embedding(self, vector: tuple[float, ...]) -> None:
        if len(vector) != self._embeddings.profile.dimensions:
            raise ValueError(
                "embedding dimensions do not match provider profile: "
                f"expected {self._embeddings.profile.dimensions}, got {len(vector)}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding contains a non-finite value")
