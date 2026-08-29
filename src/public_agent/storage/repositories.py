from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from public_agent.core.types import utc_now
from public_agent.growth.conflicts import (
    superseding_source_ids,
    superseding_source_statuses,
    superseding_source_versions,
)
from public_agent.growth.governance import (
    CandidateGovernanceCursor,
    CandidateGovernanceDecision,
    CandidateGovernancePage,
    CandidateGovernancePolicy,
    CandidateGovernanceQuery,
    CandidateGovernanceSnapshot,
    GovernanceAction,
    GovernanceApplyResult,
    GovernanceReason,
)
from public_agent.growth.models import CandidateStatus, EvaluationResult, LearningCandidate
from public_agent.growth.pipeline import memory_from_candidate, memory_id_for_candidate
from public_agent.growth.service import LearningStore
from public_agent.memory.base import MemoryQuery, MemoryRecord, MemoryStore, MemoryType
from public_agent.storage.models import (
    AgentModel,
    ApprovalModel,
    CandidateGovernanceActionModel,
    CandidateLineageModel,
    EvaluationModel,
    LearningCandidateModel,
    MemoryModel,
    TenantModel,
)


@dataclass(frozen=True, slots=True)
class ScopeIds:
    tenant_id: UUID
    agent_id: UUID
    domain_id: str


class PostgresMemoryStore(MemoryStore):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, memory: MemoryRecord) -> None:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, memory.tenant_id, memory.agent_id)
            source_run_id = _optional_uuid(memory.metadata.get("source_run_id"))
            candidate_id = _optional_uuid(memory.metadata.get("candidate_id"))
            row = await session.get(MemoryModel, memory.id)
            if row is None:
                session.add(
                    MemoryModel(
                        id=memory.id,
                        tenant_id=scope.tenant_id,
                        agent_id=scope.agent_id,
                        domain_id=str(memory.metadata.get("domain_id", scope.domain_id)),
                        namespace=memory.namespace,
                        memory_type=memory.memory_type.value,
                        content=memory.content,
                        status="active",
                        confidence=memory.confidence,
                        importance=memory.importance,
                        source_run_id=source_run_id,
                        candidate_id=candidate_id,
                        metadata_json=memory.metadata,
                        created_at=memory.created_at,
                        updated_at=memory.created_at,
                        expires_at=memory.expires_at,
                    )
                )
                return
            if row.tenant_id != scope.tenant_id or row.agent_id != scope.agent_id:
                raise ValueError("Memory id already belongs to another tenant or agent")
            row.domain_id = str(memory.metadata.get("domain_id", scope.domain_id))
            row.namespace = memory.namespace
            row.memory_type = memory.memory_type.value
            row.content = memory.content
            row.status = "active"
            row.confidence = memory.confidence
            row.importance = memory.importance
            row.source_run_id = source_run_id
            row.candidate_id = candidate_id
            row.metadata_json = memory.metadata
            row.expires_at = memory.expires_at

    async def search(self, query: MemoryQuery) -> tuple[MemoryRecord, ...]:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, query.tenant_id, query.agent_id)
            statement = select(MemoryModel).where(
                MemoryModel.tenant_id == scope.tenant_id,
                MemoryModel.agent_id == scope.agent_id,
                MemoryModel.namespace == query.namespace,
                MemoryModel.status == "active",
                (MemoryModel.expires_at.is_(None) | (MemoryModel.expires_at > datetime.now(UTC))),
            )
            if query.memory_types:
                statement = statement.where(
                    MemoryModel.memory_type.in_([item.value for item in query.memory_types])
                )
            statement = statement.order_by(
                MemoryModel.importance.desc(),
                MemoryModel.confidence.desc(),
                MemoryModel.created_at.desc(),
            ).limit(min(max(query.limit * 20, 100), 500))
            rows = tuple((await session.scalars(statement)).all())
            query_terms = _terms(query.text)
            ranked = sorted(
                rows,
                key=lambda row: (
                    _lexical_score(query_terms, row.content),
                    row.importance,
                    row.confidence,
                    row.created_at,
                ),
                reverse=True,
            )
            selected = tuple(ranked[: query.limit])
            if selected:
                recalled_at = datetime.now(UTC)
                recalled_ids = set(
                    (
                        await session.scalars(
                            update(MemoryModel)
                            .where(
                                MemoryModel.id.in_([row.id for row in selected]),
                                MemoryModel.status == "active",
                                (
                                    MemoryModel.expires_at.is_(None)
                                    | (MemoryModel.expires_at > recalled_at)
                                ),
                            )
                            .values(
                                recall_count=MemoryModel.recall_count + 1,
                                last_recalled_at=recalled_at,
                            )
                            .returning(MemoryModel.id)
                        )
                    ).all()
                )
                selected = tuple(row for row in selected if row.id in recalled_ids)
            return tuple(
                _memory_from_row(row, tenant_id=query.tenant_id, agent_id=query.agent_id)
                for row in selected
            )

    async def deactivate(self, memory_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(MemoryModel).where(MemoryModel.id == memory_id).with_for_update()
            )
            if row is None:
                raise KeyError(f"Unknown memory: {memory_id}")
            row.status = "superseded"

    async def activate(self, memory_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(MemoryModel).where(MemoryModel.id == memory_id).with_for_update()
            )
            if row is None:
                raise KeyError(f"Unknown memory: {memory_id}")
            row.status = "active"


