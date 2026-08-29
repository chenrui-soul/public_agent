from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from public_agent.config import Settings
from public_agent.growth.governance import (
    CandidateGovernancePolicy,
    CandidateGovernanceQuery,
    CandidateGovernanceService,
)
from public_agent.growth.models import (
    CandidateRisk,
    CandidateStatus,
    CandidateType,
    EvaluationResult,
    LearningCandidate,
)
from public_agent.growth.service import LearningService
from public_agent.memory.base import MemoryQuery
from public_agent.storage.database import Database
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    CandidateGovernanceActionModel,
    CandidateLineageModel,
    LearningCandidateModel,
    MemoryModel,
    TenantModel,
)
from public_agent.storage.repositories import (
    PostgresCandidateGovernanceRepository,
    PostgresKnowledgeAssetPublisher,
    PostgresLearningStore,
    PostgresMemoryStore,
)

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


class PassingEvaluator:
    async def evaluate(self, candidate: LearningCandidate) -> EvaluationResult:
        del candidate
        return EvaluationResult(
            passed=True,
            score=0.92,
            summary="Compatible compression passed the governance regression suite",
        )


def _candidate(
    *,
    tenant_slug: str,
    agent_key: str,
    created_at: datetime,
    content: str,
    importance: float,
    confidence: float,
    status: CandidateStatus = CandidateStatus.APPROVED,
) -> LearningCandidate:
    return LearningCandidate(
        tenant_id=tenant_slug,
        agent_id=agent_key,
        domain_id=agent_key,
        candidate_type=CandidateType.MEMORY,
        risk=CandidateRisk.LOW,
        title=content[:100],
        proposed_change={
            "content": content,
            "namespace": "governance",
            "memory_type": "procedural",
            "importance": importance,
            "confidence": confidence,
            "evidence_event_ids": [],
            "tags": ["governance"],
        },
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )


async def _create_scope(
    database: Database,
    *,
    prefix: str,
) -> tuple[UUID, str, str]:
    tenant_id = uuid4()
    agent_id = uuid4()
    tenant_slug = f"{prefix}-tenant-{tenant_id.hex[:10]}"
    agent_key = f"{prefix}-agent-{agent_id.hex[:10]}"
    async with database.sessions() as session, session.begin():
        session.add(TenantModel(id=tenant_id, slug=tenant_slug, name=f"{prefix} Tenant"))
        await session.flush()
        session.add(
            AgentModel(
                id=agent_id,
                tenant_id=tenant_id,
                agent_key=agent_key,
                name=f"{prefix} Agent",
                domain_id=agent_key,
            )
        )
        await session.flush()
        session.add(
            AgentVersionModel(
                tenant_id=tenant_id,
                agent_id=agent_id,
                version="0.8.0",
                instructions="Exercise candidate lifecycle governance.",
                memory_namespace="governance",
                configuration={},
            )
        )
    return tenant_id, tenant_slug, agent_key


async def _publish(
    candidate: LearningCandidate,
    *,
    learning_store: PostgresLearningStore,
    publisher: PostgresKnowledgeAssetPublisher,
    evaluation_score: float,
) -> tuple[LearningCandidate, UUID]:
    await learning_store.save(candidate)
    await learning_store.save_evaluation(
        candidate.id,
        EvaluationResult(
            passed=True,
            score=evaluation_score,
            summary="Published test candidate",
        ),
    )
    active, memory = await publisher.publish(candidate, decided_by="governance-test")
    return active, memory.id


