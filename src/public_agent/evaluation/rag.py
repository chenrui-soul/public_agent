from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from public_agent.core.types import utc_now
from public_agent.knowledge.base import (
    EmbeddingProfile,
    KnowledgeHit,
    KnowledgeQuery,
    KnowledgeRetriever,
)

_MAX_DATASET_BYTES = 10_000_000
_MAX_DATASET_CASES = 10_000
_CITATION_PATTERN = re.compile(r"\[(K[0-9]+)\]")
_CLAIM_SPLIT_PATTERN = re.compile(
    r"(?<=[.!?])\s+|(?<=[\u3002\uFF01\uFF1F])\s*|\n+"
)


class RAGEvaluationCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=150, pattern=r"^[A-Za-z0-9._-]+$")
    query: str = Field(min_length=1, max_length=20_000)
    relevant_source_keys: tuple[str, ...] = Field(min_length=1, max_length=100)
    access_tags: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relevant_source_keys", "access_tags", "tags")
    @classmethod
    def normalize_string_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if any(len(item) > 300 for item in normalized):
            raise ValueError("evaluation labels must be at most 300 characters")
        return normalized

    @model_validator(mode="after")
    def validate_metadata(self) -> RAGEvaluationCase:
        _ensure_json_value(self.metadata, field_name="evaluation case metadata")
        return self


class RAGEvaluationDataset(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(min_length=1, max_length=100)
    agent_id: str = Field(min_length=1, max_length=100)
    domain_id: str = Field(min_length=1, max_length=100)
    namespace: str = Field(min_length=1, max_length=150)
    top_k: int = Field(default=5, ge=1, le=20)
    cases: tuple[RAGEvaluationCase, ...] = Field(min_length=1, max_length=_MAX_DATASET_CASES)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dataset(self) -> RAGEvaluationDataset:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case ids must be unique")
        _ensure_json_value(self.metadata, field_name="evaluation dataset metadata")
        return self

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def load(cls, path: str | Path) -> RAGEvaluationDataset:
        dataset_path = Path(path)
        raw = dataset_path.read_bytes()
        if len(raw) > _MAX_DATASET_BYTES:
            raise ValueError("RAG evaluation dataset exceeds the 10 MB limit")
        text = raw.decode("utf-8")
        suffix = dataset_path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            payload = yaml.safe_load(text)
        elif suffix == ".jsonl":
            payload = _load_jsonl(text)
        else:
            raise ValueError("RAG evaluation datasets must use YAML or JSONL")
        if not isinstance(payload, dict):
            raise ValueError("RAG evaluation dataset root must be an object")
        return cls.model_validate(payload)


class RAGRetrievalMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    hit_rate: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    ndcg: float = Field(ge=0, le=1)
    irrelevant_retrieval_rate: float = Field(ge=0, le=1)


class RAGCitationMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid_citation_rate: float = Field(ge=0, le=1)
    citation_precision: float = Field(ge=0, le=1)
    citation_recall: float = Field(ge=0, le=1)
    source_coverage: float = Field(ge=0, le=1)
    uncited_claim_rate: float = Field(ge=0, le=1)


class RAGEvaluatedHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    citation_id: str
    document_id: UUID
    chunk_id: UUID
    source_key: str
    version: str
    chunk_index: int = Field(ge=0)
    score: float = Field(ge=0)
    fusion_score: float | None = Field(default=None, ge=0)
    lexical_score: float | None = Field(default=None, ge=0)
    lexical_profile: str | None = Field(default=None, min_length=1, max_length=100)
    semantic_similarity: float | None = Field(default=None, ge=-1, le=1)
    reranker_score: float | None = Field(default=None, ge=0, le=1)
    reranker_profile: str | None = Field(default=None, min_length=1, max_length=200)
    reranker_status: str | None = Field(default=None, min_length=1, max_length=50)


class RAGCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    query: str
    expected_source_keys: tuple[str, ...]
    retrieved_hits: tuple[RAGEvaluatedHit, ...]
    retrieval: RAGRetrievalMetrics
    citation: RAGCitationMetrics | None = None
    answer: str | None = None
    latency_ms: float = Field(ge=0)
    passed: bool
    error_code: str | None = Field(default=None, max_length=100)
    tags: tuple[str, ...] = ()
    difficulty: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def retrieved_source_keys(self) -> tuple[str, ...]:
        return tuple(hit.source_key for hit in self.retrieved_hits)


class RAGAggregateMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_count: int = Field(ge=1)
    successful_case_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    hit_rate_at_k: float = Field(ge=0, le=1)
    recall_at_k: float = Field(ge=0, le=1)
    mrr_at_k: float = Field(ge=0, le=1)
    ndcg_at_k: float = Field(ge=0, le=1)
    irrelevant_retrieval_rate: float = Field(ge=0, le=1)
    mean_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)
    valid_citation_rate: float | None = Field(default=None, ge=0, le=1)
    citation_precision: float | None = Field(default=None, ge=0, le=1)
    citation_recall: float | None = Field(default=None, ge=0, le=1)
    source_coverage: float | None = Field(default=None, ge=0, le=1)
    uncited_claim_rate: float | None = Field(default=None, ge=0, le=1)


class RAGQualityThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_hit_rate_at_k: float = Field(default=0.8, ge=0, le=1)
    min_recall_at_k: float = Field(default=0.7, ge=0, le=1)
    min_mrr_at_k: float = Field(default=0.6, ge=0, le=1)
    min_ndcg_at_k: float = Field(default=0.65, ge=0, le=1)
    max_irrelevant_retrieval_rate: float = Field(default=0.5, ge=0, le=1)
    max_p95_latency_ms: float = Field(default=2_000, gt=0)
    max_case_errors: int = Field(default=0, ge=0)
    min_valid_citation_rate: float | None = Field(default=None, ge=0, le=1)
    min_citation_precision: float | None = Field(default=None, ge=0, le=1)
    min_citation_recall: float | None = Field(default=None, ge=0, le=1)
    min_source_coverage: float | None = Field(default=None, ge=0, le=1)
    max_uncited_claim_rate: float | None = Field(default=None, ge=0, le=1)

    @property
    def requires_answer_metrics(self) -> bool:
        return any(
            value is not None
            for value in (
                self.min_valid_citation_rate,
                self.min_citation_precision,
                self.min_citation_recall,
                self.min_source_coverage,
                self.max_uncited_claim_rate,
            )
        )


class RAGRegressionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_quality_drop: float = Field(default=0.05, ge=0, le=1)
    max_irrelevant_rate_increase: float = Field(default=0.05, ge=0, le=1)
    max_latency_increase_ratio: float = Field(default=0.25, ge=0, le=10)


class RAGGateCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    operator: Literal[">=", "<="]
    actual: float
    threshold: float
    passed: bool
    baseline: float | None = None


