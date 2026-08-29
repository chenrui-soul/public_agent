from __future__ import annotations

from public_agent.knowledge.base import KnowledgeHit, KnowledgeQuery, TextSegmenter


class ChineseHybridReranker:
    """Local deterministic reranker combining token coverage, RRF, and semantics."""

    def __init__(self, segmenter: TextSegmenter) -> None:
        self._segmenter = segmenter
        self._profile = f"zh-hybrid-reranker-v1:{segmenter.profile}"

    @property
    def profile(self) -> str:
        return self._profile

    async def rerank(
        self,
        query: KnowledgeQuery,
        candidates: tuple[KnowledgeHit, ...],
        *,
        limit: int,
    ) -> tuple[KnowledgeHit, ...]:
        if limit < 1:
            raise ValueError("reranker limit must be positive")
        if not candidates:
            return ()

        query_tokens = tuple(dict.fromkeys(self._segmenter.segment(query.text)))[:128]
        if not query_tokens:
            return candidates[:limit]
        query_weight = sum(_token_weight(token) for token in query_tokens)
        max_fusion_score = max(candidate.score for candidate in candidates) or 1.0

        scored: list[tuple[float, float, int, KnowledgeHit]] = []
        for candidate in candidates:
            content_tokens = set(self._segmenter.segment(candidate.content))
            title_tokens = set(self._segmenter.segment(candidate.title))
            content_coverage = _weighted_coverage(
                query_tokens,
                content_tokens,
                total_weight=query_weight,
            )
            title_coverage = _weighted_coverage(
                query_tokens,
                title_tokens,
                total_weight=query_weight,
            )
            semantic_score = (
                (candidate.semantic_similarity + 1.0) / 2.0
                if candidate.semantic_similarity is not None
                else 0.0
            )
            fusion_score = candidate.score / max_fusion_score
            reranker_score = min(
                1.0,
                0.40 * content_coverage
                + 0.20 * title_coverage
                + 0.20 * semantic_score
                + 0.20 * fusion_score,
            )
            metadata = dict(candidate.metadata)
            metadata["ranking"] = {
                "status": "applied",
                "fusion_score": candidate.score,
                "query_token_count": len(query_tokens),
                "content_coverage": content_coverage,
                "title_coverage": title_coverage,
                "semantic_score": semantic_score,
                "reranker_profile": self.profile,
            }
            reranked = candidate.model_copy(
                update={
                    "score": reranker_score,
                    "reranker_score": reranker_score,
                    "reranker_profile": self.profile,
                    "metadata": metadata,
                }
            )
            scored.append(
                (
                    reranker_score,
                    candidate.score,
                    -candidate.chunk_index,
                    reranked,
                )
            )

        scored.sort(key=lambda item: item[:3], reverse=True)
        return tuple(item[3] for item in scored[:limit])


def _weighted_coverage(
    query_tokens: tuple[str, ...],
    candidate_tokens: set[str],
    *,
    total_weight: float,
) -> float:
    if total_weight <= 0:
        return 0.0
    matched_weight = sum(
        _token_weight(token) for token in query_tokens if token in candidate_tokens
    )
    return matched_weight / total_weight


def _token_weight(token: str) -> float:
    if token.isascii():
        return 1.0 if len(token) >= 3 else 0.5
    return 2.0 if len(token) >= 2 else 0.25
