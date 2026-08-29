from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from public_agent.knowledge import (
    ChineseHybridReranker,
    JiebaChineseSegmenter,
    KnowledgeHit,
    KnowledgeQuery,
    lexical_text,
)
from public_agent.storage.knowledge import _index_lexical_text, _rerank_with_fallback

_CASES = json.loads(
    (Path(__file__).parents[1] / "references" / "chinese_rag_cases.json").read_text(
        encoding="utf-8"
    )
)


def _query(*, limit: int = 2) -> KnowledgeQuery:
    return KnowledgeQuery(
        tenant_id="tenant",
        agent_id="support-agent",
        domain_id="support-agent",
        namespace="support-manuals",
        text=_CASES["query"],
        limit=limit,
    )


def _hit(
    *,
    source_key: str,
    title: str,
    content: str,
    score: float,
    semantic_similarity: float,
) -> KnowledgeHit:
    return KnowledgeHit(
        citation_id="K1",
        document_id=uuid4(),
        chunk_id=uuid4(),
        source_key=source_key,
        title=title,
        version="1",
        chunk_index=0,
        content=content,
        score=score,
        semantic_similarity=semantic_similarity,
    )


def _candidates() -> tuple[KnowledgeHit, ...]:
    return (
        _hit(
            source_key="shipping-policy",
            title=_CASES["irrelevant_title"],
            content=_CASES["irrelevant_content"],
            score=0.04,
            semantic_similarity=0.9,
        ),
        _hit(
            source_key="refund-policy",
            title=_CASES["relevant_title"],
            content=_CASES["relevant_content"],
            score=0.02,
            semantic_similarity=0.5,
        ),
    )


def test_jieba_segmenter_is_deterministic_safe_and_versioned() -> None:
    default = JiebaChineseSegmenter()
    custom = JiebaChineseSegmenter(custom_terms=(_CASES["custom_term"],))

    tokens = default.segment("智能体知识沉淀与混合检索 Agent_RAG-2026")

    assert "知识" in tokens
    assert "沉淀" in tokens
    assert "agent" in tokens
    assert "rag" in tokens
    assert "2026" in tokens
    assert all(" " not in token for token in tokens)
    assert default.profile == "jieba-search-v1:5b3d20762f73"
    assert custom.profile != default.profile
    assert _CASES["custom_term"] in custom.segment("智能体工程需要知识沉淀")
    assert custom.segment("Ａｇｅｎｔ＿ＲＡＧ") == ("agent", "rag")  # noqa: RUF001
    assert lexical_text(default, "知识沉淀") == "知识 沉淀"


def test_jieba_segmenter_rejects_invalid_capacity_and_empty_lexical_text() -> None:
    with pytest.raises(ValueError, match="max_terms"):
        JiebaChineseSegmenter(max_terms=0)
    with pytest.raises(ValueError, match="10000 terms"):
        JiebaChineseSegmenter(custom_terms=tuple(f"词条{index}" for index in range(10_001)))
    with pytest.raises(ValueError, match="100 characters"):
        JiebaChineseSegmenter(custom_terms=("知" * 101,))
    with pytest.raises(TypeError, match="string"):
        JiebaChineseSegmenter().segment(123)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="searchable terms"):
        lexical_text(JiebaChineseSegmenter(), "   ")
    assert _index_lexical_text(JiebaChineseSegmenter(), "...") == ""


@pytest.mark.asyncio
async def test_chinese_hybrid_reranker_promotes_high_coverage_candidate() -> None:
    segmenter = JiebaChineseSegmenter()
    reranker = ChineseHybridReranker(segmenter)

    results = await reranker.rerank(_query(), _candidates(), limit=2)

    assert [hit.source_key for hit in results] == ["refund-policy", "shipping-policy"]
    assert results[0].reranker_score is not None
    assert results[0].reranker_score > results[1].reranker_score  # type: ignore[operator]
    assert results[0].reranker_profile == reranker.profile
    assert results[0].metadata["ranking"]["content_coverage"] > 0

    with pytest.raises(ValueError, match="limit"):
        await reranker.rerank(_query(), _candidates(), limit=0)


class _ErrorReranker:
    profile = "test-error-reranker-v1"

    async def rerank(
        self,
        query: KnowledgeQuery,
        candidates: tuple[KnowledgeHit, ...],
        *,
        limit: int,
    ) -> tuple[KnowledgeHit, ...]:
        del query, candidates, limit
        raise RuntimeError("sensitive provider detail")


class _SlowReranker:
    profile = "test-slow-reranker-v1"

    async def rerank(
        self,
        query: KnowledgeQuery,
        candidates: tuple[KnowledgeHit, ...],
        *,
        limit: int,
    ) -> tuple[KnowledgeHit, ...]:
        del query, limit
        await asyncio.sleep(0.05)
        return candidates


class _InvalidReranker:
    profile = "test-invalid-reranker-v1"

    def __init__(self, mode: str) -> None:
        self._mode = mode

    async def rerank(
        self,
        query: KnowledgeQuery,
        candidates: tuple[KnowledgeHit, ...],
        *,
        limit: int,
    ) -> tuple[KnowledgeHit, ...]:
        del query, limit
        valid = candidates[0].model_copy(
            update={"score": 0.8, "reranker_score": 0.8}
        )
        if self._mode == "duplicate":
            return (valid, valid)
        return (valid.model_copy(update={"content": "tampered content"}),)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reranker", "timeout_seconds", "expected_status", "expected_error"),
    [
        (_ErrorReranker(), 1.0, "error", "RuntimeError"),
        (_SlowReranker(), 0.001, "timeout", "TimeoutError"),
        (_InvalidReranker("duplicate"), 1.0, "error", "ValueError"),
        (_InvalidReranker("tamper"), 1.0, "error", "ValueError"),
    ],
)
async def test_reranker_failures_fall_back_to_original_rrf_order(
    reranker: object,
    timeout_seconds: float,
    expected_status: str,
    expected_error: str,
) -> None:
    candidates = _candidates()

    results = await _rerank_with_fallback(
        reranker,  # type: ignore[arg-type]
        reranker.profile,  # type: ignore[attr-defined]
        _query(),
        candidates,
        limit=2,
        timeout_seconds=timeout_seconds,
    )

    assert [hit.source_key for hit in results] == [
        "shipping-policy",
        "refund-policy",
    ]
    assert [hit.score for hit in results] == [0.04, 0.02]
    assert all(hit.reranker_score is None for hit in results)
    assert all(
        hit.reranker_profile == reranker.profile for hit in results  # type: ignore[attr-defined]
    )
    assert all(hit.metadata["ranking"]["status"] == expected_status for hit in results)
    assert all(
        hit.metadata["ranking"]["error_type"] == expected_error for hit in results
    )
    assert "sensitive provider detail" not in json.dumps(
        [hit.metadata for hit in results]
    )
