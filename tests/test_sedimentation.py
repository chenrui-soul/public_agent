from uuid import uuid4

import pytest

from public_agent.core.types import AgentSpec, RunContext, RunResult, RunStatus
from public_agent.growth.models import CandidateStatus
from public_agent.growth.pipeline import (
    EvidenceBasedCandidateEvaluator,
    InMemoryKnowledgeAssetPublisher,
    KnowledgeSedimentationPipeline,
    SuccessfulRunKnowledgeExtractor,
)
from public_agent.growth.service import InMemoryLearningStore, LearningService
from public_agent.memory.base import MemoryQuery
from public_agent.memory.in_memory import InMemoryMemoryStore


def build_pipeline() -> tuple[
    KnowledgeSedimentationPipeline,
    InMemoryLearningStore,
    InMemoryMemoryStore,
]:
    learning_store = InMemoryLearningStore()
    memory_store = InMemoryMemoryStore()
    pipeline = KnowledgeSedimentationPipeline(
        learning=LearningService(learning_store),
        learning_store=learning_store,
        extractor=SuccessfulRunKnowledgeExtractor(),
        evaluator=EvidenceBasedCandidateEvaluator(),
        publisher=InMemoryKnowledgeAssetPublisher(
            learning=learning_store,
            memory=memory_store,
        ),
    )
    return pipeline, learning_store, memory_store


def agent_spec() -> AgentSpec:
    return AgentSpec(
        id="tax_assistant",
        name="Tax Assistant",
        version="0.1.0",
        instructions="Help with tax workflows.",
        memory_namespace="tax",
    )


@pytest.mark.asyncio
async def test_successful_run_requires_approval_then_becomes_reusable_and_rollbackable() -> None:
    pipeline, learning_store, memory_store = build_pipeline()
    agent = agent_spec()
    context = RunContext(tenant_id="tenant-a")
    result = RunResult(
        run_id=uuid4(),
        status=RunStatus.SUCCEEDED,
        output="Validate source documents before calculating the tax amount.",
        steps=1,
    )

    candidates = await pipeline.process_run(
        agent=agent,
        context=context,
        task="How should tax be calculated?",
        result=result,
    )

    assert len(candidates) == 1
    assert candidates[0].status is CandidateStatus.AWAITING_APPROVAL
    assert await memory_store.search(
        MemoryQuery(
            tenant_id="tenant-a",
            agent_id=agent.id,
            namespace=agent.memory_namespace,
            text="validate tax documents",
        )
    ) == ()

    active, memory = await pipeline.approve_and_publish(
        candidates[0].id,
        decided_by="reviewer@example.com",
        decision_note="Verified against the domain checklist",
    )
    recalled = await memory_store.search(
        MemoryQuery(
            tenant_id="tenant-a",
            agent_id=agent.id,
            namespace=agent.memory_namespace,
            text="validate tax documents",
        )
    )

    assert active.status is CandidateStatus.ACTIVE
    assert recalled == (memory,)

    rolled_back = await pipeline.rollback(active.id)
    assert rolled_back.status is CandidateStatus.ROLLED_BACK
    assert await memory_store.search(
        MemoryQuery(
            tenant_id="tenant-a",
            agent_id=agent.id,
            namespace=agent.memory_namespace,
            text="validate tax documents",
        )
    ) == ()
    assert (await learning_store.get(active.id)).status is CandidateStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_duplicate_successful_run_does_not_create_another_candidate() -> None:
    pipeline, _, _ = build_pipeline()
    agent = agent_spec()
    context = RunContext(tenant_id="tenant-a")
    output = "Validate source documents before calculating the tax amount."

    first = await pipeline.process_run(
        agent=agent,
        context=context,
        task="First task",
        result=RunResult(run_id=uuid4(), status=RunStatus.SUCCEEDED, output=output),
    )
    second = await pipeline.process_run(
        agent=agent,
        context=context,
        task="Second task",
        result=RunResult(run_id=uuid4(), status=RunStatus.SUCCEEDED, output=output),
    )

    assert len(first) == 1
    assert second == ()


@pytest.mark.asyncio
async def test_failed_run_does_not_create_a_learning_candidate() -> None:
    pipeline, _, _ = build_pipeline()

    candidates = await pipeline.process_run(
        agent=agent_spec(),
        context=RunContext(tenant_id="tenant-a"),
        task="A failed task",
        result=RunResult(
            run_id=uuid4(),
            status=RunStatus.FAILED,
            error="The model provider was unavailable",
        ),
    )

    assert candidates == ()