class RAGQualityGate(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    checks: tuple[RAGGateCheck, ...]


class RAGEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    dataset_name: str
    dataset_version: str
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant_id: str
    agent_id: str
    domain_id: str
    namespace: str
    top_k: int = Field(ge=1, le=20)
    embedding_profile: EmbeddingProfile
    retriever_config: dict[str, Any]
    thresholds: RAGQualityThresholds
    regression_policy: RAGRegressionPolicy | None = None
    baseline_run_id: UUID | None = None
    status: Literal["passed", "failed"]
    started_at: datetime
    completed_at: datetime
    duration_ms: float = Field(ge=0)
    metrics: RAGAggregateMetrics
    gate: RAGQualityGate
    cases: tuple[RAGCaseResult, ...]


class RAGAnswerProvider(Protocol):
    async def answer(
        self,
        case: RAGEvaluationCase,
        hits: tuple[KnowledgeHit, ...],
    ) -> str:
        """Generate one answer from the retrieved context."""


class RAGEvaluationStore(Protocol):
    async def latest_successful_metrics(
        self,
        *,
        dataset_hash: str,
        embedding_profile: EmbeddingProfile,
    ) -> tuple[UUID, RAGAggregateMetrics] | None:
        """Return the latest passing run for regression comparison."""

    async def save(self, report: RAGEvaluationReport) -> None:
        """Persist an immutable evaluation report and its case results."""


class RAGEvaluator:
    def __init__(
        self,
        *,
        retriever: KnowledgeRetriever,
        embedding_profile: EmbeddingProfile,
        store: RAGEvaluationStore | None = None,
        answer_provider: RAGAnswerProvider | None = None,
        max_concurrency: int = 4,
    ) -> None:
        if not 1 <= max_concurrency <= 64:
            raise ValueError("RAG evaluation concurrency must be between 1 and 64")
        self._retriever = retriever
        self._embedding_profile = embedding_profile
        self._store = store
        self._answer_provider = answer_provider
        self._max_concurrency = max_concurrency

    async def run(
        self,
        dataset: RAGEvaluationDataset,
        *,
        thresholds: RAGQualityThresholds | None = None,
        regression_policy: RAGRegressionPolicy | None = None,
        retriever_config: dict[str, Any] | None = None,
    ) -> RAGEvaluationReport:
        quality_thresholds = thresholds or RAGQualityThresholds()
        if quality_thresholds.requires_answer_metrics and self._answer_provider is None:
            raise ValueError("answer quality thresholds require a RAGAnswerProvider")
        if regression_policy is not None and self._store is None:
            raise ValueError("RAG regression evaluation requires a persistent evaluation store")
        configuration = retriever_config or {}
        _ensure_json_value(configuration, field_name="retriever configuration")

        baseline: tuple[UUID, RAGAggregateMetrics] | None = None
        if regression_policy is not None:
            assert self._store is not None
            baseline = await self._store.latest_successful_metrics(
                dataset_hash=dataset.content_hash,
                embedding_profile=self._embedding_profile,
            )

        started_at = utc_now()
        started_clock = time.perf_counter()
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def evaluate(case: RAGEvaluationCase) -> RAGCaseResult:
            async with semaphore:
                return await self._evaluate_case(dataset, case)

        case_results = tuple(await asyncio.gather(*(evaluate(case) for case in dataset.cases)))
        metrics = _aggregate_metrics(case_results)
        baseline_run_id = baseline[0] if baseline is not None else None
        baseline_metrics = baseline[1] if baseline is not None else None
        gate = _quality_gate(
            metrics,
            thresholds=quality_thresholds,
            baseline=baseline_metrics,
            regression_policy=regression_policy,
        )
        completed_at = utc_now()
        report = RAGEvaluationReport(
            dataset_name=dataset.name,
            dataset_version=dataset.version,
            dataset_hash=dataset.content_hash,
            tenant_id=dataset.tenant_id,
            agent_id=dataset.agent_id,
            domain_id=dataset.domain_id,
            namespace=dataset.namespace,
            top_k=dataset.top_k,
            embedding_profile=self._embedding_profile,
            retriever_config=configuration,
            thresholds=quality_thresholds,
            regression_policy=regression_policy,
            baseline_run_id=baseline_run_id,
            status="passed" if gate.passed else "failed",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=(time.perf_counter() - started_clock) * 1_000,
            metrics=metrics,
            gate=gate,
            cases=case_results,
        )
        if self._store is not None:
            await self._store.save(report)
        return report

    async def _evaluate_case(
        self,
        dataset: RAGEvaluationDataset,
        case: RAGEvaluationCase,
    ) -> RAGCaseResult:
        started = time.perf_counter()
        hits: tuple[KnowledgeHit, ...] = ()
        error_code: str | None = None
        try:
            retrieved = await self._retriever.retrieve(
                KnowledgeQuery(
                    tenant_id=dataset.tenant_id,
                    agent_id=dataset.agent_id,
                    domain_id=dataset.domain_id,
                    namespace=dataset.namespace,
                    text=case.query,
                    limit=dataset.top_k,
                    access_tags=case.access_tags,
                )
            )
            hits = tuple(retrieved)
            if any(not isinstance(hit, KnowledgeHit) for hit in hits):
                raise TypeError("retriever returned an invalid knowledge hit")
        except Exception as exc:
            error_code = type(exc).__name__[:100]
        latency_ms = (time.perf_counter() - started) * 1_000
        retrieval = compute_retrieval_metrics(
            hits,
            relevant_source_keys=case.relevant_source_keys,
            top_k=dataset.top_k,
        )

        answer: str | None = None
        citation: RAGCitationMetrics | None = None
        if error_code is None and self._answer_provider is not None:
            try:
                generated_answer = await self._answer_provider.answer(case, hits)
                if not isinstance(generated_answer, str):
                    raise TypeError("answer provider returned a non-string answer")
                answer = generated_answer
                citation = compute_citation_metrics(
                    answer,
                    hits,
                    relevant_source_keys=case.relevant_source_keys,
                )
            except Exception as exc:
                answer = None
                citation = None
                error_code = type(exc).__name__[:100]

        evaluated_hits = tuple(
            RAGEvaluatedHit(
                citation_id=hit.citation_id,
                document_id=hit.document_id,
                chunk_id=hit.chunk_id,
                source_key=hit.source_key,
                version=hit.version,
                chunk_index=hit.chunk_index,
                score=hit.score,
                fusion_score=_hit_metadata_float(hit, "ranking", "fusion_score"),
                lexical_score=hit.lexical_score,
                lexical_profile=_hit_metadata_str(hit, "retrieval", "lexical_profile"),
                semantic_similarity=hit.semantic_similarity,
                reranker_score=hit.reranker_score,
                reranker_profile=hit.reranker_profile,
                reranker_status=_hit_metadata_str(hit, "ranking", "status"),
            )
            for hit in hits[: dataset.top_k]
        )
        citation_passed = citation is None or citation.citation_recall > 0
        return RAGCaseResult(
            case_id=case.id,
            query=case.query,
            expected_source_keys=case.relevant_source_keys,
            retrieved_hits=evaluated_hits,
            retrieval=retrieval,
            citation=citation,
            answer=answer,
            latency_ms=latency_ms,
            passed=error_code is None and retrieval.hit_rate == 1 and citation_passed,
            error_code=error_code,
            tags=case.tags,
            difficulty=case.difficulty,
            metadata=case.metadata,
        )


def _hit_metadata_str(hit: KnowledgeHit, section: str, key: str) -> str | None:
    value = hit.metadata.get(section)
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    return item if isinstance(item, str) else None


def _hit_metadata_float(hit: KnowledgeHit, section: str, key: str) -> float | None:
    value = hit.metadata.get(section)
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        return None
    number = float(item)
    return number if math.isfinite(number) else None


def compute_retrieval_metrics(
    hits: Sequence[KnowledgeHit],
    *,
    relevant_source_keys: Sequence[str],
    top_k: int,
) -> RAGRetrievalMetrics:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    relevant = set(relevant_source_keys)
    if not relevant:
        raise ValueError("relevant source keys must not be empty")
    ranked_hits = tuple(hits[:top_k])
    ranked_sources = tuple(dict.fromkeys(hit.source_key for hit in ranked_hits))
    matched = relevant.intersection(ranked_sources)
    first_relevant_rank = next(
        (rank for rank, hit in enumerate(ranked_hits, start=1) if hit.source_key in relevant),
        None,
    )
    gains: list[float] = []
    seen: set[str] = set()
    for hit in ranked_hits:
        is_new_relevant = hit.source_key in relevant and hit.source_key not in seen
        gains.append(1.0 if is_new_relevant else 0.0)
        seen.add(hit.source_key)
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_relevant = min(len(relevant), top_k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_relevant + 1))
    irrelevant_count = sum(hit.source_key not in relevant for hit in ranked_hits)
    return RAGRetrievalMetrics(
        hit_rate=1.0 if matched else 0.0,
        recall=len(matched) / len(relevant),
        reciprocal_rank=(1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0),
        ndcg=dcg / ideal_dcg if ideal_dcg else 0.0,
        irrelevant_retrieval_rate=(
            irrelevant_count / len(ranked_hits) if ranked_hits else 0.0
        ),
    )


