from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from public_agent.config import Settings
from public_agent.evaluation import (
    RAGEvaluationCase,
    RAGEvaluationDataset,
    RAGEvaluator,
    RAGQualityThresholds,
    RAGRegressionPolicy,
)
from public_agent.knowledge import (
    DeterministicHashEmbeddingProvider,
    KnowledgeDocumentInput,
    KnowledgeIngestionService,
)
from public_agent.storage.database import Database
from public_agent.storage.evaluations import PostgresRAGEvaluationStore
from public_agent.storage.knowledge import PostgresKnowledgeRepository
from public_agent.storage.models import (
    AgentModel,
    RAGEvaluationCaseResultModel,
    RAGEvaluationRunModel,
    TenantModel,
)

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_postgres_rag_evaluation_persists_cases_and_uses_regression_baseline() -> None:
    database = Database(Settings().database_url)
    tenant_uuid = uuid4()
    tenant_slug = f"tenant-{tenant_uuid.hex[:12]}"
    agent_key = f"support-agent-{uuid4().hex[:12]}"
    agent_uuid = uuid4()
    embeddings = DeterministicHashEmbeddingProvider()
    knowledge = PostgresKnowledgeRepository(database.sessions, embeddings)
    ingestion = KnowledgeIngestionService(writer=knowledge, embeddings=embeddings)
    evaluation_store = PostgresRAGEvaluationStore(database.sessions)

    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_uuid, slug=tenant_slug, name="Tenant"))
            await session.flush()
            session.add(
                AgentModel(
                    id=agent_uuid,
                    tenant_id=tenant_uuid,
                    agent_key=agent_key,
                    name="Support Agent",
                    domain_id=agent_key,
                )
            )

        for source_key, title, content in (
            (
                "refund-policy",
                "退款政策",
                "退款期限为收货后三十天，申请时需要提供有效发票。",  # noqa: RUF001
            ),
            (
                "shipping-policy",
                "配送政策",
                "加急配送会在两个工作日内送达。",
            ),
        ):
            await ingestion.ingest(
                KnowledgeDocumentInput(
                    tenant_id=tenant_slug,
                    agent_id=agent_key,
                    domain_id=agent_key,
                    namespace="support-manuals",
                    source_key=source_key,
                    title=title,
                    content=content,
                )
            )

        dataset = RAGEvaluationDataset(
            name="support-rag",
            version="1",
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            namespace="support-manuals",
            top_k=2,
            cases=(
                RAGEvaluationCase(
                    id="refund-window",
                    query="退款期限和发票要求",
                    relevant_source_keys=("refund-policy",),
                ),
                RAGEvaluationCase(
                    id="express-shipping",
                    query="加急配送需要几个工作日",
                    relevant_source_keys=("shipping-policy",),
                ),
            ),
        )
        evaluator = RAGEvaluator(
            retriever=knowledge,
            embedding_profile=embeddings.profile,
            store=evaluation_store,
            max_concurrency=2,
        )
        thresholds = RAGQualityThresholds(
            min_hit_rate_at_k=1,
            min_recall_at_k=1,
            min_mrr_at_k=0.5,
            min_ndcg_at_k=0.6,
            max_irrelevant_retrieval_rate=0.5,
            max_p95_latency_ms=5_000,
        )

        first = await evaluator.run(
            dataset,
            thresholds=thresholds,
            retriever_config={
                "rrf_k": 60,
                "minimum_semantic_similarity": 0.15,
                "segmenter_profile": "jieba-search-v1:5b3d20762f73",
            },
        )
        assert first.status == "passed"
        await evaluation_store.save(first)

        second = await evaluator.run(
            dataset,
            thresholds=thresholds,
            regression_policy=RAGRegressionPolicy(
                max_quality_drop=0,
                max_irrelevant_rate_increase=0,
                max_latency_increase_ratio=5,
            ),
        )
        assert second.status == "passed"
        assert second.baseline_run_id == first.id
        assert any(
            check.metric == "regression.hit_rate_at_k" and check.passed
            for check in second.gate.checks
        )

        async with database.sessions() as session:
            runs = tuple(
                (
                    await session.scalars(
                        select(RAGEvaluationRunModel)
                        .where(RAGEvaluationRunModel.tenant_id == tenant_uuid)
                        .order_by(RAGEvaluationRunModel.completed_at)
                    )
                ).all()
            )
            case_count = await session.scalar(
                select(func.count(RAGEvaluationCaseResultModel.id)).where(
                    RAGEvaluationCaseResultModel.run_id.in_((first.id, second.id))
                )
            )
        assert [run.id for run in runs] == [first.id, second.id]
        assert runs[0].embedding_profile == embeddings.profile.name
        assert runs[1].baseline_run_id == first.id
        assert case_count == 4

        changed = first.model_copy(update={"dataset_name": "changed"})
        with pytest.raises(ValueError, match="run ids are immutable"):
            await evaluation_store.save(changed)
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()