class PostgresLearningStore(LearningStore):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, candidate: LearningCandidate) -> None:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, candidate.tenant_id, candidate.agent_id)
            row = await session.get(LearningCandidateModel, candidate.id)
            if row is None:
                session.add(
                    LearningCandidateModel(
                        id=candidate.id,
                        tenant_id=scope.tenant_id,
                        agent_id=scope.agent_id,
                        domain_id=candidate.domain_id,
                        candidate_type=candidate.candidate_type.value,
                        risk=candidate.risk.value,
                        title=candidate.title,
                        fingerprint=candidate.fingerprint,
                        proposed_change=candidate.proposed_change,
                        evidence_run_ids=[str(item) for item in candidate.evidence_run_ids],
                        status=candidate.status.value,
                        version=candidate.version,
                        created_at=candidate.created_at,
                        updated_at=candidate.updated_at,
                        expires_at=candidate.expires_at,
                        protected_until=candidate.protected_until,
                    )
                )
                return
            if row.tenant_id != scope.tenant_id or row.agent_id != scope.agent_id:
                raise ValueError("Candidate id already belongs to another tenant or agent")
            if candidate.version != row.version + 1:
                raise ValueError(
                    "Candidate version conflict: "
                    f"stored={row.version}, incoming={candidate.version}"
                )
            row.domain_id = candidate.domain_id
            row.candidate_type = candidate.candidate_type.value
            row.risk = candidate.risk.value
            row.title = candidate.title
            row.fingerprint = candidate.fingerprint
            row.proposed_change = candidate.proposed_change
            row.evidence_run_ids = [str(item) for item in candidate.evidence_run_ids]
            row.status = candidate.status.value
            row.version = candidate.version
            row.updated_at = candidate.updated_at
            row.expires_at = candidate.expires_at
            row.protected_until = candidate.protected_until

    async def get(self, candidate_id: UUID) -> LearningCandidate:
        async with self._sessions() as session:
            row = await session.get(LearningCandidateModel, candidate_id)
            if row is None:
                raise KeyError(f"Unknown learning candidate: {candidate_id}")
            tenant = await session.get(TenantModel, row.tenant_id)
            agent = await session.get(AgentModel, row.agent_id)
            if tenant is None or agent is None:
                raise RuntimeError("Candidate scope is missing")
            return _candidate_from_row(row, tenant_id=tenant.slug, agent_id=agent.agent_key)

    async def find_by_fingerprint(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        fingerprint: str,
    ) -> LearningCandidate | None:
        async with self._sessions() as session:
            scope = await _resolve_scope(session, tenant_id, agent_id)
            row = await session.scalar(
                select(LearningCandidateModel)
                .where(
                    LearningCandidateModel.tenant_id == scope.tenant_id,
                    LearningCandidateModel.agent_id == scope.agent_id,
                    LearningCandidateModel.domain_id == domain_id,
                    LearningCandidateModel.fingerprint == fingerprint,
                    LearningCandidateModel.status.not_in(_TERMINAL_CANDIDATE_STATUSES),
                )
                .order_by(LearningCandidateModel.updated_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            return _candidate_from_row(row, tenant_id=tenant_id, agent_id=agent_id)

    async def create_if_fingerprint_absent(self, candidate: LearningCandidate) -> bool:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, candidate.tenant_id, candidate.agent_id)
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        _fingerprint_lock_id(candidate, tenant_uuid=scope.tenant_id)
                    )
                )
            )
            duplicate = await session.scalar(
                select(LearningCandidateModel.id)
                .where(
                    LearningCandidateModel.tenant_id == scope.tenant_id,
                    LearningCandidateModel.agent_id == scope.agent_id,
                    LearningCandidateModel.domain_id == candidate.domain_id,
                    LearningCandidateModel.fingerprint == candidate.fingerprint,
                    LearningCandidateModel.status.not_in(_TERMINAL_CANDIDATE_STATUSES),
                )
                .limit(1)
            )
            if duplicate is not None:
                return False
            if await session.get(LearningCandidateModel, candidate.id) is not None:
                raise ValueError("Candidate id already exists")
            session.add(_candidate_row(candidate, scope=scope))
            return True

    async def list_for_conflict(
        self,
        candidate: LearningCandidate,
        *,
        limit: int = 100,
    ) -> tuple[LearningCandidate, ...]:
        async with self._sessions() as session:
            scope = await _resolve_scope(session, candidate.tenant_id, candidate.agent_id)
            statement = select(LearningCandidateModel).where(
                LearningCandidateModel.tenant_id == scope.tenant_id,
                LearningCandidateModel.agent_id == scope.agent_id,
                LearningCandidateModel.domain_id == candidate.domain_id,
                LearningCandidateModel.candidate_type == candidate.candidate_type.value,
                LearningCandidateModel.id != candidate.id,
                LearningCandidateModel.status.not_in(_TERMINAL_CANDIDATE_STATUSES),
            )
            for key in ("namespace", "memory_type"):
                value = candidate.proposed_change.get(key)
                if value is not None:
                    statement = statement.where(
                        LearningCandidateModel.proposed_change[key].astext == str(value)
                    )
            rows = tuple(
                (
                    await session.scalars(
                        statement.order_by(
                            LearningCandidateModel.updated_at.desc(),
                            LearningCandidateModel.id,
                        ).limit(min(max(limit, 1), 500))
                    )
                ).all()
            )
            return tuple(
                _candidate_from_row(
                    row,
                    tenant_id=candidate.tenant_id,
                    agent_id=candidate.agent_id,
                )
                for row in rows
            )

    async def create_merged_candidate(
        self,
        candidate: LearningCandidate,
        *,
        source_versions: dict[UUID, int],
    ) -> tuple[LearningCandidate, bool]:
        if not source_versions:
            raise ValueError("Merged candidate requires source versions")
        source_ids = sorted(source_versions, key=str)
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, candidate.tenant_id, candidate.agent_id)
            source_rows = tuple(
                (
                    await session.scalars(
                        select(LearningCandidateModel)
                        .where(LearningCandidateModel.id.in_(source_ids))
                        .order_by(LearningCandidateModel.id)
                        .with_for_update()
                    )
                ).all()
            )
            if {row.id for row in source_rows} != set(source_ids):
                raise KeyError("One or more merge source candidates are missing")
            existing = await session.get(LearningCandidateModel, candidate.id)
            if existing is not None:
                return (
                    _candidate_from_row(
                        existing,
                        tenant_id=candidate.tenant_id,
                        agent_id=candidate.agent_id,
                    ),
                    False,
                )
            for row in source_rows:
                if row.tenant_id != scope.tenant_id or row.agent_id != scope.agent_id:
                    raise ValueError("Merge sources must belong to the same tenant and agent")
                if row.domain_id != candidate.domain_id:
                    raise ValueError("Merge sources must belong to the same domain")
                if row.candidate_type != candidate.candidate_type.value:
                    raise ValueError("Merge sources must have the same candidate type")
                if row.status in _TERMINAL_CANDIDATE_STATUSES:
                    raise ValueError(f"Terminal candidate cannot be merged: {row.id}")
                if row.version != source_versions[row.id]:
                    raise ValueError(f"Merge source changed before merge: {row.id}")
            session.add(_candidate_row(candidate, scope=scope))
            await session.flush()
            session.add_all(
                [
                    CandidateLineageModel(
                        child_candidate_id=candidate.id,
                        source_candidate_id=row.id,
                        relation_type="merge",
                        source_version=row.version,
                        source_status=row.status,
                        created_at=candidate.created_at,
                    )
                    for row in source_rows
                ]
            )
            return candidate, True

    async def save_evaluation(
        self,
        candidate_id: UUID,
        result: EvaluationResult,
    ) -> None:
        async with self._sessions() as session, session.begin():
            candidate = await session.get(LearningCandidateModel, candidate_id)
            if candidate is None:
                raise KeyError(f"Unknown learning candidate: {candidate_id}")
            session.add(
                EvaluationModel(
                    tenant_id=candidate.tenant_id,
                    candidate_id=candidate_id,
                    passed=result.passed,
                    score=result.score,
                    summary=result.summary,
                    metrics=result.metrics,
                    created_at=result.created_at,
                    updated_at=result.created_at,
                )
            )


class PostgresCandidateGovernanceRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def scan(self, query: CandidateGovernanceQuery) -> CandidateGovernancePage:
        async with self._sessions() as session:
            scope = await _resolve_scope(session, query.tenant_id, query.agent_id)
            statement = select(LearningCandidateModel).where(
                LearningCandidateModel.tenant_id == scope.tenant_id,
                LearningCandidateModel.agent_id == scope.agent_id,
                LearningCandidateModel.status.in_(_GOVERNANCE_SCAN_STATUSES),
            )
            if query.domain_id is not None:
                statement = statement.where(LearningCandidateModel.domain_id == query.domain_id)
            if query.after is not None:
                statement = statement.where(
                    or_(
                        LearningCandidateModel.created_at > query.after.created_at,
                        and_(
                            LearningCandidateModel.created_at == query.after.created_at,
                            LearningCandidateModel.id > query.after.candidate_id,
                        ),
                    )
                )
            rows = tuple(
                (
                    await session.scalars(
                        statement.order_by(
                            LearningCandidateModel.created_at,
                            LearningCandidateModel.id,
                        ).limit(query.limit + 1)
                    )
                ).all()
            )
            page_rows = rows[: query.limit]
            if not page_rows:
                return CandidateGovernancePage(items=())

            candidate_ids = [row.id for row in page_rows]
            memory_rows = tuple(
                (
                    await session.scalars(
                        select(MemoryModel).where(
                            MemoryModel.tenant_id == scope.tenant_id,
                            MemoryModel.agent_id == scope.agent_id,
                            MemoryModel.candidate_id.in_(candidate_ids),
                        )
                    )
                ).all()
            )
            memories = {row.candidate_id: row for row in memory_rows}
            evaluation_rows = tuple(
                (
                    await session.scalars(
                        select(EvaluationModel)
                        .where(EvaluationModel.candidate_id.in_(candidate_ids))
                        .distinct(EvaluationModel.candidate_id)
                        .order_by(
                            EvaluationModel.candidate_id,
                            EvaluationModel.created_at.desc(),
                            EvaluationModel.id.desc(),
                        )
                    )
                ).all()
            )
            latest_scores: dict[UUID, float] = {}
            for evaluation in evaluation_rows:
                latest_scores.setdefault(evaluation.candidate_id, evaluation.score)

            child = aliased(LearningCandidateModel)
            live_source_ids = set(
                (
                    await session.scalars(
                        select(CandidateLineageModel.source_candidate_id)
                        .join(
                            child,
                            child.id == CandidateLineageModel.child_candidate_id,
                        )
                        .where(
                            CandidateLineageModel.source_candidate_id.in_(candidate_ids),
                            child.status.not_in(_TERMINAL_CANDIDATE_STATUSES),
                        )
                    )
                ).all()
            )
            snapshots = tuple(
                CandidateGovernanceSnapshot(
                    candidate=_candidate_from_row(
                        row,
                        tenant_id=query.tenant_id,
                        agent_id=query.agent_id,
                    ),
                    latest_evaluation_score=latest_scores.get(row.id),
                    memory_status=(memories[row.id].status if row.id in memories else None),
                    memory_confidence=(memories[row.id].confidence if row.id in memories else None),
                    memory_importance=(memories[row.id].importance if row.id in memories else None),
                    recall_count=(memories[row.id].recall_count if row.id in memories else 0),
                    last_recalled_at=(
                        memories[row.id].last_recalled_at if row.id in memories else None
                    ),
                    has_live_descendant=row.id in live_source_ids,
                )
                for row in page_rows
            )
            next_cursor = None
            if len(rows) > query.limit:
                last = page_rows[-1]
                next_cursor = CandidateGovernanceCursor(
                    created_at=last.created_at,
                    candidate_id=last.id,
                )
            return CandidateGovernancePage(items=snapshots, next_cursor=next_cursor)

    async def apply(
        self,
        decision: CandidateGovernanceDecision,
        *,
        policy: CandidateGovernancePolicy,
    ) -> GovernanceApplyResult:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, decision.tenant_id, decision.agent_id)
            row = await session.scalar(
                select(LearningCandidateModel)
                .where(
                    LearningCandidateModel.id == decision.candidate_id,
                    LearningCandidateModel.tenant_id == scope.tenant_id,
                    LearningCandidateModel.agent_id == scope.agent_id,
                    LearningCandidateModel.domain_id == decision.domain_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError(
                    "Unknown learning candidate in the requested governance scope: "
                    f"{decision.candidate_id}"
                )
            current = _candidate_from_row(
                row,
                tenant_id=decision.tenant_id,
                agent_id=decision.agent_id,
            )
            existing_action = await session.scalar(
                select(CandidateGovernanceActionModel.id).where(
                    CandidateGovernanceActionModel.idempotency_key == decision.idempotency_key
                )
            )
            if existing_action is not None:
                return GovernanceApplyResult(candidate=current, applied=False)
            if (
                row.version != decision.expected_version
                or row.status != decision.expected_status.value
                or row.status not in _GOVERNANCE_MUTABLE_STATUSES
                or row.risk == "high"
                or _row_explicitly_protected(row, decision.decided_at)
                or await _has_live_descendant(session, row.id)
            ):
                return GovernanceApplyResult(candidate=current, applied=False)

            memory = await session.scalar(
                select(MemoryModel).where(MemoryModel.candidate_id == row.id).with_for_update()
            )
            if memory is not None:
                if memory.recall_count != decision.expected_recall_count:
                    return GovernanceApplyResult(candidate=current, applied=False)
                if memory.importance >= policy.protected_importance or (
                    memory.confidence >= policy.protected_confidence
                ):
                    return GovernanceApplyResult(candidate=current, applied=False)

            previous_status = row.status
            previous_memory_status = memory.status if memory is not None else None
            row.status = CandidateStatus.EXPIRED.value
            row.version += 1
            row.updated_at = decision.decided_at
            if memory is not None:
                memory.status = "expired"
                memory.updated_at = decision.decided_at
            session.add(
                CandidateGovernanceActionModel(
                    tenant_id=row.tenant_id,
                    agent_id=row.agent_id,
                    candidate_id=row.id,
                    action=decision.action.value,
                    reason_code=decision.reason.value,
                    policy_version=decision.policy_version,
                    idempotency_key=decision.idempotency_key,
                    value_score=decision.value_score,
                    previous_status=previous_status,
                    target_status=CandidateStatus.EXPIRED.value,
                    details={
                        "candidate_version": decision.expected_version,
                        "expected_recall_count": decision.expected_recall_count,
                        "previous_memory_status": previous_memory_status,
                        "target_memory_status": "expired" if memory is not None else None,
                    },
                    created_at=decision.decided_at,
                )
            )
            updated = current.model_copy(
                update={
                    "status": CandidateStatus.EXPIRED,
                    "version": current.version + 1,
                    "updated_at": decision.decided_at,
                }
            )
            return GovernanceApplyResult(candidate=updated, applied=True)

    async def create_compression(
        self,
        candidate: LearningCandidate,
        *,
        source_versions: dict[UUID, int],
        policy_version: str,
        value_score: float,
    ) -> tuple[LearningCandidate, bool]:
        if not source_versions:
            raise ValueError("Compression candidate requires source versions")
        source_ids = sorted(source_versions, key=str)
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, candidate.tenant_id, candidate.agent_id)
            source_rows = tuple(
                (
                    await session.scalars(
                        select(LearningCandidateModel)
                        .where(LearningCandidateModel.id.in_(source_ids))
                        .order_by(LearningCandidateModel.id)
                        .with_for_update()
                    )
                ).all()
            )
            if {row.id for row in source_rows} != set(source_ids):
                raise KeyError("One or more compression source candidates are missing")
            existing = await session.get(LearningCandidateModel, candidate.id)
            if existing is not None:
                return (
                    _candidate_from_row(
                        existing,
                        tenant_id=candidate.tenant_id,
                        agent_id=candidate.agent_id,
                    ),
                    False,
                )
            now = utc_now()
            for row in source_rows:
                if (
                    row.tenant_id != scope.tenant_id
                    or row.agent_id != scope.agent_id
                    or row.domain_id != candidate.domain_id
                    or row.candidate_type != candidate.candidate_type.value
                ):
                    raise ValueError("Compression sources must share the candidate scope")
                if row.status != CandidateStatus.ACTIVE.value:
                    raise ValueError("Compression sources must remain active")
                if row.version != source_versions[row.id]:
                    raise ValueError(f"Compression source changed: {row.id}")
                if row.risk == "high" or _row_explicitly_protected(row, now):
                    raise ValueError("Protected or high-risk candidates cannot be compressed")
                if await _has_live_descendant(session, row.id):
                    return candidate, False

            session.add(_candidate_row(candidate, scope=scope))
            await session.flush()
            compression = candidate.proposed_change.get("compression")
            compressor_version = (
                str(compression.get("compressor_version"))
                if isinstance(compression, dict)
                else "unknown"
            )
            for row in source_rows:
                session.add(
                    CandidateLineageModel(
                        child_candidate_id=candidate.id,
                        source_candidate_id=row.id,
                        relation_type="compression",
                        source_version=row.version,
                        source_status=row.status,
                        created_at=candidate.created_at,
                    )
                )
                session.add(
                    CandidateGovernanceActionModel(
                        tenant_id=row.tenant_id,
                        agent_id=row.agent_id,
                        candidate_id=row.id,
                        action=GovernanceAction.COMPRESS.value,
                        reason_code=GovernanceReason.COMPATIBLE_COMPRESSION.value,
                        policy_version=policy_version,
                        idempotency_key=(
                            f"{row.id}:{row.version}:compress:{policy_version}:{candidate.id}"
                        ),
                        value_score=value_score,
                        previous_status=row.status,
                        target_status=row.status,
                        replacement_candidate_id=candidate.id,
                        details={
                            "source_version": row.version,
                            "compressor_version": compressor_version,
                        },
                        created_at=candidate.created_at,
                    )
                )
            return candidate, True


class PostgresKnowledgeAssetPublisher:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def publish(
        self,
        candidate: LearningCandidate,
        *,
        decided_by: str,
        decision_note: str | None = None,
    ) -> tuple[LearningCandidate, MemoryRecord]:
        if candidate.status is not CandidateStatus.APPROVED:
            raise ValueError("Candidate must be approved before publication")
        return await self._publish(
            candidate,
            decided_by=decided_by,
            decision_note=decision_note,
            stored_status=CandidateStatus.APPROVED,
            stored_version=candidate.version,
            stored_version_increment=1,
        )

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
    ) -> tuple[LearningCandidate, MemoryRecord]:
        async with self._sessions() as session:
            scope = await _resolve_scope(session, tenant_id, agent_id)
            row = await session.scalar(
                select(LearningCandidateModel).where(
                    LearningCandidateModel.id == candidate_id,
                    LearningCandidateModel.tenant_id == scope.tenant_id,
                    LearningCandidateModel.agent_id == scope.agent_id,
                    LearningCandidateModel.domain_id == domain_id,
                )
            )
            if row is None:
                raise KeyError(f"Unknown learning candidate: {candidate_id}")
            candidate = _candidate_from_row(row, tenant_id=tenant_id, agent_id=agent_id)
        synthetic_approved = candidate.model_copy(
            update={
                "status": CandidateStatus.APPROVED,
                "version": expected_version + 1,
            }
        )
        return await self._publish(
            synthetic_approved,
            decided_by=decided_by,
            decision_note=decision_note,
            stored_status=CandidateStatus.AWAITING_APPROVAL,
            stored_version=expected_version,
            stored_version_increment=2,
            replay_expected_version=expected_version,
        )

    async def _publish(
        self,
        candidate: LearningCandidate,
        *,
        decided_by: str,
        decision_note: str | None,
        stored_status: CandidateStatus,
        stored_version: int,
        stored_version_increment: int,
        replay_expected_version: int | None = None,
    ) -> tuple[LearningCandidate, MemoryRecord]:
        memory = memory_from_candidate(
            candidate,
            decided_by=decided_by,
            decision_note=decision_note,
        )
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, candidate.tenant_id, candidate.agent_id)
            row = await session.scalar(
                select(LearningCandidateModel)
                .where(
                    LearningCandidateModel.id == candidate.id,
                    LearningCandidateModel.tenant_id == scope.tenant_id,
                    LearningCandidateModel.agent_id == scope.agent_id,
                    LearningCandidateModel.domain_id == candidate.domain_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError(f"Unknown learning candidate: {candidate.id}")
            if replay_expected_version is not None and row.status == CandidateStatus.ACTIVE.value:
                existing_memory = await session.scalar(
                    select(MemoryModel).where(MemoryModel.id == memory.id)
                )
                approval = await session.scalar(
                    select(ApprovalModel)
                    .where(ApprovalModel.candidate_id == row.id)
                    .order_by(ApprovalModel.created_at.desc(), ApprovalModel.id.desc())
                    .limit(1)
                )
                payload = approval.requested_payload if approval is not None else {}
                if (
                    row.version == replay_expected_version + 2
                    and existing_memory is not None
                    and approval is not None
                    and approval.status == "approved"
                    and payload.get("decision") == "approved"
                    and payload.get("expected_candidate_version") == replay_expected_version
                    and approval.decided_by == decided_by
                    and approval.decision_note == decision_note
                ):
                    return (
                        _candidate_from_row(
                            row,
                            tenant_id=candidate.tenant_id,
                            agent_id=candidate.agent_id,
                        ),
                        _memory_from_row(
                            existing_memory,
                            tenant_id=candidate.tenant_id,
                            agent_id=candidate.agent_id,
                        ),
                    )
                raise ValueError("Candidate approval request conflicts with its prior decision")
            if row.status != stored_status.value or row.version != stored_version:
                raise ValueError("Candidate changed before publication")
            if stored_status is CandidateStatus.AWAITING_APPROVAL:
                latest_evaluation = await session.scalar(
                    select(EvaluationModel)
                    .where(EvaluationModel.candidate_id == row.id)
                    .order_by(EvaluationModel.created_at.desc(), EvaluationModel.id.desc())
                    .limit(1)
                )
                if latest_evaluation is None or not latest_evaluation.passed:
                    raise ValueError("Candidate requires a passing evaluation before approval")
            if await session.get(MemoryModel, memory.id) is not None:
                raise ValueError("Candidate memory has already been published")
            source_ids = sorted(superseding_source_ids(candidate), key=str)
            if candidate.id in source_ids:
                raise ValueError("Merged candidate cannot reference itself as a source")
            source_rows: tuple[LearningCandidateModel, ...] = ()
            if source_ids:
                source_rows = tuple(
                    (
                        await session.scalars(
                            select(LearningCandidateModel)
                            .where(LearningCandidateModel.id.in_(source_ids))
                            .order_by(LearningCandidateModel.id)
                            .with_for_update()
                        )
                    ).all()
                )
                if {source.id for source in source_rows} != set(source_ids):
                    raise KeyError("One or more merge source candidates are missing")
                expected_versions = superseding_source_versions(candidate)
                expected_statuses = superseding_source_statuses(candidate)
                if set(expected_versions) != set(source_ids) or set(expected_statuses) != set(
                    source_ids
                ):
                    raise ValueError("Merged candidate source metadata is incomplete")
                for source in source_rows:
                    if source.tenant_id != row.tenant_id or source.agent_id != row.agent_id:
                        raise ValueError("Merge source scope changed before publication")
                    if source.status in _TERMINAL_CANDIDATE_STATUSES:
                        raise ValueError("Merged candidate has a terminal source")
                    if source.version != expected_versions[source.id]:
                        raise ValueError("Merge source changed before publication")
                    if source.status != expected_statuses[source.id].value:
                        raise ValueError("Merge source status changed before publication")
                source_memory_ids = [memory_id_for_candidate(source.id) for source in source_rows]
                source_memories = {
                    source_memory.id: source_memory
                    for source_memory in (
                        await session.scalars(
                            select(MemoryModel)
                            .where(MemoryModel.id.in_(source_memory_ids))
                            .order_by(MemoryModel.id)
                            .with_for_update()
                        )
                    ).all()
                }
                for source in source_rows:
                    source_memory = source_memories.get(memory_id_for_candidate(source.id))
                    if source.status == CandidateStatus.ACTIVE.value and source_memory is None:
                        raise RuntimeError("Active merge source is missing its published memory")
                    if source_memory is not None:
                        source_memory.status = "superseded"
                    if source.status != CandidateStatus.DEPRECATED.value:
                        source.status = CandidateStatus.DEPRECATED.value
                        source.version += 1
                        source.updated_at = utc_now()
            session.add(
                MemoryModel(
                    id=memory.id,
                    tenant_id=row.tenant_id,
                    agent_id=row.agent_id,
                    domain_id=candidate.domain_id,
                    namespace=memory.namespace,
                    memory_type=memory.memory_type.value,
                    content=memory.content,
                    status="active",
                    confidence=memory.confidence,
                    importance=memory.importance,
                    source_run_id=_optional_uuid(memory.metadata.get("source_run_id")),
                    candidate_id=candidate.id,
                    metadata_json=memory.metadata,
                    created_at=memory.created_at,
                    updated_at=memory.created_at,
                    expires_at=memory.expires_at,
                )
            )
            session.add(
                ApprovalModel(
                    tenant_id=row.tenant_id,
                    candidate_id=row.id,
                    status="approved",
                    reason="Knowledge candidate approved for publication",
                    requested_payload={
                        "candidate_id": str(row.id),
                        "fingerprint": candidate.fingerprint,
                        "source_candidate_ids": [str(source_id) for source_id in source_ids],
                        "expected_candidate_version": stored_version,
                        "decision": "approved",
                    },
                    decided_by=decided_by,
                    decision_note=decision_note,
                )
            )
            row.status = CandidateStatus.ACTIVE.value
            row.version += stored_version_increment
            published_at = utc_now()
            row.updated_at = published_at

        active = candidate.model_copy(
            update={
                "status": CandidateStatus.ACTIVE,
                "version": stored_version + stored_version_increment,
                "updated_at": published_at,
            }
        )
        return active, memory

    async def rollback(
        self,
        candidate: LearningCandidate,
        *,
        memory_id: UUID,
    ) -> LearningCandidate:
        return await self._rollback(candidate, memory_id=memory_id)

    async def rollback_scoped(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
    ) -> LearningCandidate:
        async with self._sessions() as session:
            scope = await _resolve_scope(session, tenant_id, agent_id)
            row = await session.scalar(
                select(LearningCandidateModel).where(
                    LearningCandidateModel.id == candidate_id,
                    LearningCandidateModel.tenant_id == scope.tenant_id,
                    LearningCandidateModel.agent_id == scope.agent_id,
                    LearningCandidateModel.domain_id == domain_id,
                )
            )
            if row is None:
                raise KeyError(f"Unknown learning candidate: {candidate_id}")
            candidate = _candidate_from_row(row, tenant_id=tenant_id, agent_id=agent_id)
        return await self._rollback(
            candidate,
            memory_id=memory_id_for_candidate(candidate_id),
            stored_version=expected_version,
            replay_expected_version=expected_version,
        )

    async def _rollback(
        self,
        candidate: LearningCandidate,
        *,
        memory_id: UUID,
        stored_version: int | None = None,
        replay_expected_version: int | None = None,
    ) -> LearningCandidate:
        expected_version = candidate.version if stored_version is None else stored_version
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, candidate.tenant_id, candidate.agent_id)
            row = await session.scalar(
                select(LearningCandidateModel)
                .where(
                    LearningCandidateModel.id == candidate.id,
                    LearningCandidateModel.tenant_id == scope.tenant_id,
                    LearningCandidateModel.agent_id == scope.agent_id,
                    LearningCandidateModel.domain_id == candidate.domain_id,
                )
                .with_for_update()
            )
            memory = await session.scalar(
                select(MemoryModel).where(MemoryModel.id == memory_id).with_for_update()
            )
            if row is None:
                raise KeyError(f"Unknown learning candidate: {candidate.id}")
            if memory is None:
                raise KeyError(f"Unknown memory: {memory_id}")
            if (
                replay_expected_version is not None
                and row.status == CandidateStatus.ROLLED_BACK.value
            ):
                if (
                    row.version == replay_expected_version + 1
                    and memory.status == "superseded"
                ):
                    return _candidate_from_row(
                        row,
                        tenant_id=candidate.tenant_id,
                        agent_id=candidate.agent_id,
                    )
                raise ValueError("Candidate rollback request conflicts with its prior state")
            if row.status not in {
                CandidateStatus.ACTIVE.value,
                CandidateStatus.DEPRECATED.value,
            }:
                raise ValueError("Only active or deprecated candidates can be rolled back")
            if row.version != expected_version:
                raise ValueError("Candidate changed before rollback")
            source_ids = sorted(superseding_source_ids(candidate), key=str)
            if candidate.id in source_ids:
                raise ValueError("Merged candidate cannot reference itself as a source")
            if source_ids:
                source_rows = tuple(
                    (
                        await session.scalars(
                            select(LearningCandidateModel)
                            .where(LearningCandidateModel.id.in_(source_ids))
                            .order_by(LearningCandidateModel.id)
                            .with_for_update()
                        )
                    ).all()
                )
                if {source.id for source in source_rows} != set(source_ids):
                    raise KeyError("One or more merge source candidates are missing")
                source_versions = superseding_source_versions(candidate)
                source_statuses = superseding_source_statuses(candidate)
                if set(source_versions) != set(source_ids) or set(source_statuses) != set(
                    source_ids
                ):
                    raise ValueError("Merged candidate source metadata is incomplete")
                source_memory_ids = [memory_id_for_candidate(source.id) for source in source_rows]
                source_memories = {
                    source_memory.id: source_memory
                    for source_memory in (
                        await session.scalars(
                            select(MemoryModel)
                            .where(MemoryModel.id.in_(source_memory_ids))
                            .order_by(MemoryModel.id)
                            .with_for_update()
                        )
                    ).all()
                }
                for source in source_rows:
                    original_status = source_statuses[source.id]
                    expected_version = source_versions[source.id]
                    if original_status is not CandidateStatus.DEPRECATED:
                        expected_version += 1
                    if (
                        source.status != CandidateStatus.DEPRECATED.value
                        or source.version != expected_version
                    ):
                        raise ValueError("Merge source changed before rollback")
                    source_memory = source_memories.get(memory_id_for_candidate(source.id))
                    if original_status in {
                        CandidateStatus.ACTIVE,
                        CandidateStatus.DEPRECATED,
                    }:
                        if source_memory is None:
                            raise RuntimeError("Published merge source memory is missing")
                        source_memory.status = "active"
                    source.status = original_status.value
                    source.version += 1
                    source.updated_at = utc_now()
            memory.status = "superseded"
            row.status = CandidateStatus.ROLLED_BACK.value
            row.version += 1
            rolled_back_at = utc_now()
            row.updated_at = rolled_back_at

        return candidate.model_copy(
            update={
                "status": CandidateStatus.ROLLED_BACK,
                "version": expected_version + 1,
                "updated_at": rolled_back_at,
            }
        )


async def _resolve_scope(session: AsyncSession, tenant_slug: str, agent_key: str) -> ScopeIds:
    tenant = await session.scalar(select(TenantModel).where(TenantModel.slug == tenant_slug))
    if tenant is None:
        raise KeyError(f"Unknown tenant: {tenant_slug}")
    agent = await session.scalar(
        select(AgentModel).where(
            AgentModel.tenant_id == tenant.id,
            AgentModel.agent_key == agent_key,
        )
    )
    if agent is None:
        raise KeyError(f"Unknown agent for tenant {tenant_slug}: {agent_key}")
    return ScopeIds(tenant_id=tenant.id, agent_id=agent.id, domain_id=agent.domain_id)


def _memory_from_row(row: MemoryModel, *, tenant_id: str, agent_id: str) -> MemoryRecord:
    return MemoryRecord(
        id=row.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        namespace=row.namespace,
        memory_type=MemoryType(row.memory_type),
        content=row.content,
        metadata=row.metadata_json,
        confidence=row.confidence,
        importance=row.importance,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


def _candidate_from_row(
    row: LearningCandidateModel,
    *,
    tenant_id: str,
    agent_id: str,
) -> LearningCandidate:
    return LearningCandidate(
        id=row.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        domain_id=row.domain_id,
        candidate_type=row.candidate_type,
        risk=row.risk,
        title=row.title,
        fingerprint=row.fingerprint,
        proposed_change=row.proposed_change,
        evidence_run_ids=tuple(UUID(item) for item in row.evidence_run_ids),
        status=row.status,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        protected_until=row.protected_until,
    )


def _optional_uuid(value: object) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


_TERMINAL_CANDIDATE_STATUSES = (
    CandidateStatus.REJECTED.value,
    CandidateStatus.ROLLED_BACK.value,
    CandidateStatus.EXPIRED.value,
)

_GOVERNANCE_SCAN_STATUSES = (
    CandidateStatus.PENDING.value,
    CandidateStatus.EVALUATING.value,
    CandidateStatus.AWAITING_APPROVAL.value,
    CandidateStatus.APPROVED.value,
    CandidateStatus.ACTIVE.value,
    CandidateStatus.DEPRECATED.value,
)

_GOVERNANCE_MUTABLE_STATUSES = (
    CandidateStatus.PENDING.value,
    CandidateStatus.ACTIVE.value,
    CandidateStatus.DEPRECATED.value,
)


def _candidate_row(
    candidate: LearningCandidate,
    *,
    scope: ScopeIds,
) -> LearningCandidateModel:
    return LearningCandidateModel(
        id=candidate.id,
        tenant_id=scope.tenant_id,
        agent_id=scope.agent_id,
        domain_id=candidate.domain_id,
        candidate_type=candidate.candidate_type.value,
        risk=candidate.risk.value,
        title=candidate.title,
        fingerprint=candidate.fingerprint,
        proposed_change=candidate.proposed_change,
        evidence_run_ids=[str(item) for item in candidate.evidence_run_ids],
        status=candidate.status.value,
        version=candidate.version,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        expires_at=candidate.expires_at,
        protected_until=candidate.protected_until,
    )


def _fingerprint_lock_id(candidate: LearningCandidate, *, tenant_uuid: UUID) -> int:
    scoped = "|".join(
        (
            str(tenant_uuid),
            candidate.agent_id,
            candidate.domain_id,
            candidate.fingerprint,
        )
    )
    digest = hashlib.sha256(scoped.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))


def _lexical_score(query_terms: set[str], content: str) -> float:
    content_terms = _terms(content)
    return len(query_terms & content_terms) / max(len(query_terms), 1)


async def _has_live_descendant(session: AsyncSession, candidate_id: UUID) -> bool:
    child = aliased(LearningCandidateModel)
    descendant = await session.scalar(
        select(CandidateLineageModel.child_candidate_id)
        .join(child, child.id == CandidateLineageModel.child_candidate_id)
        .where(
            CandidateLineageModel.source_candidate_id == candidate_id,
            child.status.not_in(_TERMINAL_CANDIDATE_STATUSES),
        )
        .limit(1)
    )
    return descendant is not None


def _row_explicitly_protected(
    row: LearningCandidateModel,
    as_of: datetime,
) -> bool:
    if row.status in {
        CandidateStatus.EVALUATING.value,
        CandidateStatus.AWAITING_APPROVAL.value,
        CandidateStatus.APPROVED.value,
    }:
        return True
    governance = row.proposed_change.get("governance")
    if isinstance(governance, dict) and governance.get("protected") is True:
        return True
    return row.protected_until is not None and row.protected_until > as_of
