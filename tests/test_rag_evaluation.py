from __future__ import annotations

import json
import math
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import yaml

from public_agent.evaluation import (
    RAGAggregateMetrics,
    RAGEvaluationCase,
    RAGEvaluationDataset,
    RAGEvaluationReport,
    RAGEvaluator,
    RAGQualityThresholds,
    RAGRegressionPolicy,
    compute_citation_metrics,
    compute_retrieval_metrics,
)
from public_agent.knowledge import EmbeddingProfile, KnowledgeHit, KnowledgeQuery


def hit(
    citation_id: str,
    source_key: str,
    *,
    score: float = 0.02,
) -> KnowledgeHit:
    return KnowledgeHit(
        citation_id=citation_id,
        document_id=uuid4(),
        chunk_id=uuid4(),
        source_key=source_key,
        title=source_key,
        version="1",
        chunk_index=0,
        content=f"content for {source_key}",
        score=score,
        lexical_score=0.5,
        semantic_similarity=0.8,
        reranker_score=0.7,
        reranker_profile="test-reranker-v1",
        metadata={
            "retrieval": {"lexical_profile": "jieba-search-v1:test"},
            "ranking": {"status": "applied", "fusion_score": score},
        },
    )


def dataset_payload() -> dict[str, object]:
    return {
        "name": "support-rag",
        "version": "2026-08-25",
        "tenant_id": "tenant-a",
        "agent_id": "support-agent",
        "domain_id": "support-agent",
        "namespace": "support-manuals",
        "top_k": 3,
        "metadata": {"owner": "support"},
        "cases": [
            {
                "id": "refund-window",
                "query": "What is the refund window?",
                "relevant_source_keys": ["refund-policy"],
                "tags": ["policy"],
                "difficulty": "easy",
            },
            {
                "id": "shipping-speed",
                "query": "How fast is express shipping?",
                "relevant_source_keys": ["shipping-policy"],
                "difficulty": "medium",
            },
        ],
    }


def test_retrieval_metrics_cover_rank_recall_ndcg_and_irrelevant_rate() -> None:
    metrics = compute_retrieval_metrics(
        (
            hit("K1", "noise"),
            hit("K2", "source-a"),
            hit("K3", "source-a"),
            hit("K4", "source-b"),
        ),
        relevant_source_keys=("source-a", "source-b"),
        top_k=4,
    )

    expected_dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal_dcg = 1 + 1 / math.log2(3)
    assert metrics.hit_rate == 1
    assert metrics.recall == 1
    assert metrics.reciprocal_rank == 0.5
    assert metrics.ndcg == pytest.approx(expected_dcg / ideal_dcg)
    assert metrics.irrelevant_retrieval_rate == 0.25


def test_citation_metrics_detect_invalid_and_uncited_claims() -> None:
    hits = (hit("K1", "noise"), hit("K2", "refund-policy"))

    metrics = compute_citation_metrics(
        "Refunds are accepted for 30 days [K2]. This sentence has no source. "
        "Unknown source [K99].",
        hits,
        relevant_source_keys=("refund-policy",),
    )

    assert metrics.valid_citation_rate == pytest.approx(0.5)
    assert metrics.citation_precision == 1
    assert metrics.citation_recall == 1
    assert metrics.source_coverage == 1
    assert metrics.uncited_claim_rate == pytest.approx(1 / 3)


def test_citation_metrics_split_chinese_claims_without_spaces() -> None:
    metrics = compute_citation_metrics(
        "退款期限是三十天[K1]。这一句没有引用。这是一个无效引用[K0]。",
        (hit("K1", "refund-policy"),),
        relevant_source_keys=("refund-policy",),
    )

    assert metrics.valid_citation_rate == pytest.approx(0.5)
    assert metrics.citation_precision == 1
    assert metrics.uncited_claim_rate == pytest.approx(1 / 3)


