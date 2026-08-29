from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from public_agent.core.types import utc_now
from public_agent.growth.models import CandidateStatus, EvaluationResult, LearningCandidate


class LearningStore(Protocol):
    async def save(self, candidate: LearningCandidate) -> None:
        """Create or replace one versioned candidate record."""

    async def get(self, candidate_id: UUID) -> LearningCandidate:
        """Return a candidate or raise KeyError."""

    async def find_by_fingerprint(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        fingerprint: str,
    ) -> LearningCandidate | None:
        """Return a non-terminal candidate with the same reusable asset fingerprint."""

    async def create_if_fingerprint_absent(self, candidate: LearningCandidate) -> bool:
        """Atomically create a pending candidate unless the scoped fingerprint is active."""

    async def list_for_conflict(
        self,
        candidate: LearningCandidate,
        *,
        limit: int = 100,
    ) -> tuple[LearningCandidate, ...]:
        """Return scoped non-terminal candidates for conservative conflict assessment."""

    async def create_merged_candidate(
        self,
        candidate: LearningCandidate,
        *,
        source_versions: dict[UUID, int],
    ) -> tuple[LearningCandidate, bool]:
        """Create a merged candidate atomically after locking and validating its sources."""

    async def save_evaluation(
        self,
        candidate_id: UUID,
        result: EvaluationResult,
    ) -> None:
        """Persist evaluation evidence for one candidate."""


class InMemoryLearningStore:
    def __init__(self) -> None:
        self._candidates: dict[UUID, LearningCandidate] = {}
        self._evaluations: dict[UUID, list[EvaluationResult]] = {}
        self._lock = asyncio.Lock()

    async def save(self, candidate: LearningCandidate) -> None:
        self._candidates[candidate.id] = candidate

    async def get(self, candidate_id: UUID) -> LearningCandidate:
        try:
            return self._candidates[candidate_id]
        except KeyError as exc:
            raise KeyError(f"Unknown learning candidate: {candidate_id}") from exc

    async def find_by_fingerprint(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        fingerprint: str,
    ) -> LearningCandidate | None:
        terminal = {
            CandidateStatus.REJECTED,
            CandidateStatus.ROLLED_BACK,
            CandidateStatus.EXPIRED,
        }
        for candidate in self._candidates.values():
            if candidate.status in terminal:
                continue
            if (
                candidate.tenant_id == tenant_id
                and candidate.agent_id == agent_id
                and candidate.domain_id == domain_id
                and candidate.fingerprint == fingerprint
            ):
                return candidate
        return None

    async def create_if_fingerprint_absent(self, candidate: LearningCandidate) -> bool:
        async with self._lock:
            terminal = {
                CandidateStatus.REJECTED,
                CandidateStatus.ROLLED_BACK,
                CandidateStatus.EXPIRED,
            }
            duplicate = next(
                (
                    existing
                    for existing in self._candidates.values()
                    if existing.status not in terminal
                    and existing.tenant_id == candidate.tenant_id
                    and existing.agent_id == candidate.agent_id
                    and existing.domain_id == candidate.domain_id
                    and existing.fingerprint == candidate.fingerprint
                ),
                None,
            )
            if duplicate is not None:
                return False
            self._candidates[candidate.id] = candidate
            return True

    async def list_for_conflict(
        self,
        candidate: LearningCandidate,
        *,
        limit: int = 100,
    ) -> tuple[LearningCandidate, ...]:
        terminal = {
            CandidateStatus.REJECTED,
            CandidateStatus.ROLLED_BACK,
            CandidateStatus.EXPIRED,
        }
        rows = [
            existing
            for existing in self._candidates.values()
            if existing.id != candidate.id
            and existing.status not in terminal
            and existing.tenant_id == candidate.tenant_id
            and existing.agent_id == candidate.agent_id
            and existing.domain_id == candidate.domain_id
            and existing.candidate_type is candidate.candidate_type
            and all(
                existing.proposed_change.get(key) == candidate.proposed_change.get(key)
                for key in ("namespace", "memory_type")
            )
        ]
        rows.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        return tuple(rows[:limit])

    async def create_merged_candidate(
        self,
        candidate: LearningCandidate,
        *,
        source_versions: dict[UUID, int],
    ) -> tuple[LearningCandidate, bool]:
        async with self._lock:
            existing = self._candidates.get(candidate.id)
            if existing is not None:
                return existing, False
            terminal = {
                CandidateStatus.REJECTED,
                CandidateStatus.ROLLED_BACK,
                CandidateStatus.EXPIRED,
            }
            for source_id, expected_version in sorted(
                source_versions.items(), key=lambda item: str(item[0])
            ):
                source = self._candidates.get(source_id)
                if source is None:
                    raise KeyError(f"Unknown merge source candidate: {source_id}")
                if source.version != expected_version:
                    raise ValueError(f"Merge source changed before merge: {source_id}")
                if source.status in terminal:
                    raise ValueError(f"Terminal candidate cannot be merged: {source_id}")
            self._candidates[candidate.id] = candidate
            return candidate, True

    async def save_evaluation(
        self,
        candidate_id: UUID,
        result: EvaluationResult,
    ) -> None:
        if candidate_id not in self._candidates:
            raise KeyError(f"Unknown learning candidate: {candidate_id}")
        self._evaluations.setdefault(candidate_id, []).append(result)


