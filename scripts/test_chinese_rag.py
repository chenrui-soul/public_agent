from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from public_agent.knowledge import (
    ChineseHybridReranker,
    JiebaChineseSegmenter,
    KnowledgeHit,
    KnowledgeQuery,
)


async def main() -> None:
    root = Path(__file__).parents[1]
    cases = json.loads(
        (root / "references" / "chinese_rag_cases.json").read_text(encoding="utf-8")
    )
    segmenter = JiebaChineseSegmenter(custom_terms=(cases["custom_term"],))
    reranker = ChineseHybridReranker(segmenter)
    query = KnowledgeQuery(
        tenant_id="tenant",
        agent_id="support-agent",
        domain_id="support-agent",
        namespace="support-manuals",
        text=cases["query"],
        limit=2,
    )
    candidates = (
        _hit(
            source_key="shipping-policy",
            title=cases["irrelevant_title"],
            content=cases["irrelevant_content"],
            score=0.04,
            semantic_similarity=0.9,
        ),
        _hit(
            source_key="refund-policy",
            title=cases["relevant_title"],
            content=cases["relevant_content"],
            score=0.02,
            semantic_similarity=0.5,
        ),
    )

    results = await reranker.rerank(query, candidates, limit=2)
    assert results[0].source_key == "refund-policy"
    assert cases["custom_term"] in segmenter.segment("智能体工程需要知识沉淀")
    print(
        json.dumps(
            {
                "status": "passed",
                "segmenter_profile": segmenter.profile,
                "reranker_profile": reranker.profile,
                "ranking": [hit.source_key for hit in results],
            },
            ensure_ascii=False,
        )
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


if __name__ == "__main__":
    asyncio.run(main())
