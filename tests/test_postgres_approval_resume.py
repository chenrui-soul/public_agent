from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from public_agent.application import PersistentAgentService, RunCanceledError
from public_agent.config import Settings
from public_agent.core.runtime import AgentRuntime
from public_agent.core.types import (
    AgentSpec,
    ApprovalDecision,
    ModelResponse,
    RunContext,
    RunResult,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolRisk,
    utc_now,
)
from public_agent.factory import Agent
from public_agent.providers.testing import ScriptedModelProvider
from public_agent.storage.database import Database
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    ApprovalModel,
    RunEventModel,
    RunModel,
    TenantModel,
)
from public_agent.storage.runs import PostgresRunPersistence
from public_agent.tools.base import FunctionTool, ToolContext
from public_agent.tools.registry import ToolRegistry

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


async def _create_scope(
    database: Database,
    *,
    prefix: str,
    agent_key: str | None = None,
) -> tuple[UUID, str, AgentSpec]:
    tenant_uuid = uuid4()
    agent_uuid = uuid4()
    version_uuid = uuid4()
    tenant_slug = f"{prefix}-tenant-{tenant_uuid.hex[:10]}"
    resolved_agent_key = agent_key or f"{prefix}-agent-{agent_uuid.hex[:10]}"
    spec = AgentSpec(
        id=resolved_agent_key,
        name=f"{prefix} Agent",
        version="1.0.0",
        instructions="Execute approved writes exactly once.",
        memory_namespace=f"{prefix}-memory",
        allowed_tools=("approved_write",),
        max_steps=4,
    )
    async with database.sessions() as session, session.begin():
        session.add(TenantModel(id=tenant_uuid, slug=tenant_slug, name=f"{prefix} Tenant"))
        await session.flush()
        session.add(
            AgentModel(
                id=agent_uuid,
                tenant_id=tenant_uuid,
                agent_key=resolved_agent_key,
                name=spec.name,
                domain_id=resolved_agent_key,
            )
        )
        await session.flush()
        session.add(
            AgentVersionModel(
                id=version_uuid,
                tenant_id=tenant_uuid,
                agent_id=agent_uuid,
                version=spec.version,
                instructions=spec.instructions,
                memory_namespace=spec.memory_namespace,
                configuration={},
            )
        )
    return tenant_uuid, tenant_slug, spec


def _agent(spec: AgentSpec, calls: list[ToolContext], *, final: str = "done") -> Agent:
    async def approved_write(
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> dict[str, Any]:
        calls.append(context)
        return {"written": arguments["value"]}

    tools = ToolRegistry()
    tools.register(
        FunctionTool(
            ToolDefinition(
                name="approved_write",
                description="Write one approved value",
                risk=ToolRisk.HIGH_RISK_WRITE,
                idempotent=True,
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"written": {"type": "string"}},
                    "required": ["written"],
                    "additionalProperties": False,
                },
            ),
            approved_write,
        )
    )
    return Agent(
        spec=spec,
        runtime=AgentRuntime(
            model=ScriptedModelProvider(
                [
                    ModelResponse(
                        tool_calls=(
                            ToolCall(
                                id=f"write-{uuid4().hex[:8]}",
                                name="approved_write",
                                arguments={"value": "approved"},
                            ),
                        )
                    ),
                    ModelResponse(content=final),
                ]
            ),
            tools=tools,
        ),
    )