def compute_citation_metrics(
    answer: str,
    hits: Sequence[KnowledgeHit],
    *,
    relevant_source_keys: Sequence[str],
) -> RAGCitationMetrics:
    relevant = set(relevant_source_keys)
    if not relevant:
        raise ValueError("relevant source keys must not be empty")
    citations = tuple(_CITATION_PATTERN.findall(answer))
    by_citation = {hit.citation_id: hit for hit in hits}
    valid = tuple(citation for citation in citations if citation in by_citation)
    relevant_valid = tuple(
        citation for citation in valid if by_citation[citation].source_key in relevant
    )
    cited_relevant_sources = {
        by_citation[citation].source_key for citation in relevant_valid
    }
    claims = tuple(_meaningful_claims(answer))
    uncited_claims = sum(not _CITATION_PATTERN.search(claim) for claim in claims)
    recall = len(cited_relevant_sources) / len(relevant)
    return RAGCitationMetrics(
        valid_citation_rate=len(valid) / len(citations) if citations else 0.0,
        citation_precision=len(relevant_valid) / len(valid) if valid else 0.0,
        citation_recall=recall,
        source_coverage=recall,
        uncited_claim_rate=uncited_claims / len(claims) if claims else 0.0,
    )


def _aggregate_metrics(results: Sequence[RAGCaseResult]) -> RAGAggregateMetrics:
    if not results:
        raise ValueError("RAG evaluation requires at least one case result")
    count = len(results)
    citations = tuple(result.citation for result in results if result.citation is not None)
    latencies = sorted(result.latency_ms for result in results)
    return RAGAggregateMetrics(
        case_count=count,
        successful_case_count=sum(result.error_code is None for result in results),
        error_count=sum(result.error_code is not None for result in results),
        hit_rate_at_k=_mean(result.retrieval.hit_rate for result in results),
        recall_at_k=_mean(result.retrieval.recall for result in results),
        mrr_at_k=_mean(result.retrieval.reciprocal_rank for result in results),
        ndcg_at_k=_mean(result.retrieval.ndcg for result in results),
        irrelevant_retrieval_rate=_mean(
            result.retrieval.irrelevant_retrieval_rate for result in results
        ),
        mean_latency_ms=_mean(latencies),
        p95_latency_ms=_percentile(latencies, 0.95),
        valid_citation_rate=(
            _mean(metric.valid_citation_rate for metric in citations) if citations else None
        ),
        citation_precision=(
            _mean(metric.citation_precision for metric in citations) if citations else None
        ),
        citation_recall=(
            _mean(metric.citation_recall for metric in citations) if citations else None
        ),
        source_coverage=(
            _mean(metric.source_coverage for metric in citations) if citations else None
        ),
        uncited_claim_rate=(
            _mean(metric.uncited_claim_rate for metric in citations) if citations else None
        ),
    )


