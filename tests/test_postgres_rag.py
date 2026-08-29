from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from public_agent.config import Settings
from public_agent.core.events import InMemoryEventSink
from public_agent.core.runtime import AgentRuntime
from public_agent.core.types import AgentSpec, ModelResponse, RunContext, RunStatus
from public_agent.knowledge import (
    DeterministicHashEmbeddingProvider,
    JiebaChineseSegmenter,
    KnowledgeDocumentInput,
    KnowledgeIngestionService,
    KnowledgeQuery,
    TextChunker,
)
from public_agent.providers.testing import ScriptedModelProvider
from public_agent.storage.database import Database
from public_agent.storage.knowledge import PostgresKnowledgeRepository
from public_agent.storage.models import (
    AgentModel,
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
    TenantModel,
)
from public_agent.tools.registry import ToolRegistry

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


def document(
    *,
    tenant_id: str,
    agent_id: str,
    source_key: str,
    title: str,
    content: str,
    version: str = "1",
    access_tags: tuple[str, ...] = (),
) -> KnowledgeDocumentInput:
    return KnowledgeDocumentInput(
        tenant_id=tenant_id,
        agent_id=agent_id,
        domain_id=agent_id,
        namespace="support-manuals",
        source_key=source_key,
        title=title,
        content=content,
        version=version,
        source_uri=f"https://example.test/{source_key}/{version}",
        access_tags=access_tags,
    )


