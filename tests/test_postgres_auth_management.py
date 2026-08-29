from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

from public_agent.api.app import create_app
from public_agent.auth import (
    DEFAULT_MANAGEABLE_PERMISSIONS,
    APITokenCodec,
    AuthStateConflictError,
    PrincipalCreateRequest,
    PrincipalStatus,
)
from public_agent.config import Settings
from public_agent.storage.auth import PostgresAPIKeyService
from public_agent.storage.database import Database
from public_agent.storage.models import (
    AgentModel,
    APIPrincipalModel,
    AuthenticationAuditEventModel,
    TenantModel,
)

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_auth_management_api_enforces_delegation_lifecycle_and_safe_projection() -> None:
    database = Database(Settings().database_url)
    tenant_uuid = uuid4()
    other_uuid = uuid4()
    tenant_slug = f"auth-mgmt-{tenant_uuid.hex[:10]}"
    other_slug = f"auth-other-{other_uuid.hex[:10]}"
    agent_a = f"auth-agent-a-{uuid4().hex[:8]}"
    agent_b = f"auth-agent-b-{uuid4().hex[:8]}"
    service = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("auth-management-api-test-pepper"),
    )

    try:
        async with database.sessions() as session, session.begin():
            session.add_all(
                [
                    TenantModel(id=tenant_uuid, slug=tenant_slug, name="Auth Management"),
                    TenantModel(id=other_uuid, slug=other_slug, name="Auth Other"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    AgentModel(
                        id=uuid4(),
                        tenant_id=tenant_uuid,
                        agent_key=agent_a,
                        name="Agent A",
                        domain_id=agent_a,
                    ),
                    AgentModel(
                        id=uuid4(),
                        tenant_id=tenant_uuid,
                        agent_key=agent_b,
                        name="Agent B",
                        domain_id=agent_b,
                    ),
                ]
            )
        root = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="root-security-admin",
                display_name="Root Security Admin",
                permissions=tuple(sorted(DEFAULT_MANAGEABLE_PERMISSIONS)),
                all_agents=True,
            )
        )
        root_issued = await service.issue_token(
            principal_id=root.id,
            tenant_id=tenant_slug,
            label="root-token",
        )
        root_token = root_issued.token.get_secret_value()
        reader = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="principal-reader",
                display_name="Principal Reader",
                permissions=("auth.principals:read",),
                all_agents=True,
            )
        )
        reader_token = (
            await service.issue_token(
                principal_id=reader.id,
                tenant_id=tenant_slug,
                label="reader-token",
            )
        ).token.get_secret_value()
        delegator = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="limited-delegator",
                display_name="Limited Delegator",
                permissions=("auth.principals:write",),
                all_agents=True,
            )
        )
        delegator_token = (
            await service.issue_token(
                principal_id=delegator.id,
                tenant_id=tenant_slug,
                label="delegator-token",
            )
        ).token.get_secret_value()
        scoped = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="scoped-delegator",
                display_name="Scoped Delegator",
                permissions=("auth.principals:write",),
                agent_ids=(agent_a,),
            )
        )
        scoped_token = (
            await service.issue_token(
                principal_id=scoped.id,
                tenant_id=tenant_slug,
                label="scoped-token",
            )
        ).token.get_secret_value()
        issuer = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="token-issuer",
                display_name="Token Issuer",
                permissions=("auth.tokens:issue",),
                all_agents=True,
            )
        )
        issuer_token = (
            await service.issue_token(
                principal_id=issuer.id,
                tenant_id=tenant_slug,
                label="issuer-token",
            )
        ).token.get_secret_value()
        other = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=other_slug,
                subject="other-principal",
                display_name="Other Principal",
                permissions=("knowledge:read",),
                all_agents=True,
            )
        )

        app = create_app(
            database=database,
            api_keys=service,
            auth_management=service,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            root_headers = {"Authorization": f"Bearer {root_token}"}
            reader_headers = {"Authorization": f"Bearer {reader_token}"}
            delegator_headers = {"Authorization": f"Bearer {delegator_token}"}
            scoped_headers = {"Authorization": f"Bearer {scoped_token}"}
            issuer_headers = {"Authorization": f"Bearer {issuer_token}"}

            missing = await client.get("/v1/auth/principals")
            assert missing.status_code == 401
            listed = await client.get("/v1/auth/principals", headers=reader_headers)
            assert listed.status_code == 200
            forbidden = await client.post(
                "/v1/auth/principals",
                headers=reader_headers,
                json={
                    "subject": "forbidden-create",
                    "display_name": "Forbidden Create",
                    "permissions": ["knowledge:read"],
                    "all_agents": True,
                },
            )
            assert forbidden.status_code == 403
            assert forbidden.json()["error"]["code"] == "auth_management_forbidden"

            self_elevation = await client.post(
                "/v1/auth/principals",
                headers=delegator_headers,
                json={
                    "subject": "elevated-principal",
                    "display_name": "Elevated Principal",
                    "permissions": ["knowledge:write"],
                    "all_agents": True,
                },
            )
            assert self_elevation.status_code == 403
            scope_expansion = await client.post(
                "/v1/auth/principals",
                headers=scoped_headers,
                json={
                    "subject": "expanded-principal",
                    "display_name": "Expanded Principal",
                    "permissions": ["auth.principals:write"],
                    "all_agents": True,
                },
            )
            assert scope_expansion.status_code == 403

            created = await client.post(
                "/v1/auth/principals",
                headers=root_headers,
                json={
                    "subject": "managed-worker",
                    "display_name": "Managed Worker",
                    "permissions": ["knowledge:read"],
                    "agent_ids": [agent_a],
                },
            )
            assert created.status_code == 201
            target_id = created.json()["id"]
            assert created.json()["agent_ids"] == [agent_a]
            cross_tenant = await client.get(
                f"/v1/auth/principals/{other.id}",
                headers=root_headers,
            )
            assert cross_tenant.status_code == 404

            issued = await client.post(
                f"/v1/auth/principals/{target_id}/tokens",
                headers=issuer_headers,
                json={"label": "managed-worker-token"},
            )
            assert issued.status_code == 201
            issued_body = issued.json()
            target_token = issued_body["token"]
            target_token_id = issued_body["id"]
            assert target_token.startswith("public_agent_")
            revoke_forbidden = await client.post(
                f"/v1/auth/tokens/{target_token_id}/revoke",
                headers=issuer_headers,
            )
            assert revoke_forbidden.status_code == 403

            tokens = await client.get(
                f"/v1/auth/principals/{target_id}/tokens",
                headers=root_headers,
            )
            assert tokens.status_code == 200
            token_payload = json.dumps(tokens.json(), sort_keys=True)
            assert "secret_digest" not in token_payload
            assert "authorization" not in token_payload.lower()
            assert target_token not in token_payload
            assert "token" not in tokens.json()["items"][0]

            revoked = await client.post(
                f"/v1/auth/tokens/{target_token_id}/revoke",
                headers=root_headers,
            )
            replayed = await client.post(
                f"/v1/auth/tokens/{target_token_id}/revoke",
                headers=root_headers,
            )
            assert revoked.status_code == replayed.status_code == 200
            assert revoked.json()["revoked_at"] == replayed.json()["revoked_at"]
            invalid_after_revoke = await client.get(
                "/v1/auth/principals",
                headers={"Authorization": f"Bearer {target_token}"},
            )
            assert invalid_after_revoke.status_code == 401

            reissued = await client.post(
                f"/v1/auth/principals/{target_id}/tokens",
                headers=root_headers,
                json={"label": "disable-check"},
            )
            target_token = reissued.json()["token"]
            disabled = await client.post(
                f"/v1/auth/principals/{target_id}/status",
                headers=root_headers,
                json={"status": "disabled"},
            )
            assert disabled.status_code == 200
            assert disabled.json()["status"] == "disabled"
            invalid_after_disable = await client.get(
                "/v1/auth/principals",
                headers={"Authorization": f"Bearer {target_token}"},
            )
            assert invalid_after_disable.status_code == 401

            invalid_cursor = await client.get(
                "/v1/auth/principals?cursor=not-base64!",
                headers=root_headers,
            )
            assert invalid_cursor.status_code == 400
            audit = await client.get("/v1/auth/audit-events", headers=root_headers)
            assert audit.status_code == 200
            audit_payload = json.dumps(audit.json(), sort_keys=True)
            assert root_token not in audit_payload
            assert target_token not in audit_payload
            assert "secret_digest" not in audit_payload
            assert "authorization" not in audit_payload.lower()
            actions = {item["action"] for item in audit.json()["items"]}
            assert "auth.principal.create" in actions
            assert "auth.token.issue" in actions
            assert "auth.token.revoke" in actions
            assert "authentication.authenticate" in actions

        async with database.sessions() as session:
            audit_id = await session.scalar(
                select(AuthenticationAuditEventModel.id).where(
                    AuthenticationAuditEventModel.tenant_id == tenant_uuid
                )
            )
            assert audit_id is not None
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(
                    update(AuthenticationAuditEventModel)
                    .where(AuthenticationAuditEventModel.id == audit_id)
                    .values(action="tampered")
                )
            await session.rollback()
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(TenantModel).where(TenantModel.id.in_((tenant_uuid, other_uuid)))
            )
        await database.dispose()


