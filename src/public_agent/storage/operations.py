from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from public_agent.auth import AuthenticatedPrincipal, PrincipalStatus
from public_agent.core.types import utc_now
from public_agent.operations import (
    OPERATIONS_JOBS_READ,
    OPERATIONS_JOBS_RETRY,
    ManagedReflectionJobState,
    OperationAuditOutcome,
    ReflectionJobAuthorizationError,
    ReflectionJobConflictError,
    ReflectionJobCursorError,
    ReflectionJobPage,
    ReflectionJobQuery,
    ReflectionJobRecord,
    ReflectionJobRetryResult,
    ReflectionJobStats,
    ReflectionJobStatsQuery,
)
from public_agent.storage.models import (
    AgentModel,
    APIPrincipalAgentGrantModel,
    APIPrincipalModel,
    APITokenModel,
    OutboxJobModel,
    ReflectionJobOperationAuditEventModel,
    ReflectionJobRetryRequestModel,
    RunModel,
    TenantModel,
)
from public_agent.storage.outbox import REFLECTION_JOB_TYPE

_RETRY_ACTION = "operations.reflection_job.retry"
_VERSION_CONFLICT = "operations.jobs.version_conflict"
_STATE_CONFLICT = "operations.jobs.state_conflict"
_IDEMPOTENCY_CONFLICT = "operations.jobs.idempotency_conflict"
_RESOURCE_NOT_FOUND = "operations.jobs.not_found"
_AGENT_SCOPE_DENIED = "operations.jobs.agent_scope_denied"


class _CurrentActorAuthorizationError(ReflectionJobAuthorizationError):
    pass


@dataclass(frozen=True, slots=True)
class _ActorScope:
    tenant: TenantModel
    principal: APIPrincipalModel
    token: APITokenModel
    agent_ids: tuple[UUID, ...]

    def allows(self, agent_id: UUID) -> bool:
        return self.principal.all_agents or agent_id in self.agent_ids