@pytest.mark.asyncio
async def test_postgres_governance_keyset_expiry_eviction_protection_and_idempotency() -> None:
    database = Database(Settings().database_url)
    tenant_id, tenant_slug, agent_key = await _create_scope(database, prefix="lifecycle")
    as_of = datetime(2026, 8, 25, 6, tzinfo=UTC)
    old = as_of - timedelta(days=120)
    learning_store = PostgresLearningStore(database.sessions)
    publisher = PostgresKnowledgeAssetPublisher(database.sessions)
    memory_store = PostgresMemoryStore(database.sessions)
    repository = PostgresCandidateGovernanceRepository(database.sessions)

    try:
        stale = _candidate(
            tenant_slug=tenant_slug,
            agent_key=agent_key,
            created_at=old,
            content="Stale candidate that never entered evaluation.",
            importance=0.1,
            confidence=0.1,
            status=CandidateStatus.PENDING,
        )
        await learning_store.save(stale)
        low, low_memory_id = await _publish(
            _candidate(
                tenant_slug=tenant_slug,
                agent_key=agent_key,
                created_at=old,
                content="Validate source documents before calculating tax.",
                importance=0.1,
                confidence=0.1,
            ),
            learning_store=learning_store,
            publisher=publisher,
            evaluation_score=0.1,
        )
        protected, protected_memory_id = await _publish(
            _candidate(
                tenant_slug=tenant_slug,
                agent_key=agent_key,
                created_at=old,
                content="Always validate source documents before calculating tax.",
                importance=0.95,
                confidence=0.5,
            ),
            learning_store=learning_store,
            publisher=publisher,
            evaluation_score=0.9,
        )
        recalled = await memory_store.search(
            MemoryQuery(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                namespace="governance",
                text="always validate source documents tax",
                limit=1,
            )
        )
        assert recalled[0].id == protected_memory_id

        first_page = await repository.scan(
            CandidateGovernanceQuery(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                domain_id=agent_key,
                as_of=as_of,
                limit=1,
            )
        )
        assert len(first_page.items) == 1
        assert first_page.next_cursor is not None
        second_page = await repository.scan(
            CandidateGovernanceQuery(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                domain_id=agent_key,
                as_of=as_of,
                limit=5,
                after=first_page.next_cursor,
            )
        )
        assert len(second_page.items) == 2

        service = CandidateGovernanceService(
            repository=repository,
            learning=LearningService(learning_store),
            evaluator=PassingEvaluator(),
            policy=CandidateGovernancePolicy(
                low_value_threshold=0.4,
                compression_min_age_days=0,
            ),
        )
        query = CandidateGovernanceQuery(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            as_of=as_of,
        )
        first_result, concurrent_result = await asyncio.gather(
            service.run_batch(query),
            service.run_batch(query),
        )
        second_result = await service.run_batch(query)

        assert first_result.expired + concurrent_result.expired == 1
        assert first_result.evicted + concurrent_result.evicted == 1
        assert (
            first_result.skipped_reasons.get("high_value_memory", 0)
            + concurrent_result.skipped_reasons.get("high_value_memory", 0)
        ) == 2
        assert first_result.conflicts + concurrent_result.conflicts == 2
        assert second_result.expired == second_result.evicted == 0

        async with database.sessions() as session:
            rows = {
                row.id: row
                for row in (
                    await session.scalars(
                        select(LearningCandidateModel).where(
                            LearningCandidateModel.id.in_((stale.id, low.id, protected.id))
                        )
                    )
                ).all()
            }
            low_memory = await session.get(MemoryModel, low_memory_id)
            protected_memory = await session.get(MemoryModel, protected_memory_id)
            action_count = await session.scalar(
                select(func.count(CandidateGovernanceActionModel.id)).where(
                    CandidateGovernanceActionModel.tenant_id == tenant_id
                )
            )

        assert rows[stale.id].status == CandidateStatus.EXPIRED.value
        assert rows[low.id].status == CandidateStatus.EXPIRED.value
        assert rows[protected.id].status == CandidateStatus.ACTIVE.value
        assert low_memory is not None and low_memory.status == "expired"
        assert protected_memory is not None
        assert protected_memory.status == "active"
        assert protected_memory.recall_count == 1
        assert protected_memory.last_recalled_at is not None
        assert action_count == 2
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_compression_reenters_approval_and_restores_sources_on_rollback() -> None:
    database = Database(Settings().database_url)
    tenant_id, tenant_slug, agent_key = await _create_scope(database, prefix="compression")
    as_of = datetime(2026, 8, 25, 6, tzinfo=UTC)
    old = as_of - timedelta(days=60)
    learning_store = PostgresLearningStore(database.sessions)
    publisher = PostgresKnowledgeAssetPublisher(database.sessions)
    repository = PostgresCandidateGovernanceRepository(database.sessions)

    try:
        first, first_memory_id = await _publish(
            _candidate(
                tenant_slug=tenant_slug,
                agent_key=agent_key,
                created_at=old,
                content="Validate source documents before calculating tax.",
                importance=0.7,
                confidence=0.8,
            ),
            learning_store=learning_store,
            publisher=publisher,
            evaluation_score=0.9,
        )
        second, second_memory_id = await _publish(
            _candidate(
                tenant_slug=tenant_slug,
                agent_key=agent_key,
                created_at=old,
                content="Always validate source documents before calculating tax.",
                importance=0.75,
                confidence=0.82,
            ),
            learning_store=learning_store,
            publisher=publisher,
            evaluation_score=0.91,
        )
        learning = LearningService(learning_store)
        service = CandidateGovernanceService(
            repository=repository,
            learning=learning,
            evaluator=PassingEvaluator(),
            policy=CandidateGovernancePolicy(
                low_value_threshold=0,
                compression_min_age_days=0,
            ),
        )
        result = await service.run_batch(
            CandidateGovernanceQuery(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                domain_id=agent_key,
                as_of=as_of,
            )
        )

        assert len(result.compression_candidates) == 1
        compressed = result.compression_candidates[0]
        assert compressed.status is CandidateStatus.AWAITING_APPROVAL
        assert (await learning_store.get(first.id)).status is CandidateStatus.ACTIVE
        assert (await learning_store.get(second.id)).status is CandidateStatus.ACTIVE

        approved = await learning.approve(compressed.id)
        active_compressed, compressed_memory = await publisher.publish(
            approved,
            decided_by="compression-reviewer",
            decision_note="Compatible source compression approved",
        )
        assert active_compressed.status is CandidateStatus.ACTIVE

        async with database.sessions() as session:
            lineage_rows = tuple(
                (
                    await session.scalars(
                        select(CandidateLineageModel).where(
                            CandidateLineageModel.child_candidate_id == compressed.id
                        )
                    )
                ).all()
            )
            source_rows = tuple(
                (
                    await session.scalars(
                        select(LearningCandidateModel).where(
                            LearningCandidateModel.id.in_((first.id, second.id))
                        )
                    )
                ).all()
            )
            source_memories = tuple(
                (
                    await session.scalars(
                        select(MemoryModel).where(
                            MemoryModel.id.in_((first_memory_id, second_memory_id))
                        )
                    )
                ).all()
            )

        assert len(lineage_rows) == 2
        assert {row.relation_type for row in lineage_rows} == {"compression"}
        assert {row.status for row in source_rows} == {CandidateStatus.DEPRECATED.value}
        assert {row.status for row in source_memories} == {"superseded"}

        rolled_back = await publisher.rollback(
            active_compressed,
            memory_id=compressed_memory.id,
        )
        assert rolled_back.status is CandidateStatus.ROLLED_BACK
        async with database.sessions() as session:
            restored_sources = tuple(
                (
                    await session.scalars(
                        select(LearningCandidateModel).where(
                            LearningCandidateModel.id.in_((first.id, second.id))
                        )
                    )
                ).all()
            )
            restored_memories = tuple(
                (
                    await session.scalars(
                        select(MemoryModel).where(
                            MemoryModel.id.in_((first_memory_id, second_memory_id))
                        )
                    )
                ).all()
            )
        assert {row.status for row in restored_sources} == {CandidateStatus.ACTIVE.value}
        assert {row.status for row in restored_memories} == {"active"}
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        await database.dispose()