@pytest.mark.asyncio
async def test_last_security_administrator_guard_is_concurrency_safe() -> None:
    database = Database(Settings().database_url)
    tenant_uuid = uuid4()
    tenant_slug = f"auth-guard-{tenant_uuid.hex[:10]}"
    service = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("auth-last-admin-test-pepper"),
    )
    permissions = (
        "auth.principals:write",
        "auth.tokens:issue",
        "auth.tokens:revoke",
    )

    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_uuid, slug=tenant_slug, name="Auth Guard"))
        first = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="security-admin-a",
                display_name="Security Admin A",
                permissions=permissions,
                all_agents=True,
            )
        )
        second = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="security-admin-b",
                display_name="Security Admin B",
                permissions=permissions,
                all_agents=True,
            )
        )
        first_token = await service.issue_token(
            principal_id=first.id,
            tenant_id=tenant_slug,
            label="security-admin-a-token",
        )
        second_token = await service.issue_token(
            principal_id=second.id,
            tenant_id=tenant_slug,
            label="security-admin-b-token",
        )
        first_actor = await service.authenticate(first_token.token.get_secret_value())
        second_actor = await service.authenticate(second_token.token.get_secret_value())

        outcomes = await asyncio.gather(
            service.set_managed_principal_status(
                principal_id=first.id,
                status=PrincipalStatus.DISABLED,
                actor=first_actor,
            ),
            service.set_managed_principal_status(
                principal_id=second.id,
                status=PrincipalStatus.DISABLED,
                actor=second_actor,
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, AuthStateConflictError) for item in outcomes) == 1
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1

        async with database.sessions() as session:
            statuses = tuple(
                await session.scalars(
                    select(APIPrincipalModel.status)
                    .where(APIPrincipalModel.tenant_id == tenant_uuid)
                    .order_by(APIPrincipalModel.subject)
                )
            )
            audit_outcomes = tuple(
                await session.scalars(
                    select(AuthenticationAuditEventModel.outcome).where(
                        AuthenticationAuditEventModel.tenant_id == tenant_uuid,
                        AuthenticationAuditEventModel.action
                        == "auth.principal.status.set",
                    )
                )
            )
        assert statuses.count("active") == 1
        assert statuses.count("disabled") == 1
        assert "success" in audit_outcomes
        assert "conflict" in audit_outcomes

        active_actor_token = first_token if statuses[0] == "active" else second_token
        active_actor = await service.authenticate(
            active_actor_token.token.get_secret_value()
        )
        with pytest.raises(AuthStateConflictError, match="last usable"):
            await service.revoke_managed_token(
                token_id=active_actor.token_id,
                actor=active_actor,
            )
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()