@pytest.mark.asyncio
async def test_postgres_hybrid_rag_ingestion_isolation_and_runtime() -> None:
    database = Database(Settings().database_url)
    tenant_a_id = uuid4()
    tenant_b_id = uuid4()
    tenant_a = f"tenant-{tenant_a_id.hex[:12]}"
    tenant_b = f"tenant-{tenant_b_id.hex[:12]}"
    agent_key = f"support-agent-{uuid4().hex[:12]}"
    embeddings = DeterministicHashEmbeddingProvider()
    repository = PostgresKnowledgeRepository(database.sessions, embeddings)
    ingestion = KnowledgeIngestionService(
        writer=repository,
        embeddings=embeddings,
        chunker=TextChunker(max_chars=180, overlap_chars=30),
    )

    try:
        async with database.sessions() as session, session.begin():
            session.add_all(
                [
                    TenantModel(id=tenant_a_id, slug=tenant_a, name="Tenant A"),
                    TenantModel(id=tenant_b_id, slug=tenant_b, name="Tenant B"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    AgentModel(
                        id=uuid4(),
                        tenant_id=tenant_a_id,
                        agent_key=agent_key,
                        name="Support Agent A",
                        domain_id=agent_key,
                    ),
                    AgentModel(
                        id=uuid4(),
                        tenant_id=tenant_b_id,
                        agent_key=agent_key,
                        name="Support Agent B",
                        domain_id=agent_key,
                    ),
                ]
            )

        initial_document = document(
            tenant_id=tenant_a,
            agent_id=agent_key,
            source_key="refund-policy",
            title="Refund policy",
            content="The refund window is 30 days after delivery. Keep the receipt.",
        )
        first, replayed = await asyncio.gather(
            ingestion.ingest(initial_document),
            ingestion.ingest(initial_document),
        )
        assert replayed.id == first.id

        with pytest.raises(ValueError, match="versions are immutable"):
            await ingestion.ingest(
                document(
                    tenant_id=tenant_a,
                    agent_id=agent_key,
                    source_key="refund-policy",
                    title="Refund policy",
                    content="The refund window changed without a version bump.",
                )
            )

        second = await ingestion.ingest(
            document(
                tenant_id=tenant_a,
                agent_id=agent_key,
                source_key="refund-policy",
                title="Refund policy",
                content="The refund window is 45 days after delivery. Keep the receipt.",
                version="2",
            )
        )
        await ingestion.ingest(
            document(
                tenant_id=tenant_a,
                agent_id=agent_key,
                source_key="finance-discount",
                title="Finance discount",
                content="The private finance discount code is LEDGER-42.",
                access_tags=("finance",),
            )
        )
        await ingestion.ingest(
            document(
                tenant_id=tenant_b,
                agent_id=agent_key,
                source_key="tenant-b-secret",
                title="Tenant B launch",
                content="Tenant B secret launch date is Friday.",
            )
        )

        hits = await repository.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_a,
                agent_id=agent_key,
                domain_id=agent_key,
                namespace="support-manuals",
                text="What is the refund window?",
                limit=3,
            )
        )
        assert hits[0].document_id == second.id
        assert hits[0].version == "2"
        assert hits[0].lexical_score is not None
        assert hits[0].semantic_similarity is not None
        assert all(hit.source_key != "finance-discount" for hit in hits)

        finance_hits = await repository.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_a,
                agent_id=agent_key,
                domain_id=agent_key,
                namespace="support-manuals",
                text="finance discount code",
                limit=5,
                access_tags=("finance",),
            )
        )
        assert any(hit.source_key == "finance-discount" for hit in finance_hits)

        isolated_hits = await repository.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_a,
                agent_id=agent_key,
                domain_id=agent_key,
                namespace="support-manuals",
                text="secret launch date",
                limit=5,
            )
        )
        assert all(hit.source_key != "tenant-b-secret" for hit in isolated_hits)

        events = InMemoryEventSink()
        model = ScriptedModelProvider(
            [ModelResponse(content="The refund window is 45 days [K1].")]
        )
        spec = AgentSpec(
            id=agent_key,
            name="Support Agent",
            version="0.1.0",
            instructions="Answer from the support manuals.",
            memory_namespace="support-memory",
            knowledge_namespace="support-manuals",
            metadata={
                "domain_id": agent_key,
                "policies": {"require_citations": True},
            },
        )
        result = await AgentRuntime(
            model=model,
            tools=ToolRegistry(),
            knowledge=repository,
            events=events,
        ).run(
            agent=spec,
            task="What is the refund window?",
            context=RunContext(tenant_id=tenant_a),
        )
        assert result.status is RunStatus.SUCCEEDED
        assert result.output == "The refund window is 45 days [K1]."
        retrieved_event = next(
            event for event in events.events if event.event_type == "knowledge.retrieved"
        )
        assert retrieved_event.payload["hits"][0]["lexical_profile"].startswith(
            "jieba-search-v1:"
        )
        assert retrieved_event.payload["hits"][0]["reranker_profile"].startswith(
            "zh-hybrid-reranker-v1:"
        )
        assert retrieved_event.payload["hits"][0]["reranker_status"] == "applied"

        async with database.sessions() as session:
            versions = tuple(
                (
                    await session.scalars(
                        select(KnowledgeDocumentModel)
                        .where(
                            KnowledgeDocumentModel.tenant_id == tenant_a_id,
                            KnowledgeDocumentModel.source_key == "refund-policy",
                        )
                        .order_by(KnowledgeDocumentModel.version)
                    )
                ).all()
            )
        assert [(row.version, row.status) for row in versions] == [
            ("1", "superseded"),
            ("2", "active"),
        ]
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(TenantModel).where(TenantModel.id.in_((tenant_a_id, tenant_b_id)))
            )
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_chinese_lexical_recall_and_idempotent_reindex() -> None:
    database = Database(Settings().database_url)
    tenant_uuid = uuid4()
    tenant_slug = f"tenant-{tenant_uuid.hex[:12]}"
    agent_key = f"support-agent-{uuid4().hex[:12]}"
    agent_uuid = uuid4()
    embeddings = DeterministicHashEmbeddingProvider()
    default_segmenter = JiebaChineseSegmenter()
    default_repository = PostgresKnowledgeRepository(
        database.sessions,
        embeddings,
        segmenter=default_segmenter,
    )
    ingestion = KnowledgeIngestionService(
        writer=default_repository,
        embeddings=embeddings,
        chunker=TextChunker(max_chars=180, overlap_chars=30),
    )

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

        await ingestion.ingest(
            document(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                source_key="refund-cn",
                title="退款办理指南",
                content="退款期限为收货后30天，申请退款时必须提供有效发票。",  # noqa: RUF001
            )
        )
        await ingestion.ingest(
            document(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                source_key="shipping-cn",
                title="配送时效说明",
                content="同城配送通常会在两个工作日内送达。",
            )
        )

        query = KnowledgeQuery(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            namespace="support-manuals",
            text="退款期限和发票要求",
            limit=2,
        )
        default_hits = await default_repository.retrieve(query)

        assert default_hits[0].source_key == "refund-cn"
        assert default_hits[0].lexical_score is not None
        assert default_hits[0].reranker_score is not None
        assert default_hits[0].metadata["ranking"]["status"] == "applied"

        custom_segmenter = JiebaChineseSegmenter(custom_terms=("退款期限",))
        custom_repository = PostgresKnowledgeRepository(
            database.sessions,
            embeddings,
            minimum_semantic_similarity=-1,
            segmenter=custom_segmenter,
        )
        before_reindex = await custom_repository.retrieve(query)
        assert any(hit.source_key == "refund-cn" for hit in before_reindex)
        assert all(hit.lexical_score is None for hit in before_reindex)

        updated = await custom_repository.reindex_lexical(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            namespace="support-manuals",
            batch_size=1,
        )
        repeated = await custom_repository.reindex_lexical(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            namespace="support-manuals",
            batch_size=1,
        )
        after_reindex = await custom_repository.retrieve(query)

        assert updated == 2
        assert repeated == 0
        assert after_reindex[0].source_key == "refund-cn"
        assert after_reindex[0].lexical_score is not None
        async with database.sessions() as session:
            profiles = tuple(
                await session.scalars(
                    select(KnowledgeChunkModel.lexical_profile).where(
                        KnowledgeChunkModel.tenant_id == tenant_uuid
                    )
                )
            )
        assert profiles == (custom_segmenter.profile, custom_segmenter.profile)
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()
