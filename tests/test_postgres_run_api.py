from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from public_agent.api.app import create_app
from public_agent.api.runs import RunPrincipal
from public_agent.application import AgentRunManagementService, PersistentAgentService
from public_agent.auth import APITokenCodec, PrincipalCreateRequest
from public_agent.config import Settings
from public_agent.core.types import ModelResponse, ToolCall, ToolDefinition, ToolRisk
from public_agent.domains.models import (
    DomainAssetDeclaration,
    DomainAssetType,
    DomainPackage,
)
from public_agent.factory import ActiveAgentAssembler
from public_agent.providers.testing import ScriptedModelProvider
from public_agent.storage.auth import PostgresAPIKeyService
from public_agent.storage.database import Database
from public_agent.storage.domain_packages import PostgresDomainPackagePublisher
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    DomainPackageAssetModel,
    DomainPackageVersionModel,
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


async def _create_active_scope(
    database: Database,
    *,
    prefix: str,
    allowed_tools: tuple[str, ...] = (),
) -> tuple[UUID, str, str]:
    tenant_id = uuid4()
    agent_id = uuid4()
    version_id = uuid4()
    package_version_id = uuid4()
    tenant_slug = f"{prefix}-tenant-{tenant_id.hex[:8]}"
    agent_key = f"{prefix}-agent-{agent_id.hex[:8]}"
    domain_id = f"{prefix}-domain-{agent_id.hex[:8]}"
    instructions = "Use the active production domain package exactly as published."
    package = DomainPackage(
        id=domain_id,
        name=f"{prefix} Domain Agent",
        version="1.0.0",
        description="Run API integration package.",
        instructions=instructions,
        memory_namespace=f"{prefix}-memory",
        allowed_tools=allowed_tools,
        max_steps=4,
        assets=(
            DomainAssetDeclaration(
                asset_type=DomainAssetType.SKILL,
                key="runtime",
                path="skills/runtime.yaml",
                media_type="application/yaml",
            ),
        ),
    )
    manifest = package.model_dump(mode="json")
    assets = [
        {
            "asset_type": "instructions",
            "key": "instructions",
            "relative_path": "instructions.md",
            "media_type": "text/markdown",
            "content_hash": "b" * 64,
            "size_bytes": len(instructions.encode("utf-8")),
        },
        {
            "asset_type": "skill",
            "key": "runtime",
            "relative_path": "skills/runtime.yaml",
            "media_type": "application/yaml",
            "content_hash": "c" * 64,
            "size_bytes": len(b"steps:\n  - answer\n"),
        },
    ]
    async with database.sessions() as session, session.begin():
        tenant = TenantModel(id=tenant_id, slug=tenant_slug, name=f"{prefix} Tenant")
        agent = AgentModel(
            id=agent_id,
            tenant_id=tenant_id,
            agent_key=agent_key,
            name=package.name,
            domain_id=domain_id,
        )
        session.add(tenant)
        await session.flush()
        session.add(agent)
        await session.flush()
        version = AgentVersionModel(
            id=version_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            version=package.version,
            instructions=instructions,
            memory_namespace=package.memory_namespace,
            configuration={
                "domain_package": {
                    "package_version_id": str(package_version_id),
                    "domain_id": domain_id,
                    "version": package.version,
                    "content_hash": "a" * 64,
                    "manifest": manifest,
                    "assets": assets,
                }
            },
        )
        session.add(version)
        await session.flush()
        session.add(
            DomainPackageVersionModel(
                id=package_version_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                domain_id=domain_id,
                version=package.version,
                content_hash="a" * 64,
                status="active",
                revision=1,
                manifest=manifest,
                total_size_bytes=sum(asset["size_bytes"] for asset in assets),
                created_by="integration-test",
                agent_version_id=version_id,
            )
        )
        await session.flush()
        session.add_all(
            [
                DomainPackageAssetModel(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    package_version_id=package_version_id,
                    asset_type=asset["asset_type"],
                    asset_key=asset["key"],
                    relative_path=asset["relative_path"],
                    media_type=asset["media_type"],
                    content_hash=asset["content_hash"],
                    size_bytes=asset["size_bytes"],
                    content=(
                        instructions
                        if asset["asset_type"] == "instructions"
                        else "steps:\n  - answer\n"
                    ),
                )
                for asset in assets
            ]
        )
        agent.active_version_id = version_id
    return tenant_id, tenant_slug, agent_key


