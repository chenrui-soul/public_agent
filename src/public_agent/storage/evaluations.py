from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.evaluation import (
    RAGAggregateMetrics,
    RAGEvaluationReport,
    RAGEvaluationStore,
)
from public_agent.knowledge.base import EmbeddingProfile
from public_agent.storage.models import (
    AgentModel,
    RAGEvaluationCaseResultModel,
    RAGEvaluationRunModel,
    TenantModel,
)


class PostgresRAGEvaluationStore(RAGEvaluationStore):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def latest_successful_metrics(
        self,
        *,
        dataset_hash: str,
        embedding_profile: EmbeddingProfile,
    ) -> tuple[UUID, RAGAggregateMetrics] | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(RAGEvaluationRunModel)
                .where(
                    RAGEvaluationRunModel.dataset_hash == dataset_hash,
                    RAGEvaluationRunModel.embedding_profile == embedding_profile.name,
                    RAGEvaluationRunModel.embedding_dimensions == embedding_profile.dimensions,
                    RAGEvaluationRunModel.status == "passed",
                )
                .order_by(
                    RAGEvaluationRunModel.completed_at.desc(),
                    RAGEvaluationRunModel.id.desc(),
                )
                .limit(1)
            )
        if row is None:
            return None
        return row.id, RAGAggregateMetrics.model_validate(row.metrics)

    async def save(self, report: RAGEvaluationReport) -> None:
        report_hash = _report_hash(report)
        async with self._sessions() as session, session.begin():
            existing = await session.get(RAGEvaluationRunModel, report.id)
            if existing is not None:
                if existing.report_hash != report_hash:
                    raise ValueError("RAG evaluation run ids are immutable")
                return

            tenant = await session.scalar(
                select(TenantModel).where(TenantModel.slug == report.tenant_id)
            )
            if tenant is None:
                raise KeyError(f"Unknown tenant: {report.tenant_id}")
            agent = await session.scalar(
                select(AgentModel).where(
                    AgentModel.tenant_id == tenant.id,
                    AgentModel.agent_key == report.agent_id,
                )
            )
            if agent is None:
                raise KeyError(
                    f"Unknown agent for tenant {report.tenant_id}: {report.agent_id}"
                )
            if agent.domain_id != report.domain_id:
                raise ValueError("RAG evaluation domain does not match the registered agent")

            session.add(
                RAGEvaluationRunModel(
                    id=report.id,
                    tenant_id=tenant.id,
                    agent_id=agent.id,
                    domain_id=report.domain_id,
                    namespace=report.namespace,
                    dataset_name=report.dataset_name,
                    dataset_version=report.dataset_version,
                    dataset_hash=report.dataset_hash,
                    report_hash=report_hash,
                    top_k=report.top_k,
                    embedding_profile=report.embedding_profile.name,
                    embedding_dimensions=report.embedding_profile.dimensions,
                    retriever_config=report.retriever_config,
                    thresholds=report.thresholds.model_dump(mode="json"),
                    regression_policy=(
                        report.regression_policy.model_dump(mode="json")
                        if report.regression_policy is not None
                        else None
                    ),
                    baseline_run_id=report.baseline_run_id,
                    status=report.status,
                    started_at=report.started_at,
                    completed_at=report.completed_at,
                    duration_ms=report.duration_ms,
                    metrics=report.metrics.model_dump(mode="json"),
                    gate=report.gate.model_dump(mode="json"),
                )
            )
            await session.flush()
            for result in report.cases:
                session.add(
                    RAGEvaluationCaseResultModel(
                        id=uuid4(),
                        run_id=report.id,
                        case_id=result.case_id,
                        query=result.query,
                        expected_source_keys=list(result.expected_source_keys),
                        retrieved_source_keys=list(result.retrieved_source_keys),
                        retrieved_hits=[
                            hit.model_dump(mode="json") for hit in result.retrieved_hits
                        ],
                        retrieval_metrics=result.retrieval.model_dump(mode="json"),
                        citation_metrics=(
                            result.citation.model_dump(mode="json")
                            if result.citation is not None
                            else None
                        ),
                        answer=result.answer,
                        latency_ms=result.latency_ms,
                        passed=result.passed,
                        error_code=result.error_code,
                        tags=list(result.tags),
                        difficulty=result.difficulty,
                        metadata_json=result.metadata,
                    )
                )


def _report_hash(report: RAGEvaluationReport) -> str:
    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
