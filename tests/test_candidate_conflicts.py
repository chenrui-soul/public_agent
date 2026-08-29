import asyncio
from collections import deque
from uuid import UUID, uuid4

import pytest

from public_agent.core.types import AgentSpec, RunContext, RunResult, RunStatus
from public_agent.growth.conflicts import ConflictKind, RuleBasedCandidateConflictDetector
from public_agent.growth.models import CandidateStatus, EvaluationResult, LearningCandidate
from public_agent.growth.pipeline import (
    EvidenceBasedCandidateEvaluator,
    ExtractedKnowledge,
    InMemoryKnowledgeAssetPublisher,
    KnowledgeSedimentationPipeline,
    ReflectionContext,
    SuccessfulRunKnowledgeExtractor,
)
from public_agent.growth.service import InMemoryLearningStore, LearningService
from public_agent.memory.base import MemoryQuery, MemoryType
from public_agent.memory.in_memory import InMemoryMemoryStore


class QueueExtractor:
    def __init__(self, *items: ExtractedKnowledge) -> None:
        self._items = deque(items)

    async def extract(self, context: ReflectionContext) -> tuple[ExtractedKnowledge, ...]:
        del context
        return (self._items.popleft(),)


class RejectingEvaluator:
    async def evaluate(self, candidate: LearningCandidate) -> EvaluationResult:
        del candidate
        return EvaluationResult(passed=False, score=0.1, summary="Rejected by test evaluator")


def agent_spec() -> AgentSpec:
    return AgentSpec(
        id="tax_assistant",
        name="Tax Assistant",
        version="0.1.0",
        instructions="Help with tax workflows.",
        memory_namespace="tax",
    )


def build_pipeline(
    *,
    extractor: QueueExtractor | SuccessfulRunKnowledgeExtractor,
    evaluator: EvidenceBasedCandidateEvaluator | RejectingEvaluator | None = None,
) -> tuple[KnowledgeSedimentationPipeline, InMemoryLearningStore, InMemoryMemoryStore]:
    learning_store = InMemoryLearningStore()
    memory_store = InMemoryMemoryStore()
    pipeline = KnowledgeSedimentationPipeline(
        learning=LearningService(learning_store),
        learning_store=learning_store,
        extractor=extractor,
        evaluator=evaluator or EvidenceBasedCandidateEvaluator(),
        publisher=InMemoryKnowledgeAssetPublisher(
            learning=learning_store,
            memory=memory_store,
        ),
        conflict_detector=RuleBasedCandidateConflictDetector(),
    )
    return pipeline, learning_store, memory_store


async def process_item(
    pipeline: KnowledgeSedimentationPipeline,
    *,
    run_id: UUID,
) -> LearningCandidate:
    candidates = await pipeline.process_run(
        agent=agent_spec(),
        context=RunContext(tenant_id="tenant-a"),
        task="How should tax be calculated?",
        result=RunResult(
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            output="Runtime output is not used by the queue extractor.",
        ),
    )
    assert len(candidates) == 1
    return candidates[0]


@pytest.mark.asyncio
async def test_compatible_candidates_merge_with_full_lineage_and_publish_atomically() -> None:
    first_event_id = uuid4()
    second_event_id = uuid4()
    pipeline, learning_store, memory_store = build_pipeline(
        extractor=QueueExtractor(
            ExtractedKnowledge(
                title="Validate documents first",
                content="Validate source documents before calculating tax.",
                memory_type=MemoryType.PROCEDURAL,
                evidence_event_ids=(first_event_id,),
                tags=("tax", "validation"),
            ),
            ExtractedKnowledge(
                title="Always validate documents first",
                content="Always validate source documents before calculating tax.",
                memory_type=MemoryType.PROCEDURAL,
                evidence_event_ids=(second_event_id,),
                tags=("documents", "tax"),
            ),
        )
    )
    first_run_id = uuid4()
    second_run_id = uuid4()

    first = await process_item(pipeline, run_id=first_run_id)
    active_first, first_memory = await pipeline.approve_and_publish(
        first.id,
        decided_by="reviewer",
    )
    second = await process_item(pipeline, run_id=second_run_id)

    assessments = second.proposed_change["conflict_assessments"]
    assert assessments == [
        {
            "candidate_id": str(first.id),
            "kind": ConflictKind.COMPATIBLE.value,
            "score": assessments[0]["score"],
            "reason": assessments[0]["reason"],
            "detector_version": "rules-v1",
        }
    ]

    merged = await pipeline.merge_candidates((second.id, active_first.id))

    assert merged.status is CandidateStatus.AWAITING_APPROVAL
    assert merged.proposed_change["merge"]["source_candidate_ids"] == sorted(
        [str(first.id), str(second.id)]
    )
    assert merged.proposed_change["merge"]["conflict_decision"] == "compatible"
    assert merged.proposed_change["evidence_event_ids"] == sorted(
        [str(first_event_id), str(second_event_id)]
    )
    assert merged.evidence_run_ids == tuple(sorted((first_run_id, second_run_id), key=str))
    assert (await learning_store.get(first.id)).status is CandidateStatus.ACTIVE
    assert (await learning_store.get(second.id)).status is CandidateStatus.AWAITING_APPROVAL

    active_merged, merged_memory = await pipeline.approve_and_publish(
        merged.id,
        decided_by="reviewer",
        decision_note="Compatible evidence merged",
    )

    assert active_merged.status is CandidateStatus.ACTIVE
    assert (await learning_store.get(first.id)).status is CandidateStatus.DEPRECATED
    assert (await learning_store.get(second.id)).status is CandidateStatus.DEPRECATED
    recalled = await memory_store.search(
        MemoryQuery(
            tenant_id="tenant-a",
            agent_id=agent_spec().id,
            namespace=agent_spec().memory_namespace,
            text="validate source documents tax",
        )
    )
    assert recalled == (merged_memory,)
    assert first_memory not in recalled

    rolled_back = await pipeline.rollback(active_merged.id)
    assert rolled_back.status is CandidateStatus.ROLLED_BACK
    assert (await learning_store.get(first.id)).status is CandidateStatus.ACTIVE
    assert (await learning_store.get(second.id)).status is CandidateStatus.AWAITING_APPROVAL
    restored = await memory_store.search(
        MemoryQuery(
            tenant_id="tenant-a",
            agent_id=agent_spec().id,
            namespace=agent_spec().memory_namespace,
            text="validate source documents tax",
        )
    )
    assert restored == (first_memory,)
    assert merged_memory not in restored


