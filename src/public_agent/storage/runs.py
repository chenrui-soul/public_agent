from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.application import (
    ApprovalRecord,
    RunCanceledError,
    RunHandle,
    RunPersistence,
    RunRecord,
    RunResumeHandle,
)
from public_agent.core.events import EventSink, RunEvent
from public_agent.core.trace import RunTrace, RunTraceEvent
from public_agent.core.types import (
    AgentSpec,
    ApprovalDecision,
    ApprovalRequest,
    RunCheckpoint,
    RunContext,
    RunResult,
    RunStatus,
    utc_now,
)
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    ApprovalModel,
    RunEventModel,
    RunModel,
    TenantModel,
)
from public_agent.storage.outbox import (
    DEFAULT_REFLECTION_HANDLER_VERSION,
    enqueue_reflection_job,
)


class PostgresRunEventSink(EventSink):
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        tenant_id: UUID,
        run_id: UUID,
    ) -> None:
        self._sessions = sessions
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._sequence: int | None = None
        self._lock = asyncio.Lock()

    async def append(self, event: RunEvent) -> None:
        if event.run_id != self._run_id:
            raise ValueError("Event run id does not match the scoped event sink")
        async with self._lock, self._sessions() as session, session.begin():
            if self._sequence is None:
                current = await session.scalar(
                    select(func.coalesce(func.max(RunEventModel.sequence), 0)).where(
                        RunEventModel.run_id == self._run_id
                    )
                )
                self._sequence = int(current or 0)
            self._sequence += 1
            session.add(
                RunEventModel(
                    id=event.id,
                    tenant_id=self._tenant_id,
                    run_id=self._run_id,
                    sequence=self._sequence,
                    event_type=event.event_type,
                    payload=event.model_dump(mode="json")["payload"],
                    created_at=event.created_at,
                )
            )


class PostgresRunPersistence(RunPersistence):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        enqueue_reflection_jobs: bool = True,
        reflection_handler_version: str = DEFAULT_REFLECTION_HANDLER_VERSION,
        reflection_max_attempts: int = 5,
    ) -> None:
        if not reflection_handler_version.strip():
            raise ValueError("reflection_handler_version must not be blank")
        if not 1 <= reflection_max_attempts <= 100:
            raise ValueError("reflection_max_attempts must be between 1 and 100")
        self._sessions = sessions
        self._enqueue_reflection_jobs = enqueue_reflection_jobs
        self._reflection_handler_version = reflection_handler_version.strip()
        self._reflection_max_attempts = reflection_max_attempts

    async def start(
        self,
        *,
        run_id: UUID,
        agent: AgentSpec,
        context: RunContext,
        task: str,
        idempotency_key: str | None,
    ) -> RunHandle:
        async with self._sessions() as session, session.begin():
            tenant = await session.scalar(
                select(TenantModel).where(TenantModel.slug == context.tenant_id)
            )
            if tenant is None:
                raise KeyError(f"Unknown tenant: {context.tenant_id}")
            agent_row = await session.scalar(
                select(AgentModel).where(
                    AgentModel.tenant_id == tenant.id,
                    AgentModel.agent_key == agent.id,
                )
            )
            if agent_row is None:
                raise KeyError(f"Unknown agent for tenant {context.tenant_id}: {agent.id}")
            version = await session.scalar(
                select(AgentVersionModel).where(
                    AgentVersionModel.agent_id == agent_row.id,
                    AgentVersionModel.version == agent.version,
                )
            )
            if version is None:
                raise KeyError(f"Unknown agent version: {agent.id}@{agent.version}")
            metadata_json = {"run_context": context.model_dump(mode="json")}
            replayed_result: RunResult | None = None
            if idempotency_key is None:
                session.add(
                    RunModel(
                        id=run_id,
                        tenant_id=tenant.id,
                        agent_id=agent_row.id,
                        agent_version_id=version.id,
                        status="running",
                        task=task,
                        metadata_json=metadata_json,
                    )
                )
            else:
                statement = (
                    postgres_insert(RunModel)
                    .values(
                        id=run_id,
                        tenant_id=tenant.id,
                        agent_id=agent_row.id,
                        agent_version_id=version.id,
                        status="running",
                        task=task,
                        idempotency_key=idempotency_key,
                        metadata_json=metadata_json,
                    )
                    .on_conflict_do_nothing(constraint="uq_runs_tenant_idempotency")
                    .returning(RunModel.id)
                )
                inserted_id = await session.scalar(statement)
                if inserted_id is None:
                    existing = await session.scalar(
                        select(RunModel).where(
                            RunModel.tenant_id == tenant.id,
                            RunModel.idempotency_key == idempotency_key,
                        )
                    )
                    if existing is None:
                        raise RuntimeError("Idempotent run conflict could not be resolved")
                    if (
                        existing.agent_id != agent_row.id
                        or existing.agent_version_id != version.id
                        or existing.task != task
                        or existing.metadata_json.get("run_context")
                        != metadata_json["run_context"]
                    ):
                        raise ValueError(
                            "Idempotency key is already bound to a different run request"
                        )
                    run_id = existing.id
                    replayed_result = _result_from_row(existing)
                    await self._enqueue_terminal_reflection(session, existing)
            tenant_id = tenant.id

        return RunHandle(
            run_id=run_id,
            events=PostgresRunEventSink(
                sessions=self._sessions,
                tenant_id=tenant_id,
                run_id=run_id,
            ),
            replayed_result=replayed_result,
        )

    async def prepare_resume(
        self,
        *,
        run_id: UUID,
        approval_id: UUID,
        agent: AgentSpec,
        tenant_id: str,
        decision: ApprovalDecision,
        decided_by: str,
        decision_note: str | None,
        lease_seconds: int,
    ) -> RunResumeHandle:
        actor = decided_by.strip()
        if not actor or len(actor) > 200:
            raise ValueError("decided_by must contain 1 to 200 characters")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        async with self._sessions() as session, session.begin():
            tenant = await session.scalar(
                select(TenantModel).where(TenantModel.slug == tenant_id)
            )
            if tenant is None:
                raise KeyError(f"Unknown tenant: {tenant_id}")
            agent_row = await session.scalar(
                select(AgentModel).where(
                    AgentModel.tenant_id == tenant.id,
                    AgentModel.agent_key == agent.id,
                )
            )
            if agent_row is None:
                raise KeyError(f"Unknown agent for tenant {tenant_id}: {agent.id}")
            row = await session.scalar(
                select(RunModel)
                .where(
                    RunModel.id == run_id,
                    RunModel.tenant_id == tenant.id,
                    RunModel.agent_id == agent_row.id,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError("Unknown run in requested tenant and agent scope")
            agent_version = await session.scalar(
                select(AgentVersionModel).where(
                    AgentVersionModel.id == row.agent_version_id,
                    AgentVersionModel.tenant_id == tenant.id,
                    AgentVersionModel.agent_id == agent_row.id,
                    AgentVersionModel.version == agent.version,
                )
            )
            if agent_version is None:
                raise ValueError("Run agent version does not match the requested agent")
            approval = await session.scalar(
                select(ApprovalModel)
                .where(
                    ApprovalModel.id == approval_id,
                    ApprovalModel.tenant_id == tenant.id,
                    ApprovalModel.run_id == row.id,
                )
                .with_for_update()
            )
            if approval is None:
                raise KeyError("Unknown approval in requested run scope")
            target_status = decision.value
            first_decision = approval.status == "pending"
            if not first_decision and (
                approval.status != target_status
                or approval.decided_by != actor
                or approval.decision_note != decision_note
            ):
                raise ValueError("Approval already has a different decision")

            events = PostgresRunEventSink(
                sessions=self._sessions,
                tenant_id=tenant.id,
                run_id=row.id,
            )
            context = _context_from_row(row, tenant.slug)
            terminal_statuses = {
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELED.value,
                RunStatus.TIMED_OUT.value,
            }
            if not first_decision and row.status in terminal_statuses:
                await self._enqueue_terminal_reflection(session, row)
                return RunResumeHandle(
                    run_id=row.id,
                    task=row.task,
                    context=context,
                    events=events,
                    decision=decision,
                    replayed_result=_result_from_row(row),
                )
            checkpoint = _checkpoint_from_row(row)
            if checkpoint is None or checkpoint.pending_approval.id != approval_id:
                raise ValueError("Run checkpoint does not match the requested approval")
            if first_decision:
                approval.status = target_status
                approval.decided_by = actor
                approval.decision_note = decision_note
                approval.updated_at = utc_now()
                await _append_event_in_transaction(
                    session,
                    row,
                    event_type="approval.decided",
                    payload={
                        "approval_id": str(approval.id),
                        "decision": decision.value,
                        "decided_by": actor,
                    },
                )
            if decision is ApprovalDecision.REJECTED:
                if row.status == RunStatus.WAITING_APPROVAL.value:
                    row.status = RunStatus.CANCELED.value
                    row.output = None
                    row.error = "Approval rejected; pending tool call was not executed"
                    row.resume_token = None
                    row.resume_lease_expires_at = None
                    row.metadata_json = {
                        **row.metadata_json,
                        "checkpoint": None,
                    }
                    result = _result_from_row(row)
                    await self._enqueue_terminal_reflection(session, row)
                else:
                    raise ValueError("Run is not waiting for this approval decision")
                return RunResumeHandle(
                    run_id=row.id,
                    task=row.task,
                    context=context,
                    events=events,
                    decision=decision,
                    replayed_result=result,
                )

            now = utc_now()
            if row.status == RunStatus.RUNNING.value:
                if (
                    row.resume_token is not None
                    and row.resume_lease_expires_at is not None
                    and row.resume_lease_expires_at > now
                ):
                    raise RuntimeError("Run resume is already in progress")
            elif row.status != RunStatus.WAITING_APPROVAL.value:
                raise ValueError("Run is not resumable from approval")

            resume_token = uuid4()
            row.status = RunStatus.RUNNING.value
            row.resume_token = resume_token
            row.resume_lease_expires_at = now + timedelta(seconds=lease_seconds)
            await _append_event_in_transaction(
                session,
                row,
                event_type=("run.resume.claimed" if first_decision else "run.resume.reclaimed"),
                payload={
                    "approval_id": str(approval.id),
                    "lease_seconds": lease_seconds,
                    "lease_expires_at": row.resume_lease_expires_at.isoformat(),
                },
            )
            return RunResumeHandle(
                run_id=row.id,
                task=row.task,
                context=context,
                events=events,
                decision=decision,
                checkpoint=checkpoint,
                resume_token=resume_token,
            )

    async def finish(
        self,
        result: RunResult,
        *,
        resume_token: UUID | None = None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(RunModel).where(RunModel.id == result.run_id).with_for_update()
            )
            if row is None:
                raise KeyError(f"Unknown run: {result.run_id}")
            if row.status == RunStatus.CANCELED.value:
                raise RunCanceledError("Canceled run rejected a stale runtime result")
            if row.resume_token is None:
                if resume_token is not None:
                    raise ValueError("Run has no active resume ownership")
            elif resume_token != row.resume_token:
                raise ValueError("Resume token is stale or does not own this run")
            elif (
                row.resume_lease_expires_at is None
                or row.resume_lease_expires_at <= utc_now()
            ):
                raise ValueError("Resume lease expired before the run could finish")
            if result.status is RunStatus.WAITING_APPROVAL:
                checkpoint = result.checkpoint
                if checkpoint is None or checkpoint.run_id != row.id:
                    raise ValueError("Waiting run must contain a matching checkpoint")
                approval = checkpoint.pending_approval
                inserted_id = await session.scalar(
                    postgres_insert(ApprovalModel)
                    .values(
                        id=approval.id,
                        tenant_id=row.tenant_id,
                        run_id=row.id,
                        status="pending",
                        reason=approval.reason,
                        requested_payload={
                            "approval": approval.model_dump(mode="json"),
                            "checkpoint": checkpoint.model_dump(mode="json"),
                        },
                    )
                    .on_conflict_do_nothing(index_elements=[ApprovalModel.id])
                    .returning(ApprovalModel.id)
                )
                if inserted_id is None:
                    existing = await session.get(ApprovalModel, approval.id)
                    if (
                        existing is None
                        or existing.run_id != row.id
                        or existing.status != "pending"
                        or existing.requested_payload.get("checkpoint")
                        != checkpoint.model_dump(mode="json")
                    ):
                        raise ValueError("Approval id is already bound to another request")
                await _append_event_in_transaction(
                    session,
                    row,
                    event_type="approval.requested",
                    payload={
                        "approval_id": str(approval.id),
                        "tool_call_id": approval.tool_call.id,
                        "tool_name": approval.tool_call.name,
                    },
                )
            row.status = result.status.value
            row.output = result.output
            row.error = result.error
            row.metadata_json = {
                **row.metadata_json,
                "steps": result.steps,
                "checkpoint": (
                    result.checkpoint.model_dump(mode="json")
                    if result.checkpoint is not None
                    else None
                ),
            }
            row.resume_token = None
            row.resume_lease_expires_at = None
            await self._enqueue_terminal_reflection(session, row)

    async def get_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> RunRecord:
        async with self._sessions() as session:
            scoped = await _scoped_run(
                session,
                run_id=run_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            if scoped is None:
                raise KeyError("Unknown run in requested tenant and agent scope")
            row, tenant, agent, version = scoped
            return await _run_record(session, row, tenant, agent, version)

    async def cancel_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
        canceled_by: str,
        cancellation_note: str | None,
    ) -> RunRecord:
        actor = canceled_by.strip()
        if not actor or len(actor) > 200:
            raise ValueError("canceled_by must contain 1 to 200 characters")
        if cancellation_note is not None and len(cancellation_note) > 2_000:
            raise ValueError("cancellation_note must be at most 2000 characters")
        async with self._sessions() as session, session.begin():
            scoped = await _scoped_run(
                session,
                run_id=run_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                for_update=True,
            )
            if scoped is None:
                raise KeyError("Unknown run in requested tenant and agent scope")
            row, tenant, agent, version = scoped
            terminal_statuses = {
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELED.value,
                RunStatus.TIMED_OUT.value,
            }
            if row.status not in terminal_statuses:
                now = utc_now()
                pending_approvals = tuple(
                    (
                        await session.scalars(
                            select(ApprovalModel)
                            .where(
                                ApprovalModel.run_id == row.id,
                                ApprovalModel.status == "pending",
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                for approval in pending_approvals:
                    approval.status = "canceled"
                    approval.decided_by = actor
                    approval.decision_note = cancellation_note
                    approval.updated_at = now
                row.status = RunStatus.CANCELED.value
                row.output = None
                row.error = "Run canceled by an authorized operator"
                row.resume_token = None
                row.resume_lease_expires_at = None
                row.metadata_json = {**row.metadata_json, "checkpoint": None}
                row.updated_at = now
                await _append_event_in_transaction(
                    session,
                    row,
                    event_type="run.canceled",
                    payload={"canceled_by": actor},
                )
            await self._enqueue_terminal_reflection(session, row)
            return await _run_record(session, row, tenant, agent, version)

    async def get_approval(
        self,
        *,
        approval_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> ApprovalRecord:
        async with self._sessions() as session:
            statement = (
                select(
                    ApprovalModel,
                    RunModel,
                    TenantModel,
                    AgentModel,
                    AgentVersionModel,
                )
                .join(RunModel, RunModel.id == ApprovalModel.run_id)
                .join(TenantModel, TenantModel.id == RunModel.tenant_id)
                .join(AgentModel, AgentModel.id == RunModel.agent_id)
                .join(AgentVersionModel, AgentVersionModel.id == RunModel.agent_version_id)
                .where(
                    ApprovalModel.id == approval_id,
                    ApprovalModel.tenant_id == TenantModel.id,
                    TenantModel.slug == tenant_id,
                    AgentModel.agent_key == agent_id,
                    AgentVersionModel.tenant_id == TenantModel.id,
                    AgentVersionModel.agent_id == AgentModel.id,
                )
            )
            scoped = (await session.execute(statement)).one_or_none()
            if scoped is None:
                raise KeyError("Unknown approval in requested tenant and agent scope")
            approval, run, tenant, agent, version = scoped._tuple()
            return _approval_record(approval, run, tenant, agent, version)

    async def load_trace(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> RunTrace:
        async with self._sessions() as session:
            statement = (
                select(RunModel, TenantModel, AgentModel, AgentVersionModel)
                .join(TenantModel, TenantModel.id == RunModel.tenant_id)
                .join(AgentModel, AgentModel.id == RunModel.agent_id)
                .join(AgentVersionModel, AgentVersionModel.id == RunModel.agent_version_id)
                .where(
                    RunModel.id == run_id,
                    TenantModel.slug == tenant_id,
                    AgentModel.agent_key == agent_id,
                    AgentVersionModel.tenant_id == TenantModel.id,
                    AgentVersionModel.agent_id == AgentModel.id,
                )
            )
            row = (await session.execute(statement)).one_or_none()
            if row is None:
                raise KeyError(f"Unknown run in requested tenant and agent scope: {run_id}")
            run, tenant, agent, version = row._tuple()
            event_rows = tuple(
                (
                    await session.scalars(
                        select(RunEventModel)
                        .where(
                            RunEventModel.tenant_id == tenant.id,
                            RunEventModel.run_id == run.id,
                        )
                        .order_by(RunEventModel.sequence)
                    )
                ).all()
            )

        return RunTrace(
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

    async def _enqueue_terminal_reflection(
        self,
        session: AsyncSession,
        row: RunModel,
    ) -> None:
        if not self._enqueue_reflection_jobs or row.status not in {
            RunStatus.SUCCEEDED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELED.value,
            RunStatus.TIMED_OUT.value,
        }:
            return
        await enqueue_reflection_job(
            session,
            row,
            handler_version=self._reflection_handler_version,
            max_attempts=self._reflection_max_attempts,
        )


def _result_from_row(row: RunModel) -> RunResult:
    checkpoint = _checkpoint_from_row(row)
    return RunResult(
        run_id=row.id,
        status=RunStatus(row.status),
        output=row.output,
        error=row.error,
        steps=int(row.metadata_json.get("steps", 0)),
        checkpoint=checkpoint,
    )


def _checkpoint_from_row(row: RunModel) -> RunCheckpoint | None:
    checkpoint_payload = row.metadata_json.get("checkpoint")
    return (
        RunCheckpoint.model_validate(checkpoint_payload)
        if isinstance(checkpoint_payload, dict)
        else None
    )


def _context_from_row(row: RunModel, tenant_slug: str) -> RunContext:
    payload = row.metadata_json.get("run_context")
    if not isinstance(payload, dict):
        raise ValueError("Run predates resumable context persistence")
    context = RunContext.model_validate(payload)
    if context.tenant_id != tenant_slug:
        raise ValueError("Persisted run context does not match the tenant scope")
    return context


async def _append_event_in_transaction(
    session: AsyncSession,
    row: RunModel,
    *,
    event_type: str,
    payload: dict[str, object],
) -> None:
    current = await session.scalar(
        select(func.coalesce(func.max(RunEventModel.sequence), 0)).where(
            RunEventModel.run_id == row.id
        )
    )
    session.add(
        RunEventModel(
            id=uuid4(),
            tenant_id=row.tenant_id,
            run_id=row.id,
            sequence=int(current or 0) + 1,
            event_type=event_type,
            payload=payload,
        )
    )


async def _scoped_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    tenant_id: str,
    agent_id: str,
    for_update: bool = False,
) -> tuple[RunModel, TenantModel, AgentModel, AgentVersionModel] | None:
    statement = (
        select(RunModel, TenantModel, AgentModel, AgentVersionModel)
        .join(TenantModel, TenantModel.id == RunModel.tenant_id)
        .join(AgentModel, AgentModel.id == RunModel.agent_id)
        .join(AgentVersionModel, AgentVersionModel.id == RunModel.agent_version_id)
        .where(
            RunModel.id == run_id,
            TenantModel.slug == tenant_id,
            AgentModel.agent_key == agent_id,
            AgentVersionModel.tenant_id == TenantModel.id,
            AgentVersionModel.agent_id == AgentModel.id,
        )
    )
    if for_update:
        statement = statement.with_for_update(of=RunModel)
    scoped = (await session.execute(statement)).one_or_none()
    if scoped is None:
        return None
    return scoped._tuple()


async def _run_record(
    session: AsyncSession,
    row: RunModel,
    tenant: TenantModel,
    agent: AgentModel,
    version: AgentVersionModel,
) -> RunRecord:
    pending: ApprovalRecord | None = None
    if row.status == RunStatus.WAITING_APPROVAL.value:
        approval = await session.scalar(
            select(ApprovalModel)
            .where(
                ApprovalModel.run_id == row.id,
                ApprovalModel.status == "pending",
            )
            .order_by(ApprovalModel.created_at.desc(), ApprovalModel.id.desc())
            .limit(1)
        )
        if approval is not None:
            pending = _approval_record(approval, row, tenant, agent, version)
    return RunRecord(
        id=row.id,
        tenant_id=tenant.slug,
        agent_id=agent.agent_key,
        agent_version=version.version,
        status=RunStatus(row.status),
        output=row.output,
        error=row.error,
        steps=int(row.metadata_json.get("steps", 0)),
        pending_approval=pending,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _approval_record(
    approval: ApprovalModel,
    run: RunModel,
    tenant: TenantModel,
    agent: AgentModel,
    version: AgentVersionModel,
) -> ApprovalRecord:
    payload = approval.requested_payload.get("approval")
    if not isinstance(payload, dict):
        raise ValueError("Approval payload is missing its immutable request")
    request = ApprovalRequest.model_validate(payload)
    if request.id != approval.id or request.run_id != run.id:
        raise ValueError("Approval payload does not match its persisted scope")
    return ApprovalRecord(
        id=approval.id,
        run_id=run.id,
        tenant_id=tenant.slug,
        agent_id=agent.agent_key,
        agent_version=version.version,
        status=approval.status,
        reason=approval.reason,
        tool_call_id=request.tool_call.id,
        tool_name=request.tool_call.name,
        tool_version=request.tool_version,
        decided_by=approval.decided_by,
        decision_note=approval.decision_note,
        created_at=approval.created_at,
        updated_at=approval.updated_at,
    )