def test_yaml_and_jsonl_datasets_share_a_stable_content_hash(tmp_path: Path) -> None:
    payload = dataset_payload()
    yaml_path = tmp_path / "support.yaml"
    yaml_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    jsonl_path = tmp_path / "support.jsonl"
    header = {key: value for key, value in payload.items() if key != "cases"}
    records = [
        {"type": "dataset", **header},
        *({"type": "case", **case} for case in payload["cases"]),
    ]
    jsonl_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    from_yaml = RAGEvaluationDataset.load(yaml_path)
    from_jsonl = RAGEvaluationDataset.load(jsonl_path)

    assert from_yaml == from_jsonl
    assert from_yaml.content_hash == from_jsonl.content_hash
    assert len(from_yaml.content_hash) == 64


def test_dataset_rejects_duplicate_case_ids() -> None:
    payload = dataset_payload()
    payload["cases"] = [payload["cases"][0], payload["cases"][0]]

    with pytest.raises(ValueError, match="case ids must be unique"):
        RAGEvaluationDataset.model_validate(payload)


class MappingRetriever:
    def __init__(self, results: dict[str, tuple[KnowledgeHit, ...]]) -> None:
        self.results = results
        self.queries: list[KnowledgeQuery] = []

    async def retrieve(self, query: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        self.queries.append(query)
        return self.results[query.text]


class CitingAnswerProvider:
    async def answer(
        self,
        _case: RAGEvaluationCase,
        hits: tuple[KnowledgeHit, ...],
    ) -> str:
        return f"The answer is supported [{hits[0].citation_id}]."


class CapturingEvaluationStore:
    def __init__(
        self,
        baseline: tuple[UUID, RAGAggregateMetrics] | None = None,
    ) -> None:
        self.baseline = baseline
        self.reports: list[RAGEvaluationReport] = []

    async def latest_successful_metrics(
        self,
        *,
        dataset_hash: str,
        embedding_profile: EmbeddingProfile,
    ) -> tuple[UUID, RAGAggregateMetrics] | None:
        assert len(dataset_hash) == 64
        assert embedding_profile.dimensions == 384
        return self.baseline

    async def save(self, report: RAGEvaluationReport) -> None:
        self.reports.append(report)


@pytest.mark.asyncio
async def test_rag_evaluator_runs_quality_gate_and_persists_report() -> None:
    dataset = RAGEvaluationDataset.model_validate(dataset_payload())
    retriever = MappingRetriever(
        {
            "What is the refund window?": (hit("K1", "refund-policy"),),
            "How fast is express shipping?": (hit("K1", "shipping-policy"),),
        }
    )
    store = CapturingEvaluationStore()
    evaluator = RAGEvaluator(
        retriever=retriever,
        embedding_profile=EmbeddingProfile(name="openai:text-embedding-3-small"),
        store=store,
        answer_provider=CitingAnswerProvider(),
        max_concurrency=2,
    )

    report = await evaluator.run(
        dataset,
        thresholds=RAGQualityThresholds(
            min_hit_rate_at_k=1,
            min_recall_at_k=1,
            min_mrr_at_k=1,
            min_ndcg_at_k=1,
            max_irrelevant_retrieval_rate=0,
            min_valid_citation_rate=1,
            min_citation_precision=1,
            min_citation_recall=1,
            min_source_coverage=1,
            max_uncited_claim_rate=0,
        ),
        retriever_config={"rrf_k": 60, "minimum_semantic_similarity": 0.15},
    )

    assert report.status == "passed"
    assert report.gate.passed is True
    assert report.metrics.hit_rate_at_k == 1
    assert report.metrics.citation_recall == 1
    assert len(report.cases) == 2
    assert report.cases[0].retrieved_hits[0].reranker_score == 0.7
    assert report.cases[0].retrieved_hits[0].reranker_profile == "test-reranker-v1"
    assert report.cases[0].retrieved_hits[0].fusion_score == 0.02
    assert report.cases[0].retrieved_hits[0].lexical_profile == "jieba-search-v1:test"
    assert report.cases[0].retrieved_hits[0].reranker_status == "applied"
    assert store.reports == [report]
    assert {query.access_tags for query in retriever.queries} == {()}


@pytest.mark.asyncio
async def test_rag_evaluator_fails_regression_and_isolates_case_errors() -> None:
    dataset = RAGEvaluationDataset.model_validate(dataset_payload())

    class PartiallyFailingRetriever(MappingRetriever):
        async def retrieve(self, query: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
            if "shipping" in query.text:
                raise TimeoutError("sensitive upstream details")
            return (hit("K1", "noise"),)

    baseline_metrics = RAGAggregateMetrics(
        case_count=2,
        successful_case_count=2,
        error_count=0,
        hit_rate_at_k=1,
        recall_at_k=1,
        mrr_at_k=1,
        ndcg_at_k=1,
        irrelevant_retrieval_rate=0,
        mean_latency_ms=10,
        p95_latency_ms=10,
    )
    baseline_run_id = uuid4()
    store = CapturingEvaluationStore((baseline_run_id, baseline_metrics))
    evaluator = RAGEvaluator(
        retriever=PartiallyFailingRetriever({}),
        embedding_profile=EmbeddingProfile(name="openai:text-embedding-3-small"),
        store=store,
    )

    report = await evaluator.run(
        dataset,
        thresholds=RAGQualityThresholds(max_case_errors=0),
        regression_policy=RAGRegressionPolicy(max_quality_drop=0.1),
    )

    assert report.status == "failed"
    assert report.baseline_run_id == baseline_run_id
    assert report.metrics.error_count == 1
    assert report.cases[1].error_code == "TimeoutError"
    assert "sensitive" not in report.model_dump_json()
    assert any(
        check.metric == "regression.hit_rate_at_k" and not check.passed
        for check in report.gate.checks
    )


@pytest.mark.asyncio
async def test_answer_thresholds_require_an_answer_provider() -> None:
    evaluator = RAGEvaluator(
        retriever=MappingRetriever({}),
        embedding_profile=EmbeddingProfile(name="test"),
    )

    with pytest.raises(ValueError, match="require a RAGAnswerProvider"):
        await evaluator.run(
            RAGEvaluationDataset.model_validate(dataset_payload()),
            thresholds=RAGQualityThresholds(min_citation_recall=0.8),
        )


@pytest.mark.asyncio
async def test_regression_policy_requires_a_persistent_store() -> None:
    evaluator = RAGEvaluator(
        retriever=MappingRetriever({}),
        embedding_profile=EmbeddingProfile(name="test"),
    )

    with pytest.raises(ValueError, match="requires a persistent evaluation store"):
        await evaluator.run(
            RAGEvaluationDataset.model_validate(dataset_payload()),
            regression_policy=RAGRegressionPolicy(),
        )


@pytest.mark.asyncio
async def test_invalid_answer_provider_is_isolated_as_a_case_error() -> None:
    class InvalidAnswerProvider:
        async def answer(
            self,
            _case: RAGEvaluationCase,
            _hits: tuple[KnowledgeHit, ...],
        ) -> str:
            return cast(str, 42)

    dataset = RAGEvaluationDataset.model_validate(dataset_payload())
    evaluator = RAGEvaluator(
        retriever=MappingRetriever(
            {
                "What is the refund window?": (hit("K1", "refund-policy"),),
                "How fast is express shipping?": (hit("K1", "shipping-policy"),),
            }
        ),
        embedding_profile=EmbeddingProfile(name="test"),
        answer_provider=InvalidAnswerProvider(),
    )

    report = await evaluator.run(dataset)

    assert report.metrics.error_count == 2
    assert {case.error_code for case in report.cases} == {"TypeError"}
    assert all(case.answer is None for case in report.cases)