@pytest.mark.asyncio
async def test_contradictory_candidates_are_flagged_and_cannot_be_merged() -> None:
    pipeline, _, _ = build_pipeline(
        extractor=QueueExtractor(
            ExtractedKnowledge(
                title="Validate documents",
                content="Validate source documents before calculating tax.",
            ),
            ExtractedKnowledge(
                title="Skip document validation",
                content="Do not validate source documents before calculating tax.",
            ),
        )
    )

    first = await process_item(pipeline, run_id=uuid4())
    second = await process_item(pipeline, run_id=uuid4())

    assert second.proposed_change["conflict_assessments"][0]["kind"] == "contradictory"
    with pytest.raises(ValueError, match="contradictory"):
        await pipeline.merge_candidates((first.id, second.id))


@pytest.mark.asyncio
async def test_rejected_fingerprint_can_be_proposed_again() -> None:
    pipeline, _, _ = build_pipeline(
        extractor=SuccessfulRunKnowledgeExtractor(),
        evaluator=RejectingEvaluator(),
    )
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

    assert len(first) == len(second) == 1
    assert first[0].status is CandidateStatus.REJECTED
    assert second[0].status is CandidateStatus.REJECTED
    assert first[0].id != second[0].id
    assert first[0].fingerprint == second[0].fingerprint


@pytest.mark.asyncio
async def test_rolled_back_fingerprint_can_be_proposed_again() -> None:
    pipeline, _, _ = build_pipeline(extractor=SuccessfulRunKnowledgeExtractor())
    agent = agent_spec()
    context = RunContext(tenant_id="tenant-a")
    output = "Validate source documents before calculating the tax amount."

    first = (
        await pipeline.process_run(
            agent=agent,
            context=context,
            task="First proposal",
            result=RunResult(run_id=uuid4(), status=RunStatus.SUCCEEDED, output=output),
        )
    )[0]
    active, _ = await pipeline.approve_and_publish(first.id, decided_by="reviewer")
    await pipeline.rollback(active.id)
    second = await pipeline.process_run(
        agent=agent,
        context=context,
        task="Proposal after rollback",
        result=RunResult(run_id=uuid4(), status=RunStatus.SUCCEEDED, output=output),
    )

    assert len(second) == 1
    assert second[0].id != first.id
    assert second[0].fingerprint == first.fingerprint
    assert second[0].status is CandidateStatus.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_concurrent_exact_fingerprint_proposals_are_idempotent() -> None:
    pipeline, _, _ = build_pipeline(extractor=SuccessfulRunKnowledgeExtractor())
    agent = agent_spec()
    context = RunContext(tenant_id="tenant-a")
    output = "Validate source documents before calculating the tax amount."

    results = await asyncio.gather(
        *(
            pipeline.process_run(
                agent=agent,
                context=context,
                task=f"Concurrent task {index}",
                result=RunResult(
                    run_id=uuid4(),
                    status=RunStatus.SUCCEEDED,
                    output=output,
                ),
            )
            for index in range(2)
        )
    )

    assert sum(len(result) for result in results) == 1
