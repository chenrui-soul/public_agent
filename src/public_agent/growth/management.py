from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from public_agent.growth.models import (
    CandidateRisk,
    CandidateStatus,
    CandidateType,
    EvaluationResult,
    LearningCandidate,
)
from public_agent.growth.pipeline import CandidateEvaluator
from public_agent.memory.base import MemoryRecord, MemoryType


class GrowthCursorError(ValueError):
    """Raised when a management keyset cursor is malformed."""


class CandidateStateConflictError(ValueError):
    """Raised when a candidate changed before a guarded management operation."""


class CandidateDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class MemoryManagementQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    agent_id: str
    domain_id: str
    namespace: str | None = None
    memory_type: MemoryType | None = None
    status: str | None = "active"
    text: str | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class MemoryManagementRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str
    agent_id: str
    domain_id: str
    namespace: str
    memory_type: MemoryType
    content: str
    status: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    candidate_id: UUID | None = None
    source_run_id: UUID | None = None
    recall_count: int = Field(ge=0)
    last_recalled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class MemoryManagementPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[MemoryManagementRecord, ...]
    next_cursor: str | None = None


class CandidateManagementQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    agent_id: str
    domain_id: str
    status: CandidateStatus | None = None
    candidate_type: CandidateType | None = None
    risk: CandidateRisk | None = None
    text: str | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CandidateEvaluationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    passed: bool
    score: float = Field(ge=0, le=1)
    summary: str
    metrics: dict[str, float] = Field(default_factory=dict)
    candidate_version: int | None = Field(default=None, ge=1)
    created_at: datetime


class CandidateApprovalRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    status: str
    decided_by: str | None = None
    decision_note: str | None = None
    created_at: datetime
    updated_at: datetime


class PublishedMemoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    status: str
    recall_count: int = Field(ge=0)
    last_recalled_at: datetime | None = None


class CandidateManagementRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: LearningCandidate
    latest_evaluation: CandidateEvaluationRecord | None = None
    latest_approval: CandidateApprovalRecord | None = None
    published_memory: PublishedMemoryRecord | None = None


class CandidateManagementPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CandidateManagementRecord, ...]
    next_cursor: str | None = None


class GrowthManagementRepository(Protocol):
    async def list_memories(self, query: MemoryManagementQuery) -> MemoryManagementPage: ...

    async def list_candidates(
        self,
        query: CandidateManagementQuery,
    ) -> CandidateManagementPage: ...

    async def get_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
    ) -> CandidateManagementRecord: ...

    async def record_evaluation(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
        result: EvaluationResult,
    ) -> CandidateManagementRecord: ...

    async def reject_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
        decided_by: str,
        decision_note: str | None,
    ) -> CandidateManagementRecord: ...


class ManagedKnowledgeAssetPublisher(Protocol):
    async def approve_and_publish_scoped(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
        decided_by: str,
        decision_note: str | None = None,
    ) -> tuple[LearningCandidate, MemoryRecord]: ...

    async def rollback_scoped(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
    ) -> LearningCandidate: ...


class AgentGrowthManagementService:
    def __init__(
        self,
        *,
        repository: GrowthManagementRepository,
        evaluator: CandidateEvaluator,
        publisher: ManagedKnowledgeAssetPublisher,
    ) -> None:
        self._repository = repository
        self._evaluator = evaluator
        self._publisher = publisher

    async def list_memories(self, query: MemoryManagementQuery) -> MemoryManagementPage:
        return await self._repository.list_memories(query)

    async def list_candidates(
        self,
        query: CandidateManagementQuery,
    ) -> CandidateManagementPage:
        return await self._repository.list_candidates(query)

    async def get_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
    ) -> CandidateManagementRecord:
        return await self._repository.get_candidate(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
        )

    async def evaluate_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
    ) -> CandidateManagementRecord:
        record = await self.get_candidate(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
        )
        candidate = record.candidate
        if _is_evaluation_replay(record, expected_version=expected_version):
            return record
        if candidate.version != expected_version or candidate.status is not CandidateStatus.PENDING:
            raise CandidateStateConflictError(
                "Candidate must remain pending at the expected version before evaluation"
            )
        result = await self._evaluator.evaluate(candidate)
        return await self._repository.record_evaluation(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
            expected_version=expected_version,
            result=result,
        )

    async def decide_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
        decision: CandidateDecision,
        decided_by: str,
        decision_note: str | None = None,
    ) -> CandidateManagementRecord:
        if decision is CandidateDecision.REJECTED:
            return await self._repository.reject_candidate(
                candidate_id=candidate_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                domain_id=domain_id,
                expected_version=expected_version,
                decided_by=decided_by,
                decision_note=decision_note,
            )
        await self._publisher.approve_and_publish_scoped(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
            expected_version=expected_version,
            decided_by=decided_by,
            decision_note=decision_note,
        )
        return await self.get_candidate(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
        )

    async def rollback_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
    ) -> CandidateManagementRecord:
        await self._publisher.rollback_scoped(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
            expected_version=expected_version,
        )
        return await self.get_candidate(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
        )


def _is_evaluation_replay(
    record: CandidateManagementRecord,
    *,
    expected_version: int,
) -> bool:
    evaluation = record.latest_evaluation
    if evaluation is None or evaluation.candidate_version != expected_version:
        return False
    target = (
        CandidateStatus.AWAITING_APPROVAL if evaluation.passed else CandidateStatus.REJECTED
    )
    return (
        record.candidate.version == expected_version + 2
        and record.candidate.status is target
    )
