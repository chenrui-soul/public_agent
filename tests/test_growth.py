from uuid import uuid4

import pytest

from public_agent.growth.models import (
    CandidateRisk,
    CandidateStatus,
    CandidateType,
    EvaluationResult,
    LearningCandidate,
)
from public_agent.growth.service import InMemoryLearningStore, LearningService


@pytest.mark.asyncio
async def test_candidate_requires_evaluation_and_approval_before_activation() -> None:
    store = InMemoryLearningStore()
    service = LearningService(store)
    candidate = LearningCandidate(
        tenant_id="tenant-a",
        agent_id="agent-a",
        domain_id="finance",
        candidate_type=CandidateType.STRATEGY,
        risk=CandidateRisk.MEDIUM,
        title="Validate documents before tax calculation",
        proposed_change={"workflow": ["validate", "calculate"]},
        evidence_run_ids=(uuid4(),),
    )

    await service.propose(candidate)
    evaluating = await service.begin_evaluation(candidate.id)
    assert evaluating.status is CandidateStatus.EVALUATING

    awaiting = await service.record_evaluation(
        candidate.id,
        EvaluationResult(passed=True, score=0.93, summary="Regression suite passed"),
    )
    assert awaiting.status is CandidateStatus.AWAITING_APPROVAL

    approved = await service.approve(candidate.id)
    active = await service.activate(candidate.id)
    assert approved.status is CandidateStatus.APPROVED
    assert active.status is CandidateStatus.ACTIVE


@pytest.mark.asyncio
async def test_failed_evaluation_rejects_candidate() -> None:
    service = LearningService(InMemoryLearningStore())
    candidate = LearningCandidate(
        tenant_id="tenant-a",
        agent_id="agent-a",
        domain_id="finance",
        candidate_type=CandidateType.MEMORY,
        risk=CandidateRisk.LOW,
        title="Unverified fact",
        proposed_change={"content": "uncertain"},
    )
    await service.propose(candidate)
    await service.begin_evaluation(candidate.id)
    rejected = await service.record_evaluation(
        candidate.id,
        EvaluationResult(passed=False, score=0.2, summary="Evidence is insufficient"),
    )

    assert rejected.status is CandidateStatus.REJECTED
    with pytest.raises(ValueError, match="Expected awaiting_approval"):
        await service.approve(candidate.id)