@pytest.mark.asyncio
async def test_postgres_approval_resume_and_rejection_are_persistent_and_idempotent() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, spec = await _create_scope(database, prefix="resume")
    calls: list[ToolContext] = []
    agent = _agent(spec, calls)
    service = PersistentAgentService(runs=PostgresRunPersistence(database.sessions))
    context = RunContext(
        tenant_id=tenant_slug,
        session_id="resume-session",
        user_id="requester",
        metadata={"request_scope": "production"},
    )

    try:
        waiting = await service.run(agent=agent, task="perform approved write", context=context)
        assert waiting.result.status is RunStatus.WAITING_APPROVAL
        assert waiting.result.checkpoint is not None
        approval_id = waiting.result.checkpoint.pending_approval.id
        assert calls == []

        async with database.sessions() as session:
            approval = await session.get(ApprovalModel, approval_id)
            run = await session.get(RunModel, waiting.result.run_id)
            assert approval is not None and approval.status == "pending"
            assert run is not None and run.status == RunStatus.WAITING_APPROVAL.value
            assert run.metadata_json["run_context"]["metadata"] == {
                "request_scope": "production"
            }

        resumed = await PersistentAgentService(
            runs=PostgresRunPersistence(database.sessions)
        ).resume(
            run_id=waiting.result.run_id,
            approval_id=approval_id,
            agent=agent,
            tenant_id=tenant_slug,
            decision=ApprovalDecision.APPROVED,
            decided_by="human-reviewer",
            decision_note="Approved after production review.",
        )
        assert resumed.result.status is RunStatus.SUCCEEDED
        assert resumed.result.output == "done"
        assert len(calls) == 1
        assert calls[0].idempotency_key == (
            f"{waiting.result.run_id}:"
            f"{waiting.result.checkpoint.pending_approval.tool_call.id}"
        )

        replayed = await service.resume(
            run_id=waiting.result.run_id,
            approval_id=approval_id,
            agent=agent,
            tenant_id=tenant_slug,
            decision=ApprovalDecision.APPROVED,
            decided_by="human-reviewer",
            decision_note="Approved after production review.",
        )
        assert replayed.result.status is RunStatus.SUCCEEDED
        assert len(calls) == 1
        with pytest.raises(ValueError, match="different decision"):
            await service.resume(
                run_id=waiting.result.run_id,
                approval_id=approval_id,
                agent=agent,
                tenant_id=tenant_slug,
                decision=ApprovalDecision.REJECTED,
                decided_by="human-reviewer",
                decision_note="Approved after production review.",
            )

        reject_calls: list[ToolContext] = []
        reject_agent = _agent(spec, reject_calls, final="must not run")
        rejected_waiting = await service.run(
            agent=reject_agent,
            task="reject this write",
            context=context,
        )
        assert rejected_waiting.result.checkpoint is not None
        rejected_approval_id = rejected_waiting.result.checkpoint.pending_approval.id
        rejected = await service.resume(
            run_id=rejected_waiting.result.run_id,
            approval_id=rejected_approval_id,
            agent=reject_agent,
            tenant_id=tenant_slug,
            decision=ApprovalDecision.REJECTED,
            decided_by="human-reviewer",
            decision_note="Risk is too high.",
        )
        assert rejected.result.status is RunStatus.CANCELED
        assert reject_calls == []
        repeated_rejection = await service.resume(
            run_id=rejected_waiting.result.run_id,
            approval_id=rejected_approval_id,
            agent=reject_agent,
            tenant_id=tenant_slug,
            decision=ApprovalDecision.REJECTED,
            decided_by="human-reviewer",
            decision_note="Risk is too high.",
        )
        assert repeated_rejection.result.status is RunStatus.CANCELED

        async with database.sessions() as session:
            events = tuple(
                (
                    await session.scalars(
                        select(RunEventModel)
                        .where(RunEventModel.run_id == waiting.result.run_id)
                        .order_by(RunEventModel.sequence)
                    )
                ).all()
            )
            assert "approval.requested" in {event.event_type for event in events}
            assert "approval.decided" in {event.event_type for event in events}
            assert "run.resumed" in {event.event_type for event in events}
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()


