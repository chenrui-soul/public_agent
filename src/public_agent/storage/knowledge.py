from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from public_agent.knowledge.base import (
    KNOWLEDGE_EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    KnowledgeDocumentRecord,
    KnowledgeHit,
    KnowledgeQuery,
    KnowledgeReranker,
    KnowledgeRetriever,
    KnowledgeWriter,
    PreparedKnowledgeDocument,
    TextSegmenter,
)
from public_agent.knowledge.reranking import ChineseHybridReranker
from public_agent.knowledge.segmentation import JiebaChineseSegmenter
from public_agent.storage.models import (
    AgentModel,
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
    TenantModel,
)

_RRF_K = 60
_CANDIDATE_MULTIPLIER = 4
_MIN_CANDIDATES = 20
_MAX_CANDIDATES = 100


@dataclass(frozen=True, slots=True)
class _KnowledgeScope:
    tenant_id: UUID
    agent_id: UUID
    domain_id: str


@dataclass(slots=True)
class _RankedCandidate:
    chunk: KnowledgeChunkModel
    document: KnowledgeDocumentModel
    score: float = 0.0
    lexical_score: float | None = None
    semantic_similarity: float | None = None


class PostgresKnowledgeRepository(KnowledgeWriter, KnowledgeRetriever):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embeddings: EmbeddingProvider,
        minimum_semantic_similarity: float = 0.15,
        *,
        segmenter: TextSegmenter | None = None,
        reranker: KnowledgeReranker | None = None,
        reranker_timeout_seconds: float = 1.0,
    ) -> None:
        if embeddings.profile.dimensions != KNOWLEDGE_EMBEDDING_DIMENSIONS:
            raise ValueError(
                "PostgreSQL knowledge embeddings must use "
                f"{KNOWLEDGE_EMBEDDING_DIMENSIONS} dimensions"
            )
        if not -1 <= minimum_semantic_similarity <= 1:
            raise ValueError("minimum_semantic_similarity must be between -1 and 1")
        if not 0 < reranker_timeout_seconds <= 30:
            raise ValueError("reranker_timeout_seconds must be between 0 and 30")
        selected_segmenter = segmenter if segmenter is not None else JiebaChineseSegmenter()
        segmenter_profile = selected_segmenter.profile.strip()
        if not segmenter_profile or len(segmenter_profile) > 100:
            raise ValueError("segmenter profile must contain between 1 and 100 characters")
        selected_reranker = (
            reranker if reranker is not None else ChineseHybridReranker(selected_segmenter)
        )
        reranker_profile = selected_reranker.profile.strip()
        if not reranker_profile or len(reranker_profile) > 200:
            raise ValueError("reranker profile must contain between 1 and 200 characters")
        self._sessions = sessions
        self._embeddings = embeddings
        self._minimum_semantic_similarity = minimum_semantic_similarity
        self._segmenter = selected_segmenter
        self._segmenter_profile = segmenter_profile
        self._reranker = selected_reranker
        self._reranker_profile = reranker_profile
        self._reranker_timeout_seconds = reranker_timeout_seconds

    async def publish(self, document: PreparedKnowledgeDocument) -> KnowledgeDocumentRecord:
        if document.embedding_profile != self._embeddings.profile:
            raise ValueError("prepared document embedding profile does not match repository")

        source = document.document
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, source.tenant_id, source.agent_id)
            if source.domain_id != scope.domain_id:
                raise ValueError(
                    "knowledge domain does not match the registered agent domain: "
                    f"expected {scope.domain_id}, got {source.domain_id}"
                )
            await session.execute(
                select(func.pg_advisory_xact_lock(_source_lock_id(document, scope=scope)))
            )
            existing = await session.scalar(
                select(KnowledgeDocumentModel)
                .where(
                    KnowledgeDocumentModel.tenant_id == scope.tenant_id,
                    KnowledgeDocumentModel.agent_id == scope.agent_id,
                    KnowledgeDocumentModel.domain_id == source.domain_id,
                    KnowledgeDocumentModel.namespace == source.namespace,
                    KnowledgeDocumentModel.source_key == source.source_key,
                    KnowledgeDocumentModel.version == source.version,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.content_hash != document.content_hash:
                    raise ValueError(
                        "knowledge document versions are immutable; publish a new version"
                    )
                chunk_count = await session.scalar(
                    select(func.count(KnowledgeChunkModel.id)).where(
                        KnowledgeChunkModel.document_id == existing.id
                    )
                )
                return _document_record(
                    existing,
                    tenant_id=source.tenant_id,
                    agent_id=source.agent_id,
                    chunk_count=int(chunk_count or 0),
                )

            active_documents = tuple(
                (
                    await session.scalars(
                        select(KnowledgeDocumentModel)
                        .where(
                            KnowledgeDocumentModel.tenant_id == scope.tenant_id,
                            KnowledgeDocumentModel.agent_id == scope.agent_id,
                            KnowledgeDocumentModel.domain_id == source.domain_id,
                            KnowledgeDocumentModel.namespace == source.namespace,
                            KnowledgeDocumentModel.source_key == source.source_key,
                            KnowledgeDocumentModel.status == "active",
                        )
                        .with_for_update()
                    )
                ).all()
            )
            for active in active_documents:
                active.status = "superseded"

            row = KnowledgeDocumentModel(
                id=uuid4(),
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                domain_id=source.domain_id,
                namespace=source.namespace,
                source_key=source.source_key,
                title=source.title,
                version=source.version,
                source_uri=source.source_uri,
                content=source.content,
                content_hash=document.content_hash,
                status="active",
                access_tags=list(source.access_tags),
                metadata_json=source.metadata,
            )
            session.add(row)
            await session.flush()
            for chunk in document.chunks:
                session.add(
                    KnowledgeChunkModel(
                        id=uuid4(),
                        tenant_id=scope.tenant_id,
                        agent_id=scope.agent_id,
                        document_id=row.id,
                        domain_id=source.domain_id,
                        namespace=source.namespace,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        lexical_text=_index_lexical_text(self._segmenter, chunk.content),
                        lexical_profile=self._segmenter_profile,
                        start_char=chunk.start_char,
                        end_char=chunk.end_char,
                        embedding_profile=document.embedding_profile.name,
                        embedding_dimensions=document.embedding_profile.dimensions,
                        embedding=list(chunk.embedding),
                        metadata_json={
                            "start_char": chunk.start_char,
                            "end_char": chunk.end_char,
                        },
                    )
                )
            await session.flush()
            return _document_record(
                row,
                tenant_id=source.tenant_id,
                agent_id=source.agent_id,
                chunk_count=len(document.chunks),
            )

    async def reindex_lexical(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        domain_id: str | None = None,
        namespace: str | None = None,
        batch_size: int = 200,
    ) -> int:
        if not 1 <= batch_size <= 1_000:
            raise ValueError("lexical reindex batch_size must be between 1 and 1000")

        updated = 0
        while True:
            async with self._sessions() as session, session.begin():
                scope = await _resolve_scope(session, tenant_id, agent_id)
                if domain_id is not None and domain_id != scope.domain_id:
                    raise ValueError(
                        "lexical reindex domain does not match the registered agent domain"
                    )
                filters: list[ColumnElement[bool]] = [
                    KnowledgeChunkModel.tenant_id == scope.tenant_id,
                    KnowledgeChunkModel.agent_id == scope.agent_id,
                ]
                if domain_id is not None:
                    filters.append(KnowledgeChunkModel.domain_id == domain_id)
                if namespace is not None:
                    filters.append(KnowledgeChunkModel.namespace == namespace)
                filters.append(
                    KnowledgeChunkModel.lexical_profile != self._segmenter_profile
                )
                rows = tuple(
                    (
                        await session.scalars(
                            select(KnowledgeChunkModel)
                            .where(*filters)
                            .order_by(KnowledgeChunkModel.id)
                            .limit(batch_size)
                            .with_for_update(skip_locked=True)
                        )
                    ).all()
                )
                if not rows:
                    break
                for row in rows:
                    row.lexical_text = _index_lexical_text(self._segmenter, row.content)
                    row.lexical_profile = self._segmenter_profile
                updated += len(rows)
        return updated

    async def retrieve(self, query: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        fulltext_query = _fulltext_query(self._segmenter, query.text)
        query_embedding = await self._embeddings.embed(query.text)
        if len(query_embedding) != KNOWLEDGE_EMBEDDING_DIMENSIONS:
            raise ValueError("query embedding dimensions do not match the knowledge index")
        if not all(math.isfinite(value) for value in query_embedding):
            raise ValueError("query embedding contains a non-finite value")

        candidate_limit = min(
            max(query.limit * _CANDIDATE_MULTIPLIER, _MIN_CANDIDATES),
            _MAX_CANDIDATES,
        )
        async with self._sessions() as session:
            scope = await _resolve_scope(session, query.tenant_id, query.agent_id)
            if query.domain_id != scope.domain_id:
                raise ValueError(
                    "knowledge query domain does not match the registered agent domain"
                )
            filters = self._scope_filters(query, scope=scope)
            access_filter = func.cardinality(KnowledgeDocumentModel.access_tags) == 0
            if query.access_tags:
                access_filter = or_(
                    access_filter,
                    KnowledgeDocumentModel.access_tags.op("&&")(list(query.access_tags)),
                )
            filters.append(access_filter)

            tsquery = func.to_tsquery("pg_catalog.simple", fulltext_query)
            lexical_score = func.ts_rank_cd(
                KnowledgeChunkModel.search_vector,
                tsquery,
            ).label("lexical_score")
            lexical_rows = (
                await session.execute(
                    select(KnowledgeChunkModel, KnowledgeDocumentModel, lexical_score)
                    .join(
                        KnowledgeDocumentModel,
                        KnowledgeDocumentModel.id == KnowledgeChunkModel.document_id,
                    )
                    .where(
                        *filters,
                        KnowledgeChunkModel.lexical_profile == self._segmenter_profile,
                        KnowledgeChunkModel.search_vector.op("@@")(tsquery),
                    )
                    .order_by(lexical_score.desc(), KnowledgeChunkModel.id)
                    .limit(candidate_limit)
                )
            ).all()

            distance = KnowledgeChunkModel.embedding.cosine_distance(list(query_embedding))
            semantic_rows = (
                await session.execute(
                    select(KnowledgeChunkModel, KnowledgeDocumentModel, distance.label("distance"))
                    .join(
                        KnowledgeDocumentModel,
                        KnowledgeDocumentModel.id == KnowledgeChunkModel.document_id,
                    )
                    .where(
                        *filters,
                        KnowledgeChunkModel.embedding_profile
                        == self._embeddings.profile.name,
                        KnowledgeChunkModel.embedding_dimensions
                        == KNOWLEDGE_EMBEDDING_DIMENSIONS,
                    )
                    .order_by(distance, KnowledgeChunkModel.id)
                    .limit(candidate_limit)
                )
            ).all()

        ranked: dict[UUID, _RankedCandidate] = {}
        for rank, (chunk, document, score) in enumerate(lexical_rows, start=1):
            candidate = ranked.setdefault(chunk.id, _RankedCandidate(chunk, document))
            candidate.lexical_score = float(score)
            candidate.score += 1.0 / (_RRF_K + rank)
        for rank, (chunk, document, raw_distance) in enumerate(semantic_rows, start=1):
            similarity = max(-1.0, min(1.0, 1.0 - float(raw_distance)))
            if similarity < self._minimum_semantic_similarity:
                continue
            candidate = ranked.setdefault(chunk.id, _RankedCandidate(chunk, document))
            candidate.semantic_similarity = similarity
            candidate.score += 1.0 / (_RRF_K + rank)

        ordered = sorted(
            ranked.values(),
            key=lambda item: (
                item.score,
                item.lexical_score or 0.0,
                (
                    item.semantic_similarity
                    if item.semantic_similarity is not None
                    else -1.0
                ),
                -item.chunk.chunk_index,
            ),
            reverse=True,
        )[:candidate_limit]
        candidates = tuple(
            KnowledgeHit(
                citation_id=f"K{index}",
                document_id=item.document.id,
                chunk_id=item.chunk.id,
                source_key=item.document.source_key,
                title=item.document.title,
                source_uri=item.document.source_uri,
                version=item.document.version,
                chunk_index=item.chunk.chunk_index,
                content=item.chunk.content,
                score=item.score,
                lexical_score=item.lexical_score,
                semantic_similarity=item.semantic_similarity,
                metadata={
                    "document": item.document.metadata_json,
                    "chunk": item.chunk.metadata_json,
                    "retrieval": {
                        "embedding_profile": item.chunk.embedding_profile,
                        "lexical_profile": item.chunk.lexical_profile,
                    },
                },
            )
            for index, item in enumerate(ordered, start=1)
        )
        if not candidates:
            return ()
        validated = await _rerank_with_fallback(
            self._reranker,
            self._reranker_profile,
            query,
            candidates,
            limit=query.limit,
            timeout_seconds=self._reranker_timeout_seconds,
        )
        return tuple(
            hit.model_copy(update={"citation_id": f"K{index}"})
            for index, hit in enumerate(validated, start=1)
        )

    def _scope_filters(
        self,
        query: KnowledgeQuery,
        *,
        scope: _KnowledgeScope,
    ) -> list[ColumnElement[bool]]:
        return [
            KnowledgeChunkModel.tenant_id == scope.tenant_id,
            KnowledgeChunkModel.agent_id == scope.agent_id,
            KnowledgeChunkModel.domain_id == query.domain_id,
            KnowledgeChunkModel.namespace == query.namespace,
            KnowledgeDocumentModel.tenant_id == scope.tenant_id,
            KnowledgeDocumentModel.agent_id == scope.agent_id,
            KnowledgeDocumentModel.domain_id == query.domain_id,
            KnowledgeDocumentModel.namespace == query.namespace,
            KnowledgeDocumentModel.status == "active",
        ]


async def _resolve_scope(
    session: AsyncSession,
    tenant_slug: str,
    agent_key: str,
) -> _KnowledgeScope:
    tenant = await session.scalar(select(TenantModel).where(TenantModel.slug == tenant_slug))
    if tenant is None:
        raise KeyError(f"Unknown tenant: {tenant_slug}")
    agent = await session.scalar(
        select(AgentModel).where(
            AgentModel.tenant_id == tenant.id,
            AgentModel.agent_key == agent_key,
        )
    )
    if agent is None:
        raise KeyError(f"Unknown agent for tenant {tenant_slug}: {agent_key}")
    return _KnowledgeScope(
        tenant_id=tenant.id,
        agent_id=agent.id,
        domain_id=agent.domain_id,
    )


def _source_lock_id(
    document: PreparedKnowledgeDocument,
    *,
    scope: _KnowledgeScope,
) -> int:
    source = document.document
    scoped = "|".join(
        (
            str(scope.tenant_id),
            str(scope.agent_id),
            source.domain_id,
            source.namespace,
            source.source_key,
        )
    )
    digest = hashlib.sha256(scoped.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _document_record(
    row: KnowledgeDocumentModel,
    *,
    tenant_id: str,
    agent_id: str,
    chunk_count: int,
) -> KnowledgeDocumentRecord:
    return KnowledgeDocumentRecord(
        id=row.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        domain_id=row.domain_id,
        namespace=row.namespace,
        source_key=row.source_key,
        title=row.title,
        version=row.version,
        content_hash=row.content_hash,
        chunk_count=chunk_count,
        status=row.status,
        source_uri=row.source_uri,
        access_tags=tuple(row.access_tags),
        metadata=row.metadata_json,
        created_at=row.created_at,
    )


def _fulltext_query(segmenter: TextSegmenter, text: str) -> str:
    unique_terms = tuple(dict.fromkeys(segmenter.segment(text)))[:128]
    if not unique_terms:
        raise ValueError("knowledge query must contain searchable text")
    return " | ".join(unique_terms)


def _validate_reranked_hits(
    candidates: tuple[KnowledgeHit, ...],
    reranked: tuple[KnowledgeHit, ...],
    *,
    limit: int,
    reranker_profile: str,
) -> tuple[KnowledgeHit, ...]:
    if not isinstance(reranked, tuple) or not reranked or len(reranked) > limit:
        raise ValueError("reranker returned an invalid candidate count")
    originals = {candidate.chunk_id: candidate for candidate in candidates}
    seen: set[UUID] = set()
    validated: list[KnowledgeHit] = []
    for hit in reranked:
        if not isinstance(hit, KnowledgeHit) or hit.chunk_id in seen:
            raise ValueError("reranker returned a duplicate or invalid candidate")
        original = originals.get(hit.chunk_id)
        if original is None or _hit_identity(hit) != _hit_identity(original):
            raise ValueError("reranker changed candidate identity or content")
        reranker_score = hit.reranker_score
        if (
            reranker_score is None
            or not math.isfinite(reranker_score)
            or not 0 <= reranker_score <= 1
        ):
            raise ValueError("reranker returned an invalid score")
        seen.add(hit.chunk_id)
        validated.append(
            original.model_copy(
                update={
                    "score": reranker_score,
                    "reranker_score": reranker_score,
                    "reranker_profile": reranker_profile,
                    "metadata": hit.metadata,
                }
            )
        )
    return tuple(validated)


async def _rerank_with_fallback(
    reranker: KnowledgeReranker,
    reranker_profile: str,
    query: KnowledgeQuery,
    candidates: tuple[KnowledgeHit, ...],
    *,
    limit: int,
    timeout_seconds: float,
) -> tuple[KnowledgeHit, ...]:
    try:
        reranked = await asyncio.wait_for(
            reranker.rerank(query, candidates, limit=limit),
            timeout=timeout_seconds,
        )
        return _validate_reranked_hits(
            candidates,
            reranked,
            limit=limit,
            reranker_profile=reranker_profile,
        )
    except TimeoutError as exc:
        return _fallback_hits(
            candidates,
            limit=limit,
            reranker_profile=reranker_profile,
            status="timeout",
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        return _fallback_hits(
            candidates,
            limit=limit,
            reranker_profile=reranker_profile,
            status="error",
            error_type=type(exc).__name__,
        )


def _fallback_hits(
    candidates: tuple[KnowledgeHit, ...],
    *,
    limit: int,
    reranker_profile: str,
    status: str,
    error_type: str,
) -> tuple[KnowledgeHit, ...]:
    fallback: list[KnowledgeHit] = []
    for hit in candidates[:limit]:
        metadata = dict(hit.metadata)
        metadata["ranking"] = {
            "status": status,
            "error_type": error_type,
            "fusion_score": hit.score,
            "reranker_profile": reranker_profile,
        }
        fallback.append(
            hit.model_copy(
                update={
                    "reranker_score": None,
                    "reranker_profile": reranker_profile,
                    "metadata": metadata,
                }
            )
        )
    return tuple(fallback)


def _hit_identity(hit: KnowledgeHit) -> tuple[object, ...]:
    return (
        hit.document_id,
        hit.chunk_id,
        hit.source_key,
        hit.title,
        hit.source_uri,
        hit.version,
        hit.chunk_index,
        hit.content,
        hit.lexical_score,
        hit.semantic_similarity,
    )


def _index_lexical_text(segmenter: TextSegmenter, text: str) -> str:
    return " ".join(segmenter.segment(text))
