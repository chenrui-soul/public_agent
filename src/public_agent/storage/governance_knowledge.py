from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.knowledge.base import (
    KNOWLEDGE_EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    KnowledgeHit,
    KnowledgeQuery,
    KnowledgeRetriever,
    TextSegmenter,
)
from public_agent.knowledge.embeddings import DeterministicHashEmbeddingProvider
from public_agent.knowledge.segmentation import JiebaChineseSegmenter
from public_agent.operations.capacity_control import (
    GOVERNANCE_KNOWLEDGE_ACCESS_TAG,
    GOVERNANCE_KNOWLEDGE_DOMAIN,
    GOVERNANCE_KNOWLEDGE_NAMESPACE,
    CapacityGovernancePostmortemStatus,
)
from public_agent.storage.models import (
    ReflectionCapacityGovernancePostmortemModel,
    TenantModel,
)

_RRF_K = 60
_CANDIDATE_MULTIPLIER = 4
_MIN_CANDIDATES = 20
_MAX_CANDIDATES = 100


@dataclass(slots=True)
class _Candidate:
    row: ReflectionCapacityGovernancePostmortemModel
    score: float = 0.0
    lexical_score: float | None = None
    semantic_similarity: float | None = None


class PostgresGovernanceKnowledgeRetriever(KnowledgeRetriever):
    """Retrieve reviewed governance postmortems as advisory-only RAG evidence."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embeddings: EmbeddingProvider | None = None,
        *,
        segmenter: TextSegmenter | None = None,
        minimum_semantic_similarity: float = 0.15,
    ) -> None:
        self._sessions = sessions
        self._embeddings = embeddings or DeterministicHashEmbeddingProvider()
        if self._embeddings.profile.dimensions != KNOWLEDGE_EMBEDDING_DIMENSIONS:
            raise ValueError(
                "governance knowledge embeddings must match PostgreSQL dimensions"
            )
        if not -1 <= minimum_semantic_similarity <= 1:
            raise ValueError("minimum_semantic_similarity must be between -1 and 1")
        self._segmenter = segmenter or JiebaChineseSegmenter()
        self._minimum_semantic_similarity = minimum_semantic_similarity

    async def retrieve(self, query: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        if (
            query.namespace != GOVERNANCE_KNOWLEDGE_NAMESPACE
            or query.domain_id != GOVERNANCE_KNOWLEDGE_DOMAIN
            or GOVERNANCE_KNOWLEDGE_ACCESS_TAG not in query.access_tags
        ):
            return ()
        terms = tuple(dict.fromkeys(self._segmenter.segment(query.text)))[:128]
        if not terms:
            return ()
        query_embedding = await self._embeddings.embed(query.text)
        if (
            len(query_embedding) != KNOWLEDGE_EMBEDDING_DIMENSIONS
            or not all(math.isfinite(value) for value in query_embedding)
        ):
            raise ValueError("governance knowledge query embedding is invalid")
        candidate_limit = min(
            max(query.limit * _CANDIDATE_MULTIPLIER, _MIN_CANDIDATES),
            _MAX_CANDIDATES,
        )
        async with self._sessions() as session:
            tenant_id = await session.scalar(
                select(TenantModel.id).where(
                    TenantModel.slug == query.tenant_id,
                    TenantModel.active.is_(True),
                )
            )
            if tenant_id is None:
                return ()
            filters = (
                ReflectionCapacityGovernancePostmortemModel.tenant_id == tenant_id,
                ReflectionCapacityGovernancePostmortemModel.status
                == CapacityGovernancePostmortemStatus.PUBLISHED.value,
                ReflectionCapacityGovernancePostmortemModel.knowledge_namespace
                == GOVERNANCE_KNOWLEDGE_NAMESPACE,
            )
            tsquery = func.to_tsquery("pg_catalog.simple", " | ".join(terms))
            lexical_score = func.ts_rank_cd(
                ReflectionCapacityGovernancePostmortemModel.search_vector,
                tsquery,
            ).label("lexical_score")
            lexical_rows = (
                await session.execute(
                    select(ReflectionCapacityGovernancePostmortemModel, lexical_score)
                    .where(
                        *filters,
                        ReflectionCapacityGovernancePostmortemModel.lexical_profile
                        == self._segmenter.profile,
                        ReflectionCapacityGovernancePostmortemModel.search_vector.op("@@")(
                            tsquery
                        ),
                    )
                    .order_by(
                        lexical_score.desc(),
                        ReflectionCapacityGovernancePostmortemModel.id,
                    )
                    .limit(candidate_limit)
                )
            ).all()
            distance = ReflectionCapacityGovernancePostmortemModel.embedding.cosine_distance(
                list(query_embedding)
            )
            semantic_rows = (
                await session.execute(
                    select(
                        ReflectionCapacityGovernancePostmortemModel,
                        distance.label("distance"),
                    )
                    .where(
                        *filters,
                        ReflectionCapacityGovernancePostmortemModel.embedding_profile
                        == self._embeddings.profile.name,
                        ReflectionCapacityGovernancePostmortemModel.embedding_dimensions
                        == KNOWLEDGE_EMBEDDING_DIMENSIONS,
                    )
                    .order_by(
                        distance,
                        ReflectionCapacityGovernancePostmortemModel.id,
                    )
                    .limit(candidate_limit)
                )
            ).all()

        ranked: dict[UUID, _Candidate] = {}
        for rank, (row, raw_score) in enumerate(lexical_rows, start=1):
            candidate = ranked.setdefault(row.id, _Candidate(row=row))
            candidate.lexical_score = float(raw_score)
            candidate.score += 1.0 / (_RRF_K + rank)
        for rank, (row, raw_distance) in enumerate(semantic_rows, start=1):
            similarity = max(-1.0, min(1.0, 1.0 - float(raw_distance)))
            if similarity < self._minimum_semantic_similarity:
                continue
            candidate = ranked.setdefault(row.id, _Candidate(row=row))
            candidate.semantic_similarity = similarity
            candidate.score += 1.0 / (_RRF_K + rank)
        ordered = sorted(
            ranked.values(),
            key=lambda item: (
                item.score,
                item.lexical_score or 0.0,
                item.semantic_similarity if item.semantic_similarity is not None else -1.0,
                str(item.row.id),
            ),
            reverse=True,
        )[: query.limit]
        return tuple(
            _knowledge_hit(candidate, citation_id=f"K{index}")
            for index, candidate in enumerate(ordered, start=1)
        )


def _knowledge_hit(candidate: _Candidate, *, citation_id: str) -> KnowledgeHit:
    row = candidate.row
    if (
        row.knowledge_source_key is None
        or row.knowledge_version is None
        or row.published_content is None
    ):
        raise ValueError("published governance knowledge is incomplete")
    return KnowledgeHit(
        citation_id=citation_id,
        document_id=row.id,
        chunk_id=row.id,
        source_key=row.knowledge_source_key,
        title=f"Governance postmortem: {row.root_cause}",
        source_uri=None,
        version=row.knowledge_version,
        chunk_index=0,
        content=row.published_content,
        score=candidate.score,
        lexical_score=candidate.lexical_score,
        semantic_similarity=candidate.semantic_similarity,
        metadata={
            "incident_id": str(row.incident_id),
            "incident_cycle": row.incident_cycle,
            "incident_version": row.incident_version,
            "remediation_id": str(row.remediation_id),
            "remediation_version": row.remediation_version,
            "postmortem_id": str(row.id),
            "content_fingerprint": row.content_fingerprint,
            "root_cause": row.root_cause,
            "impact": row.impact,
            "prevention": row.prevention,
            "advisory_only": True,
            "authorization_source": False,
            "recovery_evidence": False,
            "execution_instruction": False,
            "retrieval": {
                "embedding_profile": row.embedding_profile,
                "lexical_profile": row.lexical_profile,
            },
        },
    )