def _quality_gate(
    metrics: RAGAggregateMetrics,
    *,
    thresholds: RAGQualityThresholds,
    baseline: RAGAggregateMetrics | None,
    regression_policy: RAGRegressionPolicy | None,
) -> RAGQualityGate:
    checks = [
        _minimum_check("hit_rate_at_k", metrics.hit_rate_at_k, thresholds.min_hit_rate_at_k),
        _minimum_check("recall_at_k", metrics.recall_at_k, thresholds.min_recall_at_k),
        _minimum_check("mrr_at_k", metrics.mrr_at_k, thresholds.min_mrr_at_k),
        _minimum_check("ndcg_at_k", metrics.ndcg_at_k, thresholds.min_ndcg_at_k),
        _maximum_check(
            "irrelevant_retrieval_rate",
            metrics.irrelevant_retrieval_rate,
            thresholds.max_irrelevant_retrieval_rate,
        ),
        _maximum_check(
            "p95_latency_ms",
            metrics.p95_latency_ms,
            thresholds.max_p95_latency_ms,
        ),
        _maximum_check(
            "error_count",
            float(metrics.error_count),
            float(thresholds.max_case_errors),
        ),
    ]
    optional_thresholds = (
        (
            "valid_citation_rate",
            metrics.valid_citation_rate,
            thresholds.min_valid_citation_rate,
            ">=",
        ),
        (
            "citation_precision",
            metrics.citation_precision,
            thresholds.min_citation_precision,
            ">=",
        ),
        ("citation_recall", metrics.citation_recall, thresholds.min_citation_recall, ">="),
        ("source_coverage", metrics.source_coverage, thresholds.min_source_coverage, ">="),
        ("uncited_claim_rate", metrics.uncited_claim_rate, thresholds.max_uncited_claim_rate, "<="),
    )
    for name, actual, threshold, operator in optional_thresholds:
        if threshold is None:
            continue
        if actual is None:
            checks.append(
                RAGGateCheck(
                    metric=name,
                    operator=operator,
                    actual=0.0 if operator == ">=" else 1.0,
                    threshold=threshold,
                    passed=False,
                )
            )
        elif operator == ">=":
            checks.append(_minimum_check(name, actual, threshold))
        else:
            checks.append(_maximum_check(name, actual, threshold))

    if baseline is not None and regression_policy is not None:
        for name in ("hit_rate_at_k", "recall_at_k", "mrr_at_k", "ndcg_at_k"):
            actual = float(getattr(metrics, name))
            baseline_value = float(getattr(baseline, name))
            checks.append(
                _minimum_check(
                    f"regression.{name}",
                    actual,
                    max(0.0, baseline_value - regression_policy.max_quality_drop),
                    baseline=baseline_value,
                )
            )
        checks.append(
            _maximum_check(
                "regression.irrelevant_retrieval_rate",
                metrics.irrelevant_retrieval_rate,
                min(
                    1.0,
                    baseline.irrelevant_retrieval_rate
                    + regression_policy.max_irrelevant_rate_increase,
                ),
                baseline=baseline.irrelevant_retrieval_rate,
            )
        )
        checks.append(
            _maximum_check(
                "regression.p95_latency_ms",
                metrics.p95_latency_ms,
                baseline.p95_latency_ms
                * (1.0 + regression_policy.max_latency_increase_ratio),
                baseline=baseline.p95_latency_ms,
            )
        )
    return RAGQualityGate(passed=all(check.passed for check in checks), checks=tuple(checks))