@pytest.mark.asyncio
async def test_resume_lease_reclaim_fences_stale_worker_and_isolates_tenants() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, spec = await _create_scope(database, prefix="lease")
    other_uuid, other_slug, _ = await _create_scope(
        database,
        prefix="other-resume",
        agent_key=spec.id,
    )
    agent = _agent(spec, [])
    service = PersistentAgentService(runs=PostgresRunPersistence(database.sessions))
    runs = PostgresRunPersistence(database.sessions)

    try:
        waiting = await service.run(
            agent=agent,
            task="claim resumable write",
            context=RunContext(tenant_id=tenant_slug),
        )
        assert waiting.result.checkpoint is not None
        approval_id = waiting.result.checkpoint.pending_approval.id
        outcomes = await asyncio.gather(
            *(
                runs.prepare_resume(
                    run_id=waiting.result.run_id,
                    approval_id=approval_id,
                    agent=spec,
                    tenant_id=tenant_slug,
                    decision=ApprovalDecision.APPROVED,
                    decided_by="lease-reviewer",
                    decision_note="Approved.",
                    lease_seconds=300,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        claims = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert len(claims) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], RuntimeError)
        assert "already in progress" in str(failures[0])
        first = claims[0]
        assert first.resume_token is not None
        with pytest.raises(KeyError, match="requested tenant"):
            await runs.prepare_resume(
                run_id=waiting.result.run_id,
                approval_id=approval_id,
                agent=spec,
                tenant_id=other_slug,
                decision=ApprovalDecision.APPROVED,
                decided_by="lease-reviewer",
                decision_note="Approved.",
                lease_seconds=300,
            )

        async with database.sessions() as session, session.begin():
            run = await session.get(RunModel, waiting.result.run_id)
            assert run is not None
            run.resume_lease_expires_at = utc_now() - timedelta(seconds=1)

        with pytest.raises(ValueError, match="lease expired"):
            await runs.finish(
                RunResult(
                    run_id=waiting.result.run_id,
                    status=RunStatus.FAILED,
                    error="expired worker",
                ),
                resume_token=first.resume_token,
            )

        reclaimed = await PostgresRunPersistence(database.sessions).prepare_resume(
            run_id=waiting.result.run_id,
            approval_id=approval_id,
            agent=spec,
            tenant_id=tenant_slug,
            decision=ApprovalDecision.APPROVED,
            decided_by="lease-reviewer",
            decision_note="Approved.",
            lease_seconds=300,
        )
        assert reclaimed.resume_token is not None
        assert reclaimed.resume_token != first.resume_token
        with pytest.raises(ValueError, match="stale"):
            await runs.finish(
                RunResult(
                    run_id=waiting.result.run_id,
                    status=RunStatus.FAILED,
                    error="stale worker",
                ),
                resume_token=first.resume_token,
            )
        await runs.finish(
            RunResult(
                run_id=waiting.result.run_id,
                status=RunStatus.FAILED,
                error="reclaimed worker stopped safely",
            ),
            resume_token=reclaimed.resume_token,
        )
        async with database.sessions() as session:
            run = await session.get(RunModel, waiting.result.run_id)
            assert run is not None
            assert run.status == RunStatus.FAILED.value
            assert run.resume_token is None
            assert run.resume_lease_expires_at is None
            resume_events = tuple(
                (
                    await session.scalars(
                        select(RunEventModel).where(
                            RunEventModel.run_id == waiting.result.run_id,
                            RunEventModel.event_type.in_(
                                ("run.resume.claimed", "run.resume.reclaimed")
                            ),
                        )
                    )
                ).all()
            )
            assert len(resume_events) == 2
            assert all("resume_token" not in event.payload for event in resume_events)
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(TenantModel).where(TenantModel.id.in_((tenant_uuid, other_uuid)))
            )
        await database.dispose()


@pytest.mark.asyncio
async def test_run_management_queries_cancel_and_fences_active_resume_owner() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, spec = await _create_scope(database, prefix="management")
    agent = _agent(spec, [])
    runs = PostgresRunPersistence(database.sessions)
    service = PersistentAgentService(runs=runs)

    try:
        waiting = await service.run(
            agent=agent,
            task="cancel this approved write",
            context=RunContext(tenant_id=tenant_slug, user_id="requester"),
        )
        assert waiting.result.checkpoint is not None
        approval_id = waiting.result.checkpoint.pending_approval.id

        record = await runs.get_run(
            run_id=waiting.result.run_id,
            tenant_id=tenant_slug,
            agent_id=spec.id,
        )
        assert record.status is RunStatus.WAITING_APPROVAL
        assert record.pending_approval is not None
        assert record.pending_approval.id == approval_id
        assert record.pending_approval.tool_name == "approved_write"

        claim = await runs.prepare_resume(
            run_id=waiting.result.run_id,
            approval_id=approval_id,
            agent=spec,
            tenant_id=tenant_slug,
            decision=ApprovalDecision.APPROVED,
            decided_by="reviewer",
            decision_note="Approved.",
            lease_seconds=300,
        )
        assert claim.resume_token is not None

        canceled = await runs.cancel_run(
            run_id=waiting.result.run_id,
            tenant_id=tenant_slug,
            agent_id=spec.id,
            canceled_by="operator",
            cancellation_note="Stop before the tool executes.",
        )
        assert canceled.status is RunStatus.CANCELED
        assert canceled.pending_approval is None
        repeated = await runs.cancel_run(
            run_id=waiting.result.run_id,
            tenant_id=tenant_slug,
            agent_id=spec.id,
            canceled_by="operator",
            cancellation_note="Stop before the tool executes.",
        )
        assert repeated.status is RunStatus.CANCELED

        approval = await runs.get_approval(
            approval_id=approval_id,
            tenant_id=tenant_slug,
            agent_id=spec.id,
        )
        assert approval.status == "approved"
        with pytest.raises(RunCanceledError, match="Canceled run"):
            await runs.finish(
                RunResult(
                    run_id=waiting.result.run_id,
                    status=RunStatus.SUCCEEDED,
                    output="stale success",
                ),
                resume_token=claim.resume_token,
            )

        async with database.sessions() as session:
            row = await session.get(RunModel, waiting.result.run_id)
            assert row is not None
            assert row.status == RunStatus.CANCELED.value
            assert row.resume_token is None
            assert row.metadata_json["checkpoint"] is None
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()