class PostgresReflectionJobOperations:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def stats(
        self,
        query: ReflectionJobStatsQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobStats:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_actor_scope(
                session,
                actor=actor,
                required_permission=OPERATIONS_JOBS_READ,
            )
            agent_uuid = await _resolve_agent_filter(
                session,
                scope=scope,
                agent_key=query.agent_id,
            )
            filters = _job_filters(
                scope=scope,
                handler_version=query.handler_version,
                agent_id=agent_uuid,
            )
            status_rows = (
                await session.execute(
                    select(OutboxJobModel.status, func.count(OutboxJobModel.id))
                    .join(RunModel, RunModel.id == OutboxJobModel.run_id)
                    .where(*filters)
                    .group_by(OutboxJobModel.status)
                )
            ).all()
            grouped = {status: int(count) for status, count in status_rows}
            oldest_available_at = await session.scalar(
                select(func.min(OutboxJobModel.available_at))
                .join(RunModel, RunModel.id == OutboxJobModel.run_id)
                .where(
                    *filters,
                    OutboxJobModel.status.in_(
                        (
                            ManagedReflectionJobState.PENDING.value,
                            ManagedReflectionJobState.RETRY_WAIT.value,
                        )
                    ),
                )
            )
        age_seconds = None
        if oldest_available_at is not None:
            age_seconds = max(int((utc_now() - oldest_available_at).total_seconds()), 0)
        return ReflectionJobStats(
            handler_version=query.handler_version,
            agent_id=query.agent_id,
            pending=grouped.get(ManagedReflectionJobState.PENDING.value, 0),
            processing=grouped.get(ManagedReflectionJobState.PROCESSING.value, 0),
            retry_wait=grouped.get(ManagedReflectionJobState.RETRY_WAIT.value, 0),
            succeeded=grouped.get(ManagedReflectionJobState.SUCCEEDED.value, 0),
            dead_letter=grouped.get(ManagedReflectionJobState.DEAD_LETTER.value, 0),
            oldest_available_at=oldest_available_at,
            oldest_available_age_seconds=age_seconds,
        )

    async def list_jobs(
        self,
        query: ReflectionJobQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobPage:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_actor_scope(
                session,
                actor=actor,
                required_permission=OPERATIONS_JOBS_READ,
            )
            agent_uuid = await _resolve_agent_filter(
                session,
                scope=scope,
                agent_key=query.agent_id,
            )
            scope_hash = _scope_hash(scope)
            after = (
                _decode_cursor(query.cursor, query=query, scope_hash=scope_hash)
                if query.cursor is not None
                else None
            )
            filters = _job_filters(
                scope=scope,
                handler_version=query.handler_version,
                agent_id=agent_uuid,
            )
            if query.status is not None:
                filters.append(OutboxJobModel.status == query.status.value)
            if after is not None:
                created_at, job_id = after
                filters.append(
                    or_(
                        OutboxJobModel.created_at < created_at,
                        and_(
                            OutboxJobModel.created_at == created_at,
                            OutboxJobModel.id < job_id,
                        ),
                    )
                )
            rows = tuple(
                (
                    await session.execute(
                        select(OutboxJobModel, AgentModel.agent_key)
                        .join(RunModel, RunModel.id == OutboxJobModel.run_id)
                        .join(AgentModel, AgentModel.id == RunModel.agent_id)
                        .where(*filters)
                        .order_by(
                            OutboxJobModel.created_at.desc(),
                            OutboxJobModel.id.desc(),
                        )
                        .limit(query.limit + 1)
                    )
                ).all()
            )
        page_rows = rows[: query.limit]
        items = tuple(_job_record(row, agent_key=agent_key) for row, agent_key in page_rows)
        next_cursor = None
        if len(rows) > query.limit and page_rows:
            last, _ = page_rows[-1]
            next_cursor = _encode_cursor(
                created_at=last.created_at,
                job_id=last.id,
                query=query,
                scope_hash=scope_hash,
            )
        return ReflectionJobPage(items=items, next_cursor=next_cursor)

    async def get_job(
        self,
        *,
        job_id: UUID,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobRecord:
        async with self._sessions() as session, session.begin():
            scope = await _resolve_actor_scope(
                session,
                actor=actor,
                required_permission=OPERATIONS_JOBS_READ,
            )
            scoped = (
                await session.execute(
                    select(OutboxJobModel, RunModel, AgentModel)
                    .join(RunModel, RunModel.id == OutboxJobModel.run_id)
                    .join(AgentModel, AgentModel.id == RunModel.agent_id)
                    .where(
                        OutboxJobModel.id == job_id,
                        OutboxJobModel.tenant_id == scope.tenant.id,
                        OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
                    )
                )
            ).one_or_none()
            if scoped is None:
                raise KeyError("Unknown reflection job")
            job, run, agent = scoped._tuple()
            if not scope.allows(run.agent_id):
                raise ReflectionJobAuthorizationError("reflection job agent scope denied")
            return _job_record(job, agent_key=agent.agent_key)

    async def retry_job(
        self,
        *,
        job_id: UUID,
        expected_version: int,
        idempotency_key: str,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobRetryResult:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        try:
            return await self._retry_in_transaction(
                job_id=job_id,
                expected_version=expected_version,
                key_hash=key_hash,
                actor=actor,
            )
        except _CurrentActorAuthorizationError:
            await self._audit_actor_denial(
                job_id=job_id,
                expected_version=expected_version,
                key_hash=key_hash,
                actor=actor,
            )
            raise ReflectionJobAuthorizationError("operations permission denied") from None

    async def _retry_in_transaction(
        self,
        *,
        job_id: UUID,
        expected_version: int,
        key_hash: str,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobRetryResult:
        deferred_error: Exception | None = None
        result: ReflectionJobRetryResult | None = None
        async with self._sessions() as session, session.begin():
            scope = await _resolve_actor_scope(
                session,
                actor=actor,
                required_permission=OPERATIONS_JOBS_RETRY,
                for_update=True,
            )
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        _idempotency_lock_id(scope.tenant.id, key_hash)
                    )
                )
            )
            existing = await session.scalar(
                select(ReflectionJobRetryRequestModel).where(
                    ReflectionJobRetryRequestModel.tenant_id == scope.tenant.id,
                    ReflectionJobRetryRequestModel.idempotency_key_hash == key_hash,
                )
            )
            if existing is not None:
                result, deferred_error = await _replay_existing_request(
                    session,
                    scope=scope,
                    request=existing,
                    job_id=job_id,
                    expected_version=expected_version,
                    key_hash=key_hash,
                )
            else:
                result, deferred_error = await _execute_retry(
                    session,
                    scope=scope,
                    job_id=job_id,
                    expected_version=expected_version,
                    key_hash=key_hash,
                )
        if deferred_error is not None:
            raise deferred_error
        if result is None:
            raise RuntimeError("reflection job retry did not produce a result")
        return result

    async def _audit_actor_denial(
        self,
        *,
        job_id: UUID,
        expected_version: int,
        key_hash: str,
        actor: AuthenticatedPrincipal,
    ) -> None:
        if actor.principal_id is None or actor.token_id is None:
            return
        try:
            async with self._sessions() as session, session.begin():
                tenant = await session.scalar(
                    select(TenantModel).where(TenantModel.slug == actor.tenant_id)
                )
                if tenant is None:
                    return
                _append_audit(
                    session,
                    tenant_id=tenant.id,
                    actor_principal_id=actor.principal_id,
                    actor_token_id=actor.token_id,
                    job_id=job_id,
                    action=_RETRY_ACTION,
                    outcome=OperationAuditOutcome.DENIED,
                    expected_version=expected_version,
                    error_code="operations.jobs.actor_invalid",
                    idempotency_key_hash=key_hash,
                )
        except Exception:
            return


async def _execute_retry(
    session: AsyncSession,
    *,
    scope: _ActorScope,
    job_id: UUID,
    expected_version: int,
    key_hash: str,
) -> tuple[ReflectionJobRetryResult | None, Exception | None]:
    scoped = (
        await session.execute(
            select(OutboxJobModel, RunModel, AgentModel)
            .join(RunModel, RunModel.id == OutboxJobModel.run_id)
            .join(AgentModel, AgentModel.id == RunModel.agent_id)
            .where(
                OutboxJobModel.id == job_id,
                OutboxJobModel.tenant_id == scope.tenant.id,
                OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
            )
            .with_for_update(of=OutboxJobModel)
        )
    ).one_or_none()
    if scoped is None:
        _append_audit(
            session,
            tenant_id=scope.tenant.id,
            actor_principal_id=scope.principal.id,
            actor_token_id=scope.token.id,
            job_id=job_id,
            action=_RETRY_ACTION,
            outcome=OperationAuditOutcome.DENIED,
            expected_version=expected_version,
            error_code=_RESOURCE_NOT_FOUND,
            idempotency_key_hash=key_hash,
        )
        return None, KeyError("Unknown reflection job")
    job, run, agent = scoped._tuple()
    if not scope.allows(run.agent_id):
        _append_audit(
            session,
            tenant_id=scope.tenant.id,
            actor_principal_id=scope.principal.id,
            actor_token_id=scope.token.id,
            job_id=job.id,
            run_id=run.id,
            agent_id=run.agent_id,
            action=_RETRY_ACTION,
            outcome=OperationAuditOutcome.DENIED,
            previous_status=job.status,
            expected_version=expected_version,
            result_version=job.version,
            error_code=_AGENT_SCOPE_DENIED,
            idempotency_key_hash=key_hash,
        )
        return None, ReflectionJobAuthorizationError("reflection job agent scope denied")
    conflict_code = None
    if job.version != expected_version:
        conflict_code = _VERSION_CONFLICT
    elif job.status != ManagedReflectionJobState.DEAD_LETTER.value:
        conflict_code = _STATE_CONFLICT
    if conflict_code is not None:
        session.add(
            ReflectionJobRetryRequestModel(
                id=uuid4(),
                tenant_id=scope.tenant.id,
                agent_id=run.agent_id,
                job_id=job.id,
                run_id=run.id,
                actor_principal_id=scope.principal.id,
                idempotency_key_hash=key_hash,
                expected_version=expected_version,
                previous_status=job.status,
                result_status=job.status,
                result_version=job.version,
                outcome=OperationAuditOutcome.CONFLICT.value,
                error_code=conflict_code,
            )
        )
        _append_audit(
            session,
            tenant_id=scope.tenant.id,
            actor_principal_id=scope.principal.id,
            actor_token_id=scope.token.id,
            job_id=job.id,
            run_id=run.id,
            agent_id=run.agent_id,
            action=_RETRY_ACTION,
            outcome=OperationAuditOutcome.CONFLICT,
            previous_status=job.status,
            target_status=ManagedReflectionJobState.PENDING.value,
            expected_version=expected_version,
            result_version=job.version,
            error_code=conflict_code,
            idempotency_key_hash=key_hash,
        )
        return None, ReflectionJobConflictError(conflict_code)
    previous_status = job.status
    now = utc_now()
    job.status = ManagedReflectionJobState.PENDING.value
    job.attempts_in_cycle = 0
    job.available_at = now
    job.lease_token = None
    job.lease_expires_at = None
    job.worker_id = None
    job.last_error_code = None
    job.completed_at = None
    job.result_metadata = {}
    job.version += 1
    job.updated_at = now
    session.add(
        ReflectionJobRetryRequestModel(
            id=uuid4(),
            tenant_id=scope.tenant.id,
            agent_id=run.agent_id,
            job_id=job.id,
            run_id=run.id,
            actor_principal_id=scope.principal.id,
            idempotency_key_hash=key_hash,
            expected_version=expected_version,
            previous_status=previous_status,
            result_status=job.status,
            result_version=job.version,
            outcome=OperationAuditOutcome.SUCCESS.value,
            error_code=None,
        )
    )
    _append_audit(
        session,
        tenant_id=scope.tenant.id,
        actor_principal_id=scope.principal.id,
        actor_token_id=scope.token.id,
        job_id=job.id,
        run_id=run.id,
        agent_id=run.agent_id,
        action=_RETRY_ACTION,
        outcome=OperationAuditOutcome.SUCCESS,
        previous_status=previous_status,
        target_status=job.status,
        expected_version=expected_version,
        result_version=job.version,
        idempotency_key_hash=key_hash,
    )
    return (
        ReflectionJobRetryResult(
            job_id=job.id,
            run_id=run.id,
            agent_id=agent.agent_key,
            previous_status=ManagedReflectionJobState(previous_status),
            status=ManagedReflectionJobState(job.status),
            version=job.version,
        ),
        None,
    )


async def _replay_existing_request(
    session: AsyncSession,
    *,
    scope: _ActorScope,
    request: ReflectionJobRetryRequestModel,
    job_id: UUID,
    expected_version: int,
    key_hash: str,
) -> tuple[ReflectionJobRetryResult | None, Exception | None]:
    same_request = (
        request.actor_principal_id == scope.principal.id
        and request.job_id == job_id
        and request.expected_version == expected_version
    )
    if not same_request:
        _append_audit(
            session,
            tenant_id=scope.tenant.id,
            actor_principal_id=scope.principal.id,
            actor_token_id=scope.token.id,
            job_id=job_id,
            action=_RETRY_ACTION,
            outcome=OperationAuditOutcome.CONFLICT,
            expected_version=expected_version,
            result_version=request.result_version,
            error_code=_IDEMPOTENCY_CONFLICT,
            idempotency_key_hash=key_hash,
        )
        return None, ReflectionJobConflictError(_IDEMPOTENCY_CONFLICT)
    if not scope.allows(request.agent_id):
        _append_audit(
            session,
            tenant_id=scope.tenant.id,
            actor_principal_id=scope.principal.id,
            actor_token_id=scope.token.id,
            job_id=request.job_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
            action=_RETRY_ACTION,
            outcome=OperationAuditOutcome.DENIED,
            previous_status=request.previous_status,
            target_status=request.result_status,
            expected_version=request.expected_version,
            result_version=request.result_version,
            error_code=_AGENT_SCOPE_DENIED,
            idempotency_key_hash=key_hash,
        )
        return None, ReflectionJobAuthorizationError("reflection job agent scope denied")
    agent_key = await session.scalar(
        select(AgentModel.agent_key).where(
            AgentModel.id == request.agent_id,
            AgentModel.tenant_id == scope.tenant.id,
        )
    )
    if agent_key is None:
        raise RuntimeError("reflection job retry request lost its agent scope")
    outcome = OperationAuditOutcome(request.outcome)
    _append_audit(
        session,
        tenant_id=scope.tenant.id,
        actor_principal_id=scope.principal.id,
        actor_token_id=scope.token.id,
        job_id=request.job_id,
        run_id=request.run_id,
        agent_id=request.agent_id,
        action=_RETRY_ACTION,
        outcome=outcome,
        previous_status=request.previous_status,
        target_status=request.result_status,
        expected_version=request.expected_version,
        result_version=request.result_version,
        error_code=request.error_code,
        idempotency_key_hash=key_hash,
    )
    if outcome is OperationAuditOutcome.CONFLICT:
        return None, ReflectionJobConflictError(request.error_code or _STATE_CONFLICT)
    return (
        ReflectionJobRetryResult(
            job_id=request.job_id,
            run_id=request.run_id,
            agent_id=agent_key,
            previous_status=ManagedReflectionJobState(request.previous_status),
            status=ManagedReflectionJobState(request.result_status),
            version=request.result_version,
            idempotent_replay=True,
        ),
        None,
    )


async def _resolve_actor_scope(
    session: AsyncSession,
    *,
    actor: AuthenticatedPrincipal,
    required_permission: str,
    for_update: bool = False,
) -> _ActorScope:
    if actor.principal_id is None or actor.token_id is None:
        raise _CurrentActorAuthorizationError("managed operations identity required")
    tenant = await session.scalar(
        select(TenantModel).where(
            TenantModel.slug == actor.tenant_id,
            TenantModel.active.is_(True),
        )
    )
    if tenant is None:
        raise _CurrentActorAuthorizationError("managed operations identity required")
    principal_statement = select(APIPrincipalModel).where(
        APIPrincipalModel.id == actor.principal_id,
        APIPrincipalModel.tenant_id == tenant.id,
        APIPrincipalModel.status == PrincipalStatus.ACTIVE.value,
    )
    token_statement = select(APITokenModel).where(
        APITokenModel.id == actor.token_id,
        APITokenModel.principal_id == actor.principal_id,
        APITokenModel.tenant_id == tenant.id,
        APITokenModel.revoked_at.is_(None),
        or_(APITokenModel.expires_at.is_(None), APITokenModel.expires_at > utc_now()),
    )
    if for_update:
        principal_statement = principal_statement.with_for_update()
        token_statement = token_statement.with_for_update()
    principal = await session.scalar(principal_statement)
    token = await session.scalar(token_statement)
    if principal is None or token is None or required_permission not in principal.permissions:
        raise _CurrentActorAuthorizationError("operations permission denied")
    agent_ids = tuple(
        await session.scalars(
            select(APIPrincipalAgentGrantModel.agent_id).where(
                APIPrincipalAgentGrantModel.principal_id == principal.id,
                APIPrincipalAgentGrantModel.tenant_id == tenant.id,
            )
        )
    )
    if principal.all_agents == bool(agent_ids):
        raise _CurrentActorAuthorizationError("invalid operations agent scope")
    return _ActorScope(
        tenant=tenant,
        principal=principal,
        token=token,
        agent_ids=agent_ids,
    )


async def _resolve_agent_filter(
    session: AsyncSession,
    *,
    scope: _ActorScope,
    agent_key: str | None,
) -> UUID | None:
    if agent_key is None:
        return None
    agent_id = await session.scalar(
        select(AgentModel.id).where(
            AgentModel.tenant_id == scope.tenant.id,
            AgentModel.agent_key == agent_key,
        )
    )
    if agent_id is None:
        raise KeyError("Unknown agent")
    if not scope.allows(agent_id):
        raise ReflectionJobAuthorizationError("reflection job agent scope denied")
    return agent_id


def _job_filters(
    *,
    scope: _ActorScope,
    handler_version: str,
    agent_id: UUID | None,
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [
        OutboxJobModel.tenant_id == scope.tenant.id,
        OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
        OutboxJobModel.handler_version == handler_version,
    ]
    if agent_id is not None:
        filters.append(RunModel.agent_id == agent_id)
    elif not scope.principal.all_agents:
        filters.append(RunModel.agent_id.in_(scope.agent_ids))
    return filters


def _job_record(row: OutboxJobModel, *, agent_key: str) -> ReflectionJobRecord:
    return ReflectionJobRecord(
        id=row.id,
        run_id=row.run_id,
        agent_id=agent_key,
        handler_version=row.handler_version,
        status=ManagedReflectionJobState(row.status),
        version=row.version,
        attempts=row.attempts,
        attempts_in_cycle=row.attempts_in_cycle,
        max_attempts=row.max_attempts,
        available_at=row.available_at,
        lease_expires_at=row.lease_expires_at,
        last_error_code=row.last_error_code,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _append_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_principal_id: UUID,
    actor_token_id: UUID,
    job_id: UUID,
    action: str,
    outcome: OperationAuditOutcome,
    expected_version: int,
    idempotency_key_hash: str,
    run_id: UUID | None = None,
    agent_id: UUID | None = None,
    previous_status: str | None = None,
    target_status: str | None = None,
    result_version: int | None = None,
    error_code: str | None = None,
) -> None:
    session.add(
        ReflectionJobOperationAuditEventModel(
            id=uuid4(),
            tenant_id=tenant_id,
            actor_principal_id=actor_principal_id,
            actor_token_id=actor_token_id,
            job_id=job_id,
            run_id=run_id,
            agent_id=agent_id,
            action=action,
            outcome=outcome.value,
            previous_status=previous_status,
            target_status=target_status,
            expected_version=expected_version,
            result_version=result_version,
            error_code=error_code,
            idempotency_key_hash=idempotency_key_hash,
        )
    )


def _scope_hash(scope: _ActorScope) -> str:
    agent_scope = "*" if scope.principal.all_agents else ",".join(
        sorted(str(agent_id) for agent_id in scope.agent_ids)
    )
    return hashlib.sha256(f"{scope.tenant.id}|{agent_scope}".encode()).hexdigest()


def _encode_cursor(
    *,
    created_at: datetime,
    job_id: UUID,
    query: ReflectionJobQuery,
    scope_hash: str,
) -> str:
    payload = json.dumps(
        {
            "agent_id": query.agent_id,
            "created_at": created_at.isoformat(),
            "handler_version": query.handler_version,
            "id": str(job_id),
            "scope": scope_hash,
            "status": query.status.value if query.status is not None else None,
            "v": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    value: str,
    *,
    query: ReflectionJobQuery,
    scope_hash: str,
) -> tuple[datetime, UUID]:
    if not value or len(value) > 500 or "=" in value:
        raise ReflectionJobCursorError("invalid reflection job cursor")
    try:
        encoded = value.encode("ascii")
        padding = b"=" * (-len(encoded) % 4)
        decoded = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
        if canonical != value:
            raise ValueError
        payload = json.loads(decoded.decode("utf-8"))
        expected_keys = {
            "agent_id",
            "created_at",
            "handler_version",
            "id",
            "scope",
            "status",
            "v",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise TypeError
        if payload["v"] != 1:
            raise ValueError
        expected_status = query.status.value if query.status is not None else None
        if (
            payload["agent_id"] != query.agent_id
            or payload["handler_version"] != query.handler_version
            or payload["status"] != expected_status
            or payload["scope"] != scope_hash
        ):
            raise ValueError
        created_at = datetime.fromisoformat(payload["created_at"])
        job_id = UUID(payload["id"])
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
        raise ReflectionJobCursorError("invalid reflection job cursor") from exc
    return created_at, job_id


def _idempotency_lock_id(tenant_id: UUID, key_hash: str) -> int:
    digest = hashlib.sha256(f"operations-retry|{tenant_id}|{key_hash}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