def _minimum_check(
    metric: str,
    actual: float,
    threshold: float,
    *,
    baseline: float | None = None,
) -> RAGGateCheck:
    return RAGGateCheck(
        metric=metric,
        operator=">=",
        actual=actual,
        threshold=threshold,
        passed=actual >= threshold,
        baseline=baseline,
    )


def _maximum_check(
    metric: str,
    actual: float,
    threshold: float,
    *,
    baseline: float | None = None,
) -> RAGGateCheck:
    return RAGGateCheck(
        metric=metric,
        operator="<=",
        actual=actual,
        threshold=threshold,
        passed=actual <= threshold,
        baseline=baseline,
    )


def _mean(values: Iterable[float]) -> float:
    materialized = tuple(float(value) for value in values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(values[lower])
    weight = position - lower
    return float(values[lower] * (1.0 - weight) + values[upper] * weight)


def _meaningful_claims(answer: str) -> Iterator[str]:
    for claim in _CLAIM_SPLIT_PATTERN.split(answer):
        cleaned = _CITATION_PATTERN.sub("", claim).strip()
        if len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned)) >= 5:
            yield claim


def _load_jsonl(text: str) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL line {line_number} must be an object")
        records.append(record)
    if not records or records[0].get("type") != "dataset":
        raise ValueError("JSONL must start with a dataset metadata record")
    header = dict(records[0])
    header.pop("type", None)
    cases: list[dict[str, Any]] = []
    for record in records[1:]:
        if record.get("type") != "case":
            raise ValueError("JSONL records after the header must have type=case")
        case = dict(record)
        case.pop("type", None)
        cases.append(case)
    header["cases"] = cases
    return header


def _ensure_json_value(value: Any, *, field_name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc
