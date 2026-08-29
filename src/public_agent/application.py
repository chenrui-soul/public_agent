from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from public_agent.core.events import EventSink, RunEvent
from public_agent.core.trace import RunTrace
from public_agent.core.types import (
    AgentSpec,
    ApprovalDecision,
    RunCheckpoint,
    RunContext,
    RunResult,
    RunStatus,
)
from public_agent.factory import Agent
from public_agent.growth.models import LearningCandidate
from public_agent.growth.pipeline import KnowledgeSedimentationPipeline


@dataclass(frozen=True, slots=True)
class RunHandle:
    run_id: UUID
    events: EventSink
    replayed_result: RunResult | None = None


@dataclass(frozen=True, slots=True)
class RunResumeHandle:
    run_id: UUID
    task: str
    context: RunContext
    events: EventSink
    decision: ApprovalDecision
    checkpoint: RunCheckpoint | None = None
    resume_token: UUID | None = None
    replayed_result: RunResult | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: UUID
    run_id: UUID
    tenant_id: str
    agent_id: str
    agent_version: str
    status: str
    reason: str
    tool_call_id: str
    tool_name: str
    tool_version: str
    decided_by: str | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: UUID
    tenant_id: str
    agent_id: str
    agent_version: str
    status: RunStatus
    output: str | None
    error: str | None
    steps: int
    pending_approval: ApprovalRecord | None
    created_at: datetime
    updated_at: datetime


class RunCanceledError(RuntimeError):
    """Raised when a canceled run fences a stale runtime from persisting results."""


class RunPersistence(Protocol):
    async def start(
        self,
        *,
        run_id: UUID,
        agent: AgentSpec,
        context: RunContext,
        task: str,
        idempotency_key: str | None,
    ) -> RunHandle:
        """Create a running record and return its scoped event sink."""

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
        """Persist an approval decision and claim exclusive resume ownership."""

    async def finish(
        self,
        result: RunResult,
        *,
        resume_token: UUID | None = None,
    ) -> None:
        """Persist the terminal or paused run result."""

    async def load_trace(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> RunTrace:
        """Load the complete ordered run trajectory within its logical scope."""


class RunManagementPersistence(Protocol):
    async def get_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> RunRecord:
        """Load a run without exposing checkpoint or resume ownership state."""

    async def cancel_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
        canceled_by: str,
        cancellation_note: str | None,
    ) -> RunRecord:
        """Idempotently cancel a non-terminal run and fence active resume owners."""

    async def get_approval(
        self,
        *,
        approval_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> ApprovalRecord:
        """Load an approval without returning its checkpoint or tool arguments."""


class ActiveAgentProvider(Protocol):
    async def load(self, *, tenant_id: str, agent_id: str) -> Agent:
        """Build an Agent from the current active, validated domain package."""


@dataclass(frozen=True, slots=True)
class RunExecution:
    result: RunResult
    learning_candidates: tuple[LearningCandidate, ...] = ()
    sedimentation_error: str | None = None


class PersistentAgentService:
    def __init__(
        self,
        *,
        runs: RunPersistence,
        sedimentation: KnowledgeSedimentationPipeline | None = None,
    ) -> None:
        self._runs = runs
        self._sedimentation = sedimentation

    async def run(
        self,
        *,
        agent: Agent,
        task: str,
        context: RunContext,
        idempotency_key: str | None = None,
    ) -> RunExecution:
        run_id = uuid4()
        handle = await self._runs.start(
            run_id=run_id,
            agent=agent.spec,
            context=context,
            task=task,
            idempotency_key=idempotency_key,
        )
        if handle.replayed_result is not None:
            return RunExecution(result=handle.replayed_result)
        try:
            result = await agent.runtime.run(
                agent=agent.spec,
                task=task,
                context=context,
                run_id=handle.run_id,
                event_sink=handle.events,
            )
        except Exception as exc:
            failed = RunResult(
                run_id=handle.run_id,
                status="failed",
                error=f"Unhandled runtime failure: {type(exc).__name__}",
            )
            try:
                await self._runs.finish(failed)
            except RunCanceledError:
                return RunExecution(result=_canceled_result(failed))
            return RunExecution(result=failed)

        try:
            await self._runs.finish(result)
        except RunCanceledError:
            return RunExecution(result=_canceled_result(result))
        if result.status is RunStatus.WAITING_APPROVAL:
            return RunExecution(result=result)

        return await self._sediment(
            agent=agent,
            task=task,
            context=context,
            result=result,
            events=handle.events,
        )

    async def resume(
        self,
        *,
        run_id: UUID,
        approval_id: UUID,
        agent: Agent,
        tenant_id: str,
        decision: ApprovalDecision,
        decided_by: str,
        decision_note: str | None = None,
        lease_seconds: int = 300,
    ) -> RunExecution:
        handle = await self._runs.prepare_resume(
            run_id=run_id,
            approval_id=approval_id,
            agent=agent.spec,
            tenant_id=tenant_id,
            decision=decision,
            decided_by=decided_by,
            decision_note=decision_note,
            lease_seconds=lease_seconds,
        )
        if handle.replayed_result is not None:
            return RunExecution(result=handle.replayed_result)
        if decision is ApprovalDecision.REJECTED:
            raise RuntimeError("Rejected approval must return a persisted canceled result")
        if handle.checkpoint is None or handle.resume_token is None:
            raise RuntimeError("Approved resume claim is missing checkpoint ownership")
        try:
            result = await agent.runtime.resume(
                agent=agent.spec,
                task=handle.task,
                context=handle.context,
                checkpoint=handle.checkpoint,
                event_sink=handle.events,
            )
        except Exception as exc:
            failed = RunResult(
                run_id=handle.run_id,
                status=RunStatus.FAILED,
                error=f"Unhandled runtime resume failure: {type(exc).__name__}",
                steps=handle.checkpoint.step,
            )
            try:
                await self._runs.finish(failed, resume_token=handle.resume_token)
            except RunCanceledError:
                return RunExecution(result=_canceled_result(failed))
            return RunExecution(result=failed)

        try:
            await self._runs.finish(result, resume_token=handle.resume_token)
        except RunCanceledError:
            return RunExecution(result=_canceled_result(result))
        if result.status is RunStatus.WAITING_APPROVAL:
            return RunExecution(result=result)
        return await self._sediment(
            agent=agent,
            task=handle.task,
            context=handle.context,
            result=result,
            events=handle.events,
        )

    async def _sediment(
        self,
        *,
        agent: Agent,
        task: str,
        context: RunContext,
        result: RunResult,
        events: EventSink,
    ) -> RunExecution:
        if self._sedimentation is None:
            return RunExecution(result=result)

        try:
            trace = await self._runs.load_trace(
                run_id=result.run_id,
                tenant_id=context.tenant_id,
                agent_id=agent.spec.id,
            )
            candidates = await self._sedimentation.process_run(
                agent=agent.spec,
                context=context,
                task=task,
                result=result,
                trace=trace,
            )
        except Exception as exc:
            error = f"Knowledge sedimentation failed: {type(exc).__name__}"
            await events.append(
                RunEvent(
                    run_id=result.run_id,
                    event_type="knowledge.reflection.failed",
                    payload={"error": error},
                )
            )
            return RunExecution(result=result, sedimentation_error=error)

        await events.append(
            RunEvent(
                run_id=result.run_id,
                event_type="knowledge.candidates.created",
                payload={
                    "count": len(candidates),
                    "candidate_ids": [str(candidate.id) for candidate in candidates],
                },
            )
        )
        return RunExecution(result=result, learning_candidates=candidates)


class AgentRunManagementService:
    def __init__(
        self,
        *,
        executor: PersistentAgentService,
        runs: RunManagementPersistence,
        agents: ActiveAgentProvider,
    ) -> None:
        self._executor = executor
        self._runs = runs
        self._agents = agents

    async def create_run(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        task: str,
        context: RunContext,
        idempotency_key: str,
    ) -> RunRecord:
        agent = await self._agents.load(tenant_id=tenant_id, agent_id=agent_id)
        execution = await self._executor.run(
            agent=agent,
            task=task,
            context=context,
            idempotency_key=idempotency_key,
        )
        return await self._runs.get_run(
            run_id=execution.result.run_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )

    async def get_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> RunRecord:
        return await self._runs.get_run(
            run_id=run_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )

    async def cancel_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
        canceled_by: str,
        cancellation_note: str | None = None,
    ) -> RunRecord:
        return await self._runs.cancel_run(
            run_id=run_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            canceled_by=canceled_by,
            cancellation_note=cancellation_note,
        )

    async def get_approval(
        self,
        *,
        approval_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> ApprovalRecord:
        return await self._runs.get_approval(
            approval_id=approval_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )

    async def decide_approval(
        self,
        *,
        approval_id: UUID,
        tenant_id: str,
        agent_id: str,
        decision: ApprovalDecision,
        decided_by: str,
        decision_note: str | None = None,
        lease_seconds: int = 300,
    ) -> RunRecord:
        approval = await self._runs.get_approval(
            approval_id=approval_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )
        agent = await self._agents.load(tenant_id=tenant_id, agent_id=agent_id)
        await self._executor.resume(
            run_id=approval.run_id,
            approval_id=approval.id,
            agent=agent,
            tenant_id=tenant_id,
            decision=decision,
            decided_by=decided_by,
            decision_note=decision_note,
            lease_seconds=lease_seconds,
        )
        return await self._runs.get_run(
            run_id=approval.run_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )


def _canceled_result(result: RunResult) -> RunResult:
    return RunResult(
        run_id=result.run_id,
        status=RunStatus.CANCELED,
        error="Run canceled by an authorized operator.",
        steps=result.steps,
    )
