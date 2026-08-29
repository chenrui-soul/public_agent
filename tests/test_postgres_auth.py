from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete

from public_agent.api.app import create_app
from public_agent.auth import (
    APITokenCodec,
    AuthenticationError,
    PrincipalCreateRequest,
    PrincipalStatus,
)
from public_agent.config import Settings
from public_agent.core.types import utc_now
from public_agent.knowledge import DeterministicHashEmbeddingProvider
from public_agent.storage.auth import PostgresAPIKeyService
from public_agent.storage.database import Database
from public_agent.storage.knowledge import PostgresKnowledgeRepository
from public_agent.storage.knowledge_management import PostgresKnowledgeManagementService
from public_agent.storage.models import AgentModel, APITokenModel, TenantModel

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_postgres_api_tokens_are_hashed_scoped_revocable_and_disableable() -> None:
    database = Database(Settings().database_url)
    tenant_uuid = uuid4()
    other_uuid = uuid4()
    tenant_slug = f"auth-tenant-{tenant_uuid.hex[:10]}"
    other_slug = f"auth-other-{other_uuid.hex[:10]}"
    agent_key = f"auth-agent-{uuid4().hex[:10]}"
    codec = APITokenCodec(SecretStr("postgres-auth-test-pepper"))
    service = PostgresAPIKeyService(database.sessions, codec=codec)
    request = PrincipalCreateRequest(
        tenant_id=tenant_slug,
        subject="knowledge-ingestor",
        display_name="Knowledge Ingestor",
        permissions=("knowledge:read", "knowledge:write"),
        agent_ids=(agent_key,),
    )

    try:
        async with database.sessions() as session, session.begin():
            session.add_all(
                [
                    TenantModel(id=tenant_uuid, slug=tenant_slug, name="Auth Tenant"),
                    TenantModel(id=other_uuid, slug=other_slug, name="Other Tenant"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    AgentModel(
                        id=uuid4(),
                        tenant_id=tenant_uuid,
                        agent_key=agent_key,
                        name="Auth Agent",
                        domain_id=agent_key,
                    ),
                    AgentModel(
                        id=uuid4(),
                        tenant_id=other_uuid,
                        agent_key=agent_key,
                        name="Other Auth Agent",
                        domain_id=agent_key,
                    ),
                ]
            )

        first, replayed = await asyncio.gather(
            service.create_principal(request),
            service.create_principal(request),
        )
        assert replayed.id == first.id
        with pytest.raises(ValueError, match="different configuration"):
            await service.create_principal(
                request.model_copy(update={"permissions": ("knowledge:read",)})
            )
        with pytest.raises(KeyError, match="requested tenant"):
            await service.create_principal(
                request.model_copy(update={"agent_ids": ("missing-agent",)})
            )

        issued = await service.issue_token(
            principal_id=first.id,
            tenant_id=tenant_slug,
            label="integration-token",
            expires_at=utc_now() + timedelta(days=30),
        )
        plaintext = issued.token.get_secret_value()
        authenticated = await service.authenticate(plaintext)
        assert authenticated.principal_id == first.id
        assert authenticated.token_id == issued.id
        assert authenticated.tenant_id == tenant_slug
        assert authenticated.allowed_agent_ids == frozenset({agent_key})
        assert authenticated.permissions == frozenset(
            {"knowledge:read", "knowledge:write"}
        )

        async with database.sessions() as session:
            token_row = await session.get(APITokenModel, issued.id)
            assert token_row is not None
            assert token_row.prefix in plaintext
            assert token_row.secret_digest != plaintext.encode()
            assert len(token_row.secret_digest) == 32
            assert token_row.last_used_at is not None
            assert not hasattr(token_row, "token")
            assert not hasattr(token_row, "secret")

        modified = plaintext[:-1] + ("A" if plaintext[-1] != "A" else "B")
        with pytest.raises(AuthenticationError, match="authentication required"):
            await service.authenticate(modified)
        with pytest.raises(KeyError, match="requested tenant"):
            await service.issue_token(
                principal_id=first.id,
                tenant_id=other_slug,
                label="cross-tenant",
            )

        assert await service.revoke_token(token_id=issued.id, tenant_id=tenant_slug)
        assert not await service.revoke_token(token_id=issued.id, tenant_id=tenant_slug)
        with pytest.raises(AuthenticationError, match="authentication required"):
            await service.authenticate(plaintext)

        second = await service.issue_token(
            principal_id=first.id,
            tenant_id=tenant_slug,
            label="disabled-principal-token",
        )
        disabled = await service.set_principal_status(
            principal_id=first.id,
            tenant_id=tenant_slug,
            status=PrincipalStatus.DISABLED,
        )
        assert disabled.status is PrincipalStatus.DISABLED
        with pytest.raises(AuthenticationError, match="authentication required"):
            await service.authenticate(second.token.get_secret_value())
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(TenantModel).where(TenantModel.id.in_((tenant_uuid, other_uuid)))
            )
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_api_token_expiry_all_agents_and_capacity_limit() -> None:
    database = Database(Settings().database_url)
    tenant_uuid = uuid4()
    tenant_slug = f"auth-admin-{tenant_uuid.hex[:10]}"
    service = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("capacity-test-pepper"),
        max_active_tokens=1,
        last_used_write_interval_seconds=0,
    )

    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_uuid, slug=tenant_slug, name="Admin Tenant"))
        principal = await service.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="tenant-admin",
                display_name="Tenant Admin",
                permissions=("knowledge:read",),
                all_agents=True,
            )
        )
        issued = await service.issue_token(
            principal_id=principal.id,
            tenant_id=tenant_slug,
            label="admin-token",
            expires_at=utc_now() + timedelta(days=1),
        )
        authenticated = await service.authenticate(issued.token.get_secret_value())
        assert authenticated.all_agents is True
        assert authenticated.allowed_agent_ids == frozenset()
        with pytest.raises(ValueError, match="active token limit"):
            await service.issue_token(
                principal_id=principal.id,
                tenant_id=tenant_slug,
                label="too-many",
            )

        assert issued.expires_at is not None
        with patch(
            "public_agent.storage.auth.utc_now",
            return_value=issued.expires_at + timedelta(seconds=1),
        ):
            with pytest.raises(AuthenticationError, match="authentication required"):
                await service.authenticate(issued.token.get_secret_value())
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_bearer_authentication_protects_real_knowledge_api() -> None:
    database = Database(Settings().database_url)
    tenant_uuid = uuid4()
    tenant_slug = f"bearer-tenant-{tenant_uuid.hex[:10]}"
    agent_key = f"bearer-agent-{uuid4().hex[:10]}"
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("bearer-api-test-pepper"),
    )
    embeddings = DeterministicHashEmbeddingProvider()
    repository = PostgresKnowledgeRepository(database.sessions, embeddings)
    knowledge = PostgresKnowledgeManagementService(
        sessions=database.sessions,
        writer=repository,
        embeddings=embeddings,
    )

    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_uuid, slug=tenant_slug, name="Bearer Tenant"))
            await session.flush()
            session.add(
                AgentModel(
                    id=uuid4(),
                    tenant_id=tenant_uuid,
                    agent_key=agent_key,
                    name="Bearer Agent",
                    domain_id=agent_key,
                )
            )
        writer = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="knowledge-writer",
                display_name="Knowledge Writer",
                permissions=("knowledge:read", "knowledge:write"),
                agent_ids=(agent_key,),
            )
        )
        writer_token = await auth.issue_token(
            principal_id=writer.id,
            tenant_id=tenant_slug,
            label="writer-token",
        )
        reader = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="knowledge-reader",
                display_name="Knowledge Reader",
                permissions=("knowledge:read",),
                agent_ids=(agent_key,),
            )
        )
        reader_token = await auth.issue_token(
            principal_id=reader.id,
            tenant_id=tenant_slug,
            label="reader-token",
        )
        app = create_app(database=database, knowledge=knowledge, api_keys=auth)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            request = {
                "headers": {
                    "Idempotency-Key": "bearer-ingestion",
                    "X-Tenant-Id": "attacker-tenant",
                },
                "data": {
                    "agent_id": agent_key,
                    "domain_id": agent_key,
                    "namespace": "manuals",
                    "source_key": "bearer-guide",
                },
                "files": {"file": ("guide.txt", b"Bearer protected content", "text/plain")},
            }
            missing = await client.post("/v1/knowledge/ingestions", **request)
            assert missing.status_code == 401
            assert missing.json()["error"]["code"] == "authentication_required"

            invalid_request = {**request, "headers": dict(request["headers"])}
            invalid_request["headers"]["Authorization"] = "Bearer invalid"
            invalid = await client.post("/v1/knowledge/ingestions", **invalid_request)
            assert invalid.status_code == 401
            assert invalid.json() == missing.json()

            read_only_request = {**request, "headers": dict(request["headers"])}
            read_only_request["headers"]["Authorization"] = (
                f"Bearer {reader_token.token.get_secret_value()}"
            )
            forbidden = await client.post(
                "/v1/knowledge/ingestions",
                **read_only_request,
            )
            assert forbidden.status_code == 403
            assert forbidden.json()["error"]["code"] == "knowledge_forbidden"

            authorized_request = {**request, "headers": dict(request["headers"])}
            authorized_request["headers"]["Authorization"] = (
                f"Bearer {writer_token.token.get_secret_value()}"
            )
            created = await client.post(
                "/v1/knowledge/ingestions",
                **authorized_request,
            )
            assert created.status_code == 202
            assert created.json()["tenant_id"] == tenant_slug
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()
