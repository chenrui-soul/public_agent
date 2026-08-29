from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.core.types import utc_now
from public_agent.growth.management import (
    CandidateApprovalRecord,
    CandidateEvaluationRecord,
    CandidateManagementPage,
    CandidateManagementQuery,
    CandidateManagementRecord,
    CandidateStateConflictError,
    GrowthCursorError,
    MemoryManagementPage,
    MemoryManagementQuery,
    MemoryManagementRecord,
    PublishedMemoryRecord,
)
from public_agent.growth.models import CandidateStatus, EvaluationResult, LearningCandidate
from public_agent.memory.base import MemoryType
from public_agent.storage.models import (
    AgentModel,
    ApprovalModel,
    EvaluationModel,
    LearningCandidateModel,
    MemoryModel,
    TenantModel,
)

_MANAGEMENT_VERSION_METRIC = "_management_candidate_version"
_MEMORY_STATUSES = frozenset({"candidate", "active", "superseded", "expired", "rejected"})


@dataclass(frozen=True, slots=True)
class _Scope:
    tenant_id: UUID
    agent_id: UUID


class PostgresGrowthManagementRepository:
    """Side-effect-free management queries and guarded candidate decisions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_memories(self, query: MemoryManagementQuery) -> MemoryManagementPage:
        if query.status is not None and query.status not in _MEMORY_STATUSES:
            raise ValueError("Unsupported memory status")
        cursor = _decode_cursor(query.cursor) if query.cursor is not None else None
        async with self._sessions() as session:
            scope = await _resolve_scope(session, query.tenant_id, query.agent_id)
            statement = select(MemoryModel).where(
                MemoryModel.tenant_id == scope.tenant_id,
                MemoryModel.agent_id == scope.agent_id,
                MemoryModel.domain_id == query.domain_id,
            )
            if query.namespace is not None:
                statement = statement.where(MemoryModel.namespace == query.namespace)
            if query.memory_type is not None:
                statement = statement.where(MemoryModel.memory_type == query.memory_type.value)
            if query.status is not None:
                statement = statement.where(MemoryModel.status == query.status)
            if query.text is not None:
                statement = statement.where(
                    MemoryModel.content.ilike(_contains_pattern(query.text), escape="\\")
                )
            if cursor is not None:
                created_at, record_id = cursor
                statement = statement.where(
                    or_(
                        MemoryModel.created_at < created_at,
                        and_(
                            MemoryModel.created_at == created_at,
                            MemoryModel.id < record_id,
                        ),
                    )
                )
            rows = tuple(
                (
                    await session.scalars(
                        statement.order_by(
                            MemoryModel.created_at.desc(),
                            MemoryModel.id.desc(),
                        ).limit(query.limit + 1)
                    )
                ).all()
            )
        page_rows = rows[: query.limit]
        next_cursor = None
        if len(rows) > query.limit and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)
        return MemoryManagementPage(
            items=tuple(
                _memory_record(row, tenant_id=query.tenant_id, agent_id=query.agent_id)
                for row in page_rows
            ),
            next_cursor=next_cursor,
        )

    async def list_candidates(
        self,
        query: CandidateManagementQuery,
    ) -> CandidateManagementPage:
        cursor = _decode_cursor(query.cursor) if query.cursor is not None else None
        async with self._sessions() as session:
            scope = await _resolve_scope(session, query.tenant_id, query.agent_id)
            statement = select(LearningCandidateModel).where(
                LearningCandidateModel.tenant_id == scope.tenant_id,
                LearningCandidateModel.agent_id == scope.agent_id,
                LearningCandidateModel.domain_id == query.domain_id,
            )
            if query.status is not None:
                statement = statement.where(
                    LearningCandidateModel.status == query.status.value
                )
            if query.candidate_type is not None:
                statement = statement.where(
                    LearningCandidateModel.candidate_type == query.candidate_type.value
                )
            if query.risk is not None:
                statement = statement.where(LearningCandidateModel.risk == query.risk.value)
            if query.text is not None:
                pattern = _contains_pattern(query.text)
                statement = statement.where(
                    or_(
                        LearningCandidateModel.title.ilike(pattern, escape="\\"),
                        LearningCandidateModel.proposed_change["content"].astext.ilike(
                            pattern,
                            escape="\\",
                        ),
                    )
                )
            if cursor is not None:
                created_at, candidate_id = cursor
                statement = statement.where(
                    or_(
                        LearningCandidateModel.created_at < created_at,
                        and_(
                            LearningCandidateModel.created_at == created_at,
                            LearningCandidateModel.id < candidate_id,
                        ),
                    )
                )
            rows = tuple(
                (
                    await session.scalars(
                        statement.order_by(
                            LearningCandidateModel.created_at.desc(),
                            LearningCandidateModel.id.desc(),
                        ).limit(query.limit + 1)
                    )
                ).all()
            )
            page_rows = rows[: query.limit]
            records = await _candidate_records(
                session,
                page_rows,
                tenant_id=query.tenant_id,
                agent_id=query.agent_id,
            )
        next_cursor = None
        if len(rows) > query.limit and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)
        return CandidateManagementPage(items=records, next_cursor=next_cursor)

    async def get_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
    ) -> CandidateManagementRecord:
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
            records = await _candidate_records(
                session,
                (row,),
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            return records[0]

    async def record_evaluation(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
        result: EvaluationResult,
    ) -> CandidateManagementRecord:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, tenant_id, agent_id)
            row = await _lock_candidate(
                session,
                scope=scope,
                candidate_id=candidate_id,
                domain_id=domain_id,
            )
            latest = await _latest_evaluation(session, candidate_id)
            target = (
                CandidateStatus.AWAITING_APPROVAL
                if result.passed
                else CandidateStatus.REJECTED
            )
            if _is_evaluation_replay(
                row,
                latest,
                expected_version=expected_version,
                target=target,
            ):
                pass
            elif row.version != expected_version or row.status != CandidateStatus.PENDING.value:
                raise CandidateStateConflictError(
                    "Candidate changed before the evaluation result could be committed"
                )
            else:
                metrics = dict(result.metrics)
                metrics[_MANAGEMENT_VERSION_METRIC] = float(expected_version)
                session.add(
                    EvaluationModel(
                        tenant_id=row.tenant_id,
                        candidate_id=row.id,
                        passed=result.passed,
                        score=result.score,
                        summary=result.summary,
                        metrics=metrics,
                        created_at=result.created_at,
                        updated_at=result.created_at,
                    )
                )
                row.status = target.value
                row.version += 2
                row.updated_at = utc_now()
        return await self.get_candidate(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
        )

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
    ) -> CandidateManagementRecord:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_scope(session, tenant_id, agent_id)
            row = await _lock_candidate(
                session,
                scope=scope,
                candidate_id=candidate_id,
                domain_id=domain_id,
            )
            approval = await _latest_approval(session, candidate_id)
            if _is_decision_replay(
                row,
                approval,
                expected_version=expected_version,
                decision="rejected",
                decided_by=decided_by,
                decision_note=decision_note,
            ):
                pass
            elif (
                row.version != expected_version
                or row.status != CandidateStatus.AWAITING_APPROVAL.value
            ):
                raise CandidateStateConflictError(
                    "Candidate changed before the rejection decision could be committed"
                )
            else:
                evaluation = await _latest_evaluation(session, candidate_id)
                if evaluation is None or not evaluation.passed:
                    raise CandidateStateConflictError(
                        "Candidate requires a passing evaluation before human review"
                    )
                session.add(
                    ApprovalModel(
                        tenant_id=row.tenant_id,
                        candidate_id=row.id,
                        status="rejected",
                        reason="Knowledge candidate rejected by an authorized reviewer",
                        requested_payload={
                            "candidate_id": str(row.id),
                            "fingerprint": row.fingerprint,
                            "expected_candidate_version": expected_version,
                            "decision": "rejected",
                        },
                        decided_by=decided_by,
                        decision_note=decision_note,
                    )
                )
                row.status = CandidateStatus.REJECTED.value
                row.version += 1
                row.updated_at = utc_now()
        return await self.get_candidate(
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            domain_id=domain_id,
        )


async def _resolve_scope(session: AsyncSession, tenant_slug: str, agent_key: str) -> _Scope:
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
    return _Scope(tenant_id=tenant.id, agent_id=agent.id)


async def _lock_candidate(
    session: AsyncSession,
    *,
    scope: _Scope,
    candidate_id: UUID,
    domain_id: str,
) -> LearningCandidateModel:
    row = await session.scalar(
        select(LearningCandidateModel)
        .where(
            LearningCandidateModel.id == candidate_id,
            LearningCandidateModel.tenant_id == scope.tenant_id,
            LearningCandidateModel.agent_id == scope.agent_id,
            LearningCandidateModel.domain_id == domain_id,
        )
        .with_for_update()
    )
    if row is None:
        raise KeyError(f"Unknown learning candidate: {candidate_id}")
    return row


async def _candidate_records(
    session: AsyncSession,
    rows: tuple[LearningCandidateModel, ...],
    *,
    tenant_id: str,
    agent_id: str,
) -> tuple[CandidateManagementRecord, ...]:
    if not rows:
        return ()
    candidate_ids = [row.id for row in rows]
    evaluations = tuple(
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
    approvals = tuple(
        (
            await session.scalars(
                select(ApprovalModel)
                .where(ApprovalModel.candidate_id.in_(candidate_ids))
                .distinct(ApprovalModel.candidate_id)
                .order_by(
                    ApprovalModel.candidate_id,
                    ApprovalModel.created_at.desc(),
                    ApprovalModel.id.desc(),
                )
            )
        ).all()
    )
    memories = tuple(
        (
            await session.scalars(
                select(MemoryModel).where(MemoryModel.candidate_id.in_(candidate_ids))
            )
        ).all()
    )
    latest_evaluations = {row.candidate_id: row for row in evaluations}
    latest_approvals = {row.candidate_id: row for row in approvals if row.candidate_id}
    published_memories = {row.candidate_id: row for row in memories if row.candidate_id}
    return tuple(
        CandidateManagementRecord(
            candidate=_candidate(row, tenant_id=tenant_id, agent_id=agent_id),
            latest_evaluation=_evaluation_record(latest_evaluations.get(row.id)),
            latest_approval=_approval_record(latest_approvals.get(row.id)),
            published_memory=_published_memory_record(published_memories.get(row.id)),
        )
        for row in rows
    )


async def _latest_evaluation(
    session: AsyncSession,
    candidate_id: UUID,
) -> EvaluationModel | None:
    return cast(
        EvaluationModel | None,
        await session.scalar(
            select(EvaluationModel)
            .where(EvaluationModel.candidate_id == candidate_id)
            .order_by(EvaluationModel.created_at.desc(), EvaluationModel.id.desc())
            .limit(1)
        ),
    )


async def _latest_approval(
    session: AsyncSession,
    candidate_id: UUID,
) -> ApprovalModel | None:
    return cast(
        ApprovalModel | None,
        await session.scalar(
            select(ApprovalModel)
            .where(ApprovalModel.candidate_id == candidate_id)
            .order_by(ApprovalModel.created_at.desc(), ApprovalModel.id.desc())
            .limit(1)
        ),
    )


def _memory_record(
    row: MemoryModel,
    *,
    tenant_id: str,
    agent_id: str,
) -> MemoryManagementRecord:
    return MemoryManagementRecord(
        id=row.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        domain_id=row.domain_id,
        namespace=row.namespace,
        memory_type=MemoryType(row.memory_type),
        content=row.content,
        status=row.status,
        confidence=row.confidence,
        importance=row.importance,
        candidate_id=row.candidate_id,
        source_run_id=row.source_run_id,
        recall_count=row.recall_count,
        last_recalled_at=row.last_recalled_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
    )


def _candidate(
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
        evidence_run_ids=tuple(UUID(value) for value in row.evidence_run_ids),
        status=row.status,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        protected_until=row.protected_until,
    )


def _evaluation_record(row: EvaluationModel | None) -> CandidateEvaluationRecord | None:
    if row is None:
        return None
    metrics = {
        key: float(value)
        for key, value in row.metrics.items()
        if key != _MANAGEMENT_VERSION_METRIC
    }
    raw_version = row.metrics.get(_MANAGEMENT_VERSION_METRIC)
    candidate_version = int(raw_version) if isinstance(raw_version, (int, float)) else None
    return CandidateEvaluationRecord(
        id=row.id,
        passed=row.passed,
        score=row.score,
        summary=row.summary,
        metrics=metrics,
        candidate_version=candidate_version,
        created_at=row.created_at,
    )


def _approval_record(row: ApprovalModel | None) -> CandidateApprovalRecord | None:
    if row is None:
        return None
    return CandidateApprovalRecord(
        id=row.id,
        status=row.status,
        decided_by=row.decided_by,
        decision_note=row.decision_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _published_memory_record(row: MemoryModel | None) -> PublishedMemoryRecord | None:
    if row is None:
        return None
    return PublishedMemoryRecord(
        id=row.id,
        status=row.status,
        recall_count=row.recall_count,
        last_recalled_at=row.last_recalled_at,
    )


def _is_evaluation_replay(
    row: LearningCandidateModel,
    evaluation: EvaluationModel | None,
    *,
    expected_version: int,
    target: CandidateStatus,
) -> bool:
    if evaluation is None:
        return False
    raw_version = evaluation.metrics.get(_MANAGEMENT_VERSION_METRIC)
    return (
        isinstance(raw_version, (int, float))
        and int(raw_version) == expected_version
        and row.version == expected_version + 2
        and row.status == target.value
    )


def _is_decision_replay(
    row: LearningCandidateModel,
    approval: ApprovalModel | None,
    *,
    expected_version: int,
    decision: str,
    decided_by: str,
    decision_note: str | None,
) -> bool:
    if approval is None:
        return False
    payload = approval.requested_payload
    return (
        row.version == expected_version + 1
        and row.status == CandidateStatus.REJECTED.value
        and approval.status == decision
        and payload.get("decision") == decision
        and payload.get("expected_candidate_version") == expected_version
        and approval.decided_by == decided_by
        and approval.decision_note == decision_note
    )


def _contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _encode_cursor(created_at: datetime, record_id: UUID) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(record_id), "v": 1},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    if not value or len(value) > 500:
        raise GrowthCursorError("invalid growth management cursor")
    try:
        padding = "=" * (-len(value) % 4)
        encoded = (value + padding).encode("ascii")
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"created_at", "id", "v"}:
            raise TypeError
        if payload["v"] != 1:
            raise ValueError
        created_at = datetime.fromisoformat(payload["created_at"])
        record_id = UUID(payload["id"])
        if created_at.tzinfo is None:
            raise ValueError
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise GrowthCursorError("invalid growth management cursor") from exc
    return created_at, record_id
