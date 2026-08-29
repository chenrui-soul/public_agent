from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Integer, and_, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.core.trace import RunTrace, RunTraceEvent
from public_agent.core.types import AgentSpec, RunContext, RunResult, RunStatus, utc_now
from public_agent.growth.models import LearningCandidate
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    OutboxJobModel,
    ReflectionWorkerHeartbeatModel,
    RunEventModel,
    RunModel,
    TenantModel,
)
from public_agent.workers.reflection import (
    ReflectionJobInput,
    ReflectionJobLeaseLostError,
    ReflectionJobState,
    ReflectionWorkerResult,
    ReflectionWorkItem,
)
from public_agent.workers.runner import (
    ReflectionBacklogSnapshot,
    ReflectionCapacitySnapshot,
    ReflectionWorkerFleetSnapshot,
    ReflectionWorkerLifecycleState,
    ReflectionWorkerRegistration,
    ReflectionWorkerRegistrationLostError,
)

REFLECTION_JOB_TYPE = "run_reflection"
DEFAULT_REFLECTION_HANDLER_VERSION = "reflection-v1"
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELED.value,
    RunStatus.TIMED_OUT.value,
}


class PostgresReflectionJobStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        handler_version: str = DEFAULT_REFLECTION_HANDLER_VERSION,
        max_attempts: int = 5,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 3_600,
    ) -> None:
        normalized_version = handler_version.strip()
        if not normalized_version or len(normalized_version) > 64:
            raise ValueError("handler_version must contain 1 to 64 characters")
        if not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100")
        if not 1 <= retry_base_seconds <= retry_max_seconds <= 86_400:
            raise ValueError("retry delays must be ordered between 1 and 86400 seconds")
        self._sessions = sessions
        self.handler_version = normalized_version
        self.max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds

    async def register_worker(self, *, worker_id: str) -> ReflectionWorkerRegistration:
        normalized_worker = worker_id.strip()
        if not normalized_worker or len(normalized_worker) > 200:
            raise ValueError("worker_id must contain 1 to 200 characters")
        instance_token = uuid4()
        now = utc_now()
        statement = (
            postgres_insert(ReflectionWorkerHeartbeatModel)
            .values(
                worker_id=normalized_worker,
                instance_token=instance_token,
                job_type=REFLECTION_JOB_TYPE,
                handler_version=self.handler_version,
                status=ReflectionWorkerLifecycleState.IDLE.value,
                processed_jobs=0,
                last_job_id=None,
                last_error_code=None,
                started_at=now,
                last_seen_at=now,
                stopped_at=None,
            )
            .on_conflict_do_update(
                index_elements=[ReflectionWorkerHeartbeatModel.worker_id],
                set_={
                    "instance_token": instance_token,
                    "job_type": REFLECTION_JOB_TYPE,
                    "handler_version": self.handler_version,
                    "status": ReflectionWorkerLifecycleState.IDLE.value,
                    "processed_jobs": 0,
                    "last_job_id": None,
                    "last_error_code": None,
                    "started_at": now,
                    "last_seen_at": now,
                    "stopped_at": None,
                },
            )
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)
        return ReflectionWorkerRegistration(
            worker_id=normalized_worker,
            instance_token=instance_token,
        )

    async def heartbeat_worker(
        self,
        registration: ReflectionWorkerRegistration,
        *,
        state: ReflectionWorkerLifecycleState,
        processed_jobs: int,
        last_result: ReflectionWorkerResult | None,
    ) -> None:
        if processed_jobs < 0:
            raise ValueError("processed_jobs must not be negative")
        job_id, error_code = _worker_result_metadata(last_result)
        now = utc_now()
        async with self._sessions() as session, session.begin():
            updated = await session.scalar(
                update(ReflectionWorkerHeartbeatModel)
                .where(
                    ReflectionWorkerHeartbeatModel.worker_id == registration.worker_id,
                    ReflectionWorkerHeartbeatModel.instance_token
                    == registration.instance_token,
                )
                .values(
                    status=state.value,
                    processed_jobs=processed_jobs,
                    last_job_id=job_id,
                    last_error_code=error_code,
                    last_seen_at=now,
                    stopped_at=None,
                )
                .returning(ReflectionWorkerHeartbeatModel.worker_id)
            )
        if updated is None:
            raise ReflectionWorkerRegistrationLostError(
                "reflection worker registration was replaced"
            )

    async def stop_worker(
        self,
        registration: ReflectionWorkerRegistration,
        *,
        processed_jobs: int,
        last_result: ReflectionWorkerResult | None,
        error_code: str | None,
    ) -> None:
        if processed_jobs < 0:
            raise ValueError("processed_jobs must not be negative")
        job_id, result_error = _worker_result_metadata(last_result)
        final_error = error_code or result_error
        if final_error is not None and _SAFE_ERROR_CODE.fullmatch(final_error) is None:
            raise ValueError("error_code must be a safe machine-readable value")
        now = utc_now()
        async with self._sessions() as session, session.begin():
            updated = await session.scalar(
                update(ReflectionWorkerHeartbeatModel)
                .where(
                    ReflectionWorkerHeartbeatModel.worker_id == registration.worker_id,
                    ReflectionWorkerHeartbeatModel.instance_token
                    == registration.instance_token,
                )
                .values(
                    status=ReflectionWorkerLifecycleState.STOPPED.value,
                    processed_jobs=processed_jobs,
                    last_job_id=job_id,
                    last_error_code=final_error,
                    last_seen_at=now,
                    stopped_at=now,
                )
                .returning(ReflectionWorkerHeartbeatModel.worker_id)
            )
        if updated is None:
            raise ReflectionWorkerRegistrationLostError(
                "reflection worker registration was replaced"
            )

    async def backlog_snapshot(self) -> ReflectionBacklogSnapshot:
        async with self._sessions() as session:
            return await _backlog_snapshot(
                session,
                handler_version=self.handler_version,
            )

    async def capacity_snapshot(
        self,
        *,
        stale_after_seconds: int,
    ) -> ReflectionCapacitySnapshot:
        if not 5 <= stale_after_seconds <= 3_600:
            raise ValueError("stale_after_seconds must be between 5 and 3600")
        observed_at = utc_now()
        stale_cutoff = observed_at - timedelta(seconds=stale_after_seconds)
        async with self._sessions() as session:
            backlog = await _backlog_snapshot(
                session,
                handler_version=self.handler_version,
            )
            status_rows = (
                await session.execute(
                    select(
                        ReflectionWorkerHeartbeatModel.status,
                        func.count(ReflectionWorkerHeartbeatModel.worker_id),
                    )
                    .where(
                        ReflectionWorkerHeartbeatModel.job_type == REFLECTION_JOB_TYPE,
                        ReflectionWorkerHeartbeatModel.handler_version
                        == self.handler_version,
                    )
                    .group_by(ReflectionWorkerHeartbeatModel.status)
                )
            ).all()
            grouped = {status: int(count) for status, count in status_rows}
            stale = int(
                await session.scalar(
                    select(func.count(ReflectionWorkerHeartbeatModel.worker_id)).where(
                        ReflectionWorkerHeartbeatModel.job_type == REFLECTION_JOB_TYPE,
                        ReflectionWorkerHeartbeatModel.handler_version
                        == self.handler_version,
                        ReflectionWorkerHeartbeatModel.status
                        != ReflectionWorkerLifecycleState.STOPPED.value,
                        ReflectionWorkerHeartbeatModel.last_seen_at < stale_cutoff,
                    )
                )
                or 0
            )
            errored = int(
                await session.scalar(
                    select(func.count(ReflectionWorkerHeartbeatModel.worker_id)).where(
                        ReflectionWorkerHeartbeatModel.job_type == REFLECTION_JOB_TYPE,
                        ReflectionWorkerHeartbeatModel.handler_version
                        == self.handler_version,
                        ReflectionWorkerHeartbeatModel.status
                        != ReflectionWorkerLifecycleState.STOPPED.value,
                        ReflectionWorkerHeartbeatModel.last_seen_at >= stale_cutoff,
                        ReflectionWorkerHeartbeatModel.last_error_code.is_not(None),
                    )
                )
                or 0
            )
            fleet_row = (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(ReflectionWorkerHeartbeatModel.processed_jobs),
                            0,
                        ),
                        func.min(ReflectionWorkerHeartbeatModel.last_seen_at),
                        func.max(ReflectionWorkerHeartbeatModel.last_seen_at),
                    ).where(
                        ReflectionWorkerHeartbeatModel.job_type == REFLECTION_JOB_TYPE,
                        ReflectionWorkerHeartbeatModel.handler_version
                        == self.handler_version,
                    )
                )
            ).one()
        stopped = grouped.get(ReflectionWorkerLifecycleState.STOPPED.value, 0)
        non_stopped = sum(grouped.values()) - stopped
        return ReflectionCapacitySnapshot(
            observed_at=observed_at,
            backlog=backlog,
            workers=ReflectionWorkerFleetSnapshot(
                registered=sum(grouped.values()),
                active=max(0, non_stopped - stale),
                stale=stale,
                stopped=stopped,
                errored=errored,
                processed_jobs=int(fleet_row[0]),
                oldest_last_seen_at=fleet_row[1],
                newest_last_seen_at=fleet_row[2],
            ),
        )

    async def ensure_job(self, *, run_id: UUID) -> UUID:
        async with self._sessions() as session, session.begin():
            run = await session.scalar(
                select(RunModel).where(RunModel.id == run_id).with_for_update()
            )
            if run is None:
                raise KeyError(f"Unknown run: {run_id}")
            return await enqueue_reflection_job(
                session,
                run,
                handler_version=self.handler_version,
                max_attempts=self.max_attempts,
            )

    async def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ReflectionWorkItem | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker or len(normalized_worker) > 200:
            raise ValueError("worker_id must contain 1 to 200 characters")
        if not 5 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        now = utc_now()
        async with self._sessions() as session, session.begin():
            expired_duration = cast(
                func.greatest(
                    0,
                    func.extract(
                        "epoch",
                        now - OutboxJobModel.last_started_at,
                    )
                    * 1_000,
                ),
                Integer,
            )
            await session.execute(
                update(OutboxJobModel)
                .where(
                    OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
                    OutboxJobModel.handler_version == self.handler_version,
                    OutboxJobModel.status == ReflectionJobState.PROCESSING.value,
                    OutboxJobModel.lease_expires_at <= now,
                    OutboxJobModel.attempts_in_cycle >= OutboxJobModel.max_attempts,
                )
                .values(
                    status=ReflectionJobState.DEAD_LETTER.value,
                    lease_token=None,
                    lease_expires_at=None,
                    worker_id=None,
                    last_error_code="reflection_worker.lease_expired",
                    completed_at=now,
                    last_processing_duration_ms=func.coalesce(expired_duration, 0),
                    total_processing_duration_ms=(
                        OutboxJobModel.total_processing_duration_ms
                        + func.coalesce(expired_duration, 0)
                    ),
                    version=OutboxJobModel.version + 1,
                    updated_at=now,
                )
            )
            eligible = or_(
                and_(
                    OutboxJobModel.status.in_(
                        (
                            ReflectionJobState.PENDING.value,
                            ReflectionJobState.RETRY_WAIT.value,
                        )
                    ),
                    OutboxJobModel.available_at <= now,
                ),
                and_(
                    OutboxJobModel.status == ReflectionJobState.PROCESSING.value,
                    OutboxJobModel.lease_expires_at <= now,
                ),
            )
            row = await session.scalar(
                select(OutboxJobModel)
                .where(
                    OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
                    OutboxJobModel.handler_version == self.handler_version,
                    OutboxJobModel.attempts_in_cycle < OutboxJobModel.max_attempts,
                    eligible,
                )
                .order_by(
                    OutboxJobModel.available_at,
                    OutboxJobModel.created_at,
                    OutboxJobModel.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            if (
                row.status == ReflectionJobState.PROCESSING.value
                and row.last_started_at is not None
            ):
                _record_processing_duration(row, now=now)
            lease_token = uuid4()
            row.status = ReflectionJobState.PROCESSING.value
            row.attempts += 1
            row.attempts_in_cycle += 1
            row.lease_token = lease_token
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.worker_id = normalized_worker
            row.last_error_code = None
            row.completed_at = None
            row.last_started_at = now
            row.last_processing_duration_ms = None
            row.version += 1
            row.updated_at = now
            await session.flush()
            return ReflectionWorkItem(
                job_id=row.id,
                run_id=row.run_id,
                lease_token=lease_token,
                attempts=row.attempts_in_cycle,
                max_attempts=row.max_attempts,
            )

    async def load_input(self, work: ReflectionWorkItem) -> ReflectionJobInput:
        async with self._sessions() as session:
            scoped = (
                await session.execute(
                    select(
                        OutboxJobModel,
                        RunModel,
                        TenantModel,
                        AgentModel,
                        AgentVersionModel,
                    )
                    .join(RunModel, RunModel.id == OutboxJobModel.run_id)
                    .join(TenantModel, TenantModel.id == RunModel.tenant_id)
                    .join(AgentModel, AgentModel.id == RunModel.agent_id)
                    .join(
                        AgentVersionModel,
                        AgentVersionModel.id == RunModel.agent_version_id,
                    )
                    .where(OutboxJobModel.id == work.job_id)
                )
            ).one_or_none()
            if scoped is None:
                raise KeyError(f"Unknown reflection job: {work.job_id}")
            job, run, tenant, agent, version = scoped._tuple()
            _verify_job_ownership(job, work, now=utc_now())
            if job.payload != {"schema_version": 1}:
                raise ValueError("Reflection job payload is not a supported safe schema")
            if run.status not in _TERMINAL_RUN_STATUSES:
                raise ValueError("Reflection job run is not terminal")
            event_rows = tuple(
                await session.scalars(
                    select(RunEventModel)
                    .where(
                        RunEventModel.tenant_id == tenant.id,
                        RunEventModel.run_id == run.id,
                    )
                    .order_by(RunEventModel.sequence)
                )
            )
        context_payload = run.metadata_json.get("run_context")
        if not isinstance(context_payload, dict):
            raise ValueError("Run is missing its persisted context")
        context = RunContext.model_validate(context_payload)
        if context.tenant_id != tenant.slug:
            raise ValueError("Persisted run context does not match its tenant")
        result = RunResult(
            run_id=run.id,
            status=RunStatus(run.status),
            output=run.output,
            error=run.error,
            steps=int(run.metadata_json.get("steps", 0)),
        )
        trace = RunTrace(
            run_id=run.id,
            tenant_id=tenant.slug,
            agent_id=agent.agent_key,
            agent_version=version.version,
            task=run.task,
            status=RunStatus(run.status),
            output=run.output,
            error=run.error,
            events=tuple(
                RunTraceEvent(
                    id=event.id,
                    sequence=event.sequence,
                    event_type=event.event_type,
                    payload=event.payload,
                    created_at=event.created_at,
                )
                for event in event_rows
            ),
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        spec = AgentSpec(
            id=agent.agent_key,
            name=agent.name,
            version=version.version,
            instructions=version.instructions,
            memory_namespace=version.memory_namespace,
            metadata={"historical_agent_version_id": str(version.id)},
        )
        return ReflectionJobInput(
            agent=spec,
            context=context,
            task=run.task,
            result=result,
            trace=trace,
        )

    async def heartbeat(
        self,
        work: ReflectionWorkItem,
        *,
        lease_seconds: int,
    ) -> None:
        if not 5 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        now = utc_now()
        async with self._sessions() as session, session.begin():
            row = await _owned_job(session, work, now=now)
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.version += 1
            row.updated_at = now

    async def complete(
        self,
        work: ReflectionWorkItem,
        *,
        candidates: tuple[LearningCandidate, ...],
    ) -> None:
        now = utc_now()
        async with self._sessions() as session, session.begin():
            row = await _owned_job(session, work, now=now)
            run = await session.scalar(
                select(RunModel).where(RunModel.id == row.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(f"Unknown run for reflection job: {row.run_id}")
            candidate_ids = [str(candidate.id) for candidate in candidates]
            _record_processing_duration(row, now=now)
            row.status = ReflectionJobState.SUCCEEDED.value
            row.result_metadata = {
                "candidate_count": len(candidate_ids),
                "candidate_ids": candidate_ids,
            }
            row.lease_token = None
            row.lease_expires_at = None
            row.worker_id = None
            row.last_error_code = None
            row.completed_at = now
            row.version += 1
            row.updated_at = now
            await _append_run_event(
                session,
                run,
                event_type="knowledge.candidates.created",
                payload={
                    "count": len(candidate_ids),
                    "candidate_ids": candidate_ids,
                    "outbox_job_id": str(row.id),
                },
            )

    async def fail(
        self,
        work: ReflectionWorkItem,
        *,
        error_code: str,
    ) -> ReflectionJobState:
        if _SAFE_ERROR_CODE.fullmatch(error_code) is None:
            raise ValueError("error_code must be a safe machine-readable value")
        now = utc_now()
        async with self._sessions() as session, session.begin():
            row = await _owned_job(session, work, now=now)
            run = await session.scalar(
                select(RunModel).where(RunModel.id == row.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(f"Unknown run for reflection job: {row.run_id}")
            exhausted = row.attempts_in_cycle >= row.max_attempts
            _record_processing_duration(row, now=now)
            state = (
                ReflectionJobState.DEAD_LETTER
                if exhausted
                else ReflectionJobState.RETRY_WAIT
            )
            row.status = state.value
            row.last_error_code = error_code
            row.lease_token = None
            row.lease_expires_at = None
            row.worker_id = None
            row.version += 1
            row.updated_at = now
            if exhausted:
                row.completed_at = now
            else:
                delay = min(
                    self._retry_base_seconds
                    * (2 ** max(row.attempts_in_cycle - 1, 0)),
                    self._retry_max_seconds,
                )
                row.available_at = now + timedelta(seconds=delay)
                row.completed_at = None
            await _append_run_event(
                session,
                run,
                event_type=(
                    "knowledge.reflection.dead_letter"
                    if exhausted
                    else "knowledge.reflection.retry_scheduled"
                ),
                payload={
                    "error_code": error_code,
                    "attempts": row.attempts_in_cycle,
                    "total_attempts": row.attempts,
                    "max_attempts": row.max_attempts,
                    "outbox_job_id": str(row.id),
                },
            )
            return state


async def _backlog_snapshot(
    session: AsyncSession,
    *,
    handler_version: str,
) -> ReflectionBacklogSnapshot:
    status_rows = (
        await session.execute(
            select(OutboxJobModel.status, func.count(OutboxJobModel.id))
            .where(
                OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
                OutboxJobModel.handler_version == handler_version,
            )
            .group_by(OutboxJobModel.status)
        )
    ).all()
    grouped = {status: int(count) for status, count in status_rows}
    oldest_available_at = await session.scalar(
        select(func.min(OutboxJobModel.available_at)).where(
            OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
            OutboxJobModel.handler_version == handler_version,
            OutboxJobModel.status.in_(
                (
                    ReflectionJobState.PENDING.value,
                    ReflectionJobState.RETRY_WAIT.value,
                )
            ),
        )
    )
    return ReflectionBacklogSnapshot(
        pending=grouped.get(ReflectionJobState.PENDING.value, 0),
        processing=grouped.get(ReflectionJobState.PROCESSING.value, 0),
        retry_wait=grouped.get(ReflectionJobState.RETRY_WAIT.value, 0),
        succeeded=grouped.get(ReflectionJobState.SUCCEEDED.value, 0),
        dead_letter=grouped.get(ReflectionJobState.DEAD_LETTER.value, 0),
        oldest_available_at=oldest_available_at,
    )


async def enqueue_reflection_job(
    session: AsyncSession,
    run: RunModel,
    *,
    handler_version: str = DEFAULT_REFLECTION_HANDLER_VERSION,
    max_attempts: int = 5,
) -> UUID:
    if run.status not in _TERMINAL_RUN_STATUSES:
        raise ValueError("Only terminal runs can enqueue reflection jobs")
    statement = (
        postgres_insert(OutboxJobModel)
        .values(
            id=uuid4(),
            tenant_id=run.tenant_id,
            run_id=run.id,
            job_type=REFLECTION_JOB_TYPE,
            handler_version=handler_version,
            status=ReflectionJobState.PENDING.value,
            payload={"schema_version": 1},
            result_metadata={},
            version=1,
            attempts=0,
            attempts_in_cycle=0,
            max_attempts=max_attempts,
            available_at=utc_now(),
            last_started_at=None,
            last_processing_duration_ms=None,
            total_processing_duration_ms=0,
        )
        .on_conflict_do_nothing(constraint="uq_outbox_jobs_run_handler")
        .returning(OutboxJobModel.id)
    )
    inserted = await session.scalar(statement)
    if inserted is not None:
        return inserted
    existing = await session.scalar(
        select(OutboxJobModel.id).where(
            OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
            OutboxJobModel.run_id == run.id,
            OutboxJobModel.handler_version == handler_version,
        )
    )
    if existing is None:
        raise RuntimeError("Idempotent reflection job conflict could not be resolved")
    return existing


async def _owned_job(
    session: AsyncSession,
    work: ReflectionWorkItem,
    *,
    now: datetime,
) -> OutboxJobModel:
    row = await session.scalar(
        select(OutboxJobModel)
        .where(OutboxJobModel.id == work.job_id)
        .with_for_update()
    )
    if row is None:
        raise KeyError(f"Unknown reflection job: {work.job_id}")
    _verify_job_ownership(row, work, now=now)
    return row


def _verify_job_ownership(
    row: OutboxJobModel,
    work: ReflectionWorkItem,
    *,
    now: datetime,
) -> None:
    if (
        row.status != ReflectionJobState.PROCESSING.value
        or row.lease_token != work.lease_token
        or row.lease_expires_at is None
        or row.lease_expires_at <= now
    ):
        raise ReflectionJobLeaseLostError("reflection job lease is stale or expired")


def _record_processing_duration(row: OutboxJobModel, *, now: datetime) -> None:
    if row.last_started_at is None:
        raise ValueError("Reflection job is missing its processing start time")
    duration_ms = max(0, int((now - row.last_started_at).total_seconds() * 1_000))
    row.last_processing_duration_ms = duration_ms
    row.total_processing_duration_ms += duration_ms


async def _append_run_event(
    session: AsyncSession,
    run: RunModel,
    *,
    event_type: str,
    payload: dict[str, object],
) -> None:
    current = await session.scalar(
        select(func.coalesce(func.max(RunEventModel.sequence), 0)).where(
            RunEventModel.run_id == run.id
        )
    )
    session.add(
        RunEventModel(
            id=uuid4(),
            tenant_id=run.tenant_id,
            run_id=run.id,
            sequence=int(current or 0) + 1,
            event_type=event_type,
            payload=payload,
        )
    )


def _worker_result_metadata(
    result: ReflectionWorkerResult | None,
) -> tuple[UUID | None, str | None]:
    if result is None:
        return None, None
    if result.error_code is not None and _SAFE_ERROR_CODE.fullmatch(result.error_code) is None:
        raise ValueError("worker result error_code must be a safe machine-readable value")
    return result.job_id, result.error_code