def _management_service(
    database: Database,
    *,
    model: ScriptedModelProvider,
    tools: ToolRegistry | None = None,
) -> AgentRunManagementService:
    runs = PostgresRunPersistence(database.sessions)
    agents = ActiveAgentAssembler(
        specs=PostgresDomainPackagePublisher(database.sessions),
        model=model,
        tools=tools or ToolRegistry(),
    )
    return AgentRunManagementService(
        executor=PersistentAgentService(runs=runs),
        runs=runs,
        agents=agents,
    )


def _principal(
    *,
    tenant_id: str,
    agent_id: str,
    subject: str = "operator-1",
    permissions: frozenset[str] | None = None,
) -> RunPrincipal:
    return RunPrincipal(
        subject=subject,
        tenant_id=tenant_id,
        allowed_agent_ids=frozenset({agent_id}),
        permissions=permissions
        or frozenset({"runs:read", "runs:write", "approvals:decide"}),
    )


@pytest.mark.asyncio
async def test_postgres_run_api_uses_active_package_and_enforces_idempotent_scope() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key = await _create_active_scope(
        database,
        prefix="run-api",
    )
    other_uuid = uuid4()
    other_slug = f"other-run-tenant-{other_uuid.hex[:8]}"
    model = ScriptedModelProvider([ModelResponse(content="active package answer")])
    service = _management_service(database, model=model)

    async def authenticated_principal() -> RunPrincipal:
        return _principal(tenant_id=tenant_slug, agent_id=agent_key)

    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=other_uuid, slug=other_slug, name="Other Tenant"))

        active_spec = await PostgresDomainPackagePublisher(
            database.sessions
        ).load_active_spec(tenant_id=tenant_slug, agent_id=agent_key)
        assert active_spec.id == agent_key
        assert active_spec.metadata["domain_id"].startswith("run-api-domain-")

        app = create_app(
            database=database,
            runs=service,
            run_principal_dependency=authenticated_principal,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            request = {
                "agent_id": agent_key,
                "task": "answer from the active package",
                "session_id": "run-api-session",
                "metadata": {"request_label": "integration"},
            }
            created = await client.post(
                "/v1/runs",
                headers={
                    "Idempotency-Key": "active-run-1",
                    "X-Tenant-Id": other_slug,
                },
                json=request,
            )
            assert created.status_code == 200
            payload = created.json()
            assert payload["status"] == "succeeded"
            assert payload["output"] == "active package answer"
            assert payload["agent_id"] == agent_key
            assert len(model.requests) == 1
            serialized = created.text
            assert "checkpoint" not in serialized
            assert "provider_state" not in serialized
            assert "resume_token" not in serialized

            replayed = await client.post(
                "/v1/runs",
                headers={"Idempotency-Key": "active-run-1"},
                json=request,
            )
            assert replayed.status_code == 200
            assert replayed.json()["id"] == payload["id"]
            assert len(model.requests) == 1

            conflict = await client.post(
                "/v1/runs",
                headers={"Idempotency-Key": "active-run-1"},
                json={**request, "metadata": {"request_label": "changed"}},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "idempotency_conflict"

        async def other_principal() -> RunPrincipal:
            return _principal(tenant_id=other_slug, agent_id=agent_key)

        other_app = create_app(
            database=database,
            runs=service,
            run_principal_dependency=other_principal,
        )
        async with AsyncClient(
            transport=ASGITransport(app=other_app),
            base_url="http://test",
        ) as other_client:
            hidden = await other_client.get(
                f"/v1/runs/{payload['id']}",
                params={"agent_id": agent_key},
            )
        assert hidden.status_code == 404
        assert hidden.json()["error"]["code"] == "run_not_found"

        async with database.sessions() as session:
            run = await session.get(RunModel, UUID(payload["id"]))
            assert run is not None
            assert run.metadata_json["run_context"] == {
                "tenant_id": tenant_slug,
                "session_id": "run-api-session",
                "user_id": "operator-1",
                "metadata": {"request_label": "integration"},
            }
            assert await session.scalar(
                select(func.count()).select_from(RunModel).where(
                    RunModel.tenant_id == tenant_uuid,
                    RunModel.idempotency_key == "active-run-1",
                )
            ) == 1
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(TenantModel).where(TenantModel.id.in_((tenant_uuid, other_uuid)))
            )
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_bearer_permissions_protect_real_run_api() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key = await _create_active_scope(
        database,
        prefix="run-bearer",
    )
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("run-api-bearer-test-pepper"),
    )
    service = _management_service(
        database,
        model=ScriptedModelProvider([ModelResponse(content="bearer run answer")]),
    )

    try:
        writer = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="run-writer",
                display_name="Run Writer",
                permissions=("runs:read", "runs:write"),
                agent_ids=(agent_key,),
            )
        )
        writer_token = await auth.issue_token(
            principal_id=writer.id,
            tenant_id=tenant_slug,
            label="run-writer-token",
        )
        reader = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="run-reader",
                display_name="Run Reader",
                permissions=("runs:read",),
                agent_ids=(agent_key,),
            )
        )
        reader_token = await auth.issue_token(
            principal_id=reader.id,
            tenant_id=tenant_slug,
            label="run-reader-token",
        )
        app = create_app(database=database, runs=service, api_keys=auth)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            request = {
                "headers": {"Idempotency-Key": "bearer-run-1"},
                "json": {"agent_id": agent_key, "task": "run with bearer auth"},
            }
            missing = await client.post("/v1/runs", **request)
            assert missing.status_code == 401
            assert missing.json()["error"]["code"] == "authentication_required"

            reader_request = {**request, "headers": dict(request["headers"])}
            reader_request["headers"]["Authorization"] = (
                f"Bearer {reader_token.token.get_secret_value()}"
            )
            forbidden = await client.post("/v1/runs", **reader_request)
            assert forbidden.status_code == 403
            assert forbidden.json()["error"]["code"] == "run_forbidden"

            writer_request = {**request, "headers": dict(request["headers"])}
            writer_request["headers"]["Authorization"] = (
                f"Bearer {writer_token.token.get_secret_value()}"
            )
            created = await client.post("/v1/runs", **writer_request)
            assert created.status_code == 200
            assert created.json()["status"] == "succeeded"

            fetched = await client.get(
                f"/v1/runs/{created.json()['id']}",
                headers={
                    "Authorization": f"Bearer {reader_token.token.get_secret_value()}"
                },
                params={"agent_id": agent_key},
            )
            assert fetched.status_code == 200
            assert fetched.json()["output"] == "bearer run answer"
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_approval_api_approves_rejects_and_cancels_exact_calls() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key = await _create_active_scope(
        database,
        prefix="approval-api",
        allowed_tools=("approved_write",),
    )
    calls: list[ToolContext] = []

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
            ),
            approved_write,
        )
    )
    model = ScriptedModelProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        id="approved-call",
                        name="approved_write",
                        arguments={"value": "approved-secret-argument"},
                    ),
                )
            ),
            ModelResponse(content="approved run completed"),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        id="rejected-call",
                        name="approved_write",
                        arguments={"value": "rejected"},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        id="canceled-call",
                        name="approved_write",
                        arguments={"value": "canceled"},
                    ),
                )
            ),
        ]
    )
    service = _management_service(database, model=model, tools=tools)

    async def authenticated_principal() -> RunPrincipal:
        return _principal(tenant_id=tenant_slug, agent_id=agent_key)

    app = create_app(
        database=database,
        runs=service,
        run_principal_dependency=authenticated_principal,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            approved_waiting = await client.post(
                "/v1/runs",
                headers={"Idempotency-Key": "approval-run-1"},
                json={"agent_id": agent_key, "task": "perform approved write"},
            )
            assert approved_waiting.status_code == 200
            approved_payload = approved_waiting.json()
            assert approved_payload["status"] == "waiting_approval"
            approval = approved_payload["pending_approval"]
            assert approval["tool_call_id"] == "approved-call"
            assert "approved-secret-argument" not in approved_waiting.text
            approval_id = approval["id"]
            run_id = approved_payload["id"]
            assert calls == []

            fetched = await client.get(
                f"/v1/approvals/{approval_id}",
                params={"agent_id": agent_key},
            )
            assert fetched.status_code == 200
            assert "arguments" not in fetched.text

            decided = await client.post(
                f"/v1/approvals/{approval_id}/decide",
                json={
                    "agent_id": agent_key,
                    "decision": "approved",
                    "note": "Approved after review.",
                    "lease_seconds": 60,
                },
            )
            assert decided.status_code == 200
            assert decided.json()["status"] == "succeeded"
            assert decided.json()["output"] == "approved run completed"
            assert len(calls) == 1
            assert calls[0].idempotency_key == f"{run_id}:approved-call"

            replayed = await client.post(
                f"/v1/approvals/{approval_id}/decide",
                json={
                    "agent_id": agent_key,
                    "decision": "approved",
                    "note": "Approved after review.",
                    "lease_seconds": 60,
                },
            )
            assert replayed.status_code == 200
            assert replayed.json()["id"] == run_id
            assert len(calls) == 1

            changed = await client.post(
                f"/v1/approvals/{approval_id}/decide",
                json={
                    "agent_id": agent_key,
                    "decision": "rejected",
                    "note": "Approved after review.",
                },
            )
            assert changed.status_code == 409
            assert changed.json()["error"]["code"] == "approval_state_conflict"

            rejected_waiting = await client.post(
                "/v1/runs",
                headers={"Idempotency-Key": "approval-run-2"},
                json={"agent_id": agent_key, "task": "reject this write"},
            )
            rejected_id = rejected_waiting.json()["pending_approval"]["id"]
            rejected = await client.post(
                f"/v1/approvals/{rejected_id}/decide",
                json={
                    "agent_id": agent_key,
                    "decision": "rejected",
                    "note": "Risk is too high.",
                },
            )
            assert rejected.status_code == 200
            assert rejected.json()["status"] == "canceled"
            assert len(calls) == 1
            repeated_rejection = await client.post(
                f"/v1/approvals/{rejected_id}/decide",
                json={
                    "agent_id": agent_key,
                    "decision": "rejected",
                    "note": "Risk is too high.",
                },
            )
            assert repeated_rejection.status_code == 200
            assert repeated_rejection.json()["status"] == "canceled"

            canceled_waiting = await client.post(
                "/v1/runs",
                headers={"Idempotency-Key": "approval-run-3"},
                json={"agent_id": agent_key, "task": "cancel this write"},
            )
            canceled_payload = canceled_waiting.json()
            canceled_approval_id = canceled_payload["pending_approval"]["id"]
            canceled = await client.post(
                f"/v1/runs/{canceled_payload['id']}/cancel",
                json={"agent_id": agent_key, "note": "Operator canceled."},
            )
            assert canceled.status_code == 200
            assert canceled.json()["status"] == "canceled"
            repeated_cancel = await client.post(
                f"/v1/runs/{canceled_payload['id']}/cancel",
                json={"agent_id": agent_key, "note": "Operator canceled."},
            )
            assert repeated_cancel.status_code == 200
            assert repeated_cancel.json()["status"] == "canceled"
            canceled_approval = await client.get(
                f"/v1/approvals/{canceled_approval_id}",
                params={"agent_id": agent_key},
            )
            assert canceled_approval.json()["status"] == "canceled"
            stale_decision = await client.post(
                f"/v1/approvals/{canceled_approval_id}/decide",
                json={"agent_id": agent_key, "decision": "approved"},
            )
            assert stale_decision.status_code == 409
            assert len(calls) == 1
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()