class LearningService:
    def __init__(self, store: LearningStore) -> None:
        self._store = store

    async def propose(self, candidate: LearningCandidate) -> LearningCandidate:
        if candidate.status is not CandidateStatus.PENDING:
            raise ValueError("New learning candidates must start in pending state")
        await self._store.save(candidate)
        return candidate

    async def propose_if_absent(self, candidate: LearningCandidate) -> bool:
        if candidate.status is not CandidateStatus.PENDING:
            raise ValueError("New learning candidates must start in pending state")
        return await self._store.create_if_fingerprint_absent(candidate)

    async def begin_evaluation(self, candidate_id: UUID) -> LearningCandidate:
        return await self._transition(
            candidate_id,
            CandidateStatus.PENDING,
            CandidateStatus.EVALUATING,
        )

    async def record_evaluation(
        self,
        candidate_id: UUID,
        result: EvaluationResult,
    ) -> LearningCandidate:
        candidate = await self._store.get(candidate_id)
        if candidate.status is not CandidateStatus.EVALUATING:
            raise ValueError("Candidate must be evaluating before recording a result")
        await self._store.save_evaluation(candidate_id, result)
        target = CandidateStatus.AWAITING_APPROVAL if result.passed else CandidateStatus.REJECTED
        return await self._replace(candidate, target)

    async def approve(self, candidate_id: UUID) -> LearningCandidate:
        return await self._transition(
            candidate_id,
            CandidateStatus.AWAITING_APPROVAL,
            CandidateStatus.APPROVED,
        )

    async def activate(self, candidate_id: UUID) -> LearningCandidate:
        return await self._transition(
            candidate_id,
            CandidateStatus.APPROVED,
            CandidateStatus.ACTIVE,
        )

    async def deprecate(self, candidate_id: UUID) -> LearningCandidate:
        return await self._transition(
            candidate_id,
            CandidateStatus.ACTIVE,
            CandidateStatus.DEPRECATED,
        )

    async def rollback(self, candidate_id: UUID) -> LearningCandidate:
        candidate = await self._store.get(candidate_id)
        if candidate.status not in {CandidateStatus.ACTIVE, CandidateStatus.DEPRECATED}:
            raise ValueError("Only active or deprecated candidates can be rolled back")
        return await self._replace(candidate, CandidateStatus.ROLLED_BACK)

    async def _transition(
        self,
        candidate_id: UUID,
        expected: CandidateStatus,
        target: CandidateStatus,
    ) -> LearningCandidate:
        candidate = await self._store.get(candidate_id)
        if candidate.status is not expected:
            raise ValueError(f"Expected {expected.value}, got {candidate.status.value}")
        return await self._replace(candidate, target)

    async def _replace(
        self,
        candidate: LearningCandidate,
        status: CandidateStatus,
    ) -> LearningCandidate:
        updated = candidate.model_copy(
            update={
                "status": status,
                "version": candidate.version + 1,
                "updated_at": utc_now(),
            }
        )
        await self._store.save(updated)
        return updated
