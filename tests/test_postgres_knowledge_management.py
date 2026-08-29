from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from public_agent.api import KnowledgePrincipal
from public_agent.api.app import create_app
from public_agent.config import Settings
from public_agent.core.types import utc_now
from public_agent.knowledge import (
    DeterministicHashEmbeddingProvider,
    DocumentSource,
    KnowledgeFileInput,
    KnowledgeIngestionStatus,
    KnowledgeQuery,
    TextChunker,
)
from public_agent.storage.database import Database
from public_agent.storage.knowledge import PostgresKnowledgeRepository
from public_agent.storage.knowledge_management import PostgresKnowledgeManagementService
from public_agent.storage.models import (
    AgentModel,
    KnowledgeDocumentModel,
    KnowledgeIngestionJobModel,
    TenantModel,
)

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


class _BlockingFirstEmbeddingProvider(DeterministicHashEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.calls = 0

    async def embed_many(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await self.release_first.wait()
        return await super().embed_many(texts)


async def _create_scope(
    database: Database,
    *,
    prefix: str,
    agent_key: str | None = None,
) -> tuple[UUID, str, str]:
    tenant_uuid = uuid4()
    tenant_slug = f"{prefix}-tenant-{tenant_uuid.hex[:10]}"
    resolved_agent_key = agent_key or f"{prefix}-agent-{uuid4().hex[:10]}"
    async with database.sessions() as session, session.begin():
        session.add(TenantModel(id=tenant_uuid, slug=tenant_slug, name=f"{prefix} Tenant"))
        await session.flush()
        session.add(
            AgentModel(
                id=uuid4(),
                tenant_id=tenant_uuid,
                agent_key=resolved_agent_key,
                name=f"{prefix} Agent",
                domain_id=resolved_agent_key,
            )
        )
    return tenant_uuid, tenant_slug, resolved_agent_key


def _request(
    *,
    tenant_id: str,
    agent_id: str,
    content: bytes,
    source_key: str = "operations-guide",
    version: str = "1",
    filename: str = "operations.txt",
    media_type: str = "text/plain",
) -> KnowledgeFileInput:
    return KnowledgeFileInput(
        tenant_id=tenant_id,
        agent_id=agent_id,
        domain_id=agent_id,
        namespace="operations",
        source_key=source_key,
        source=DocumentSource(
            filename=filename,
            media_type=media_type,
            content=content,
        ),
        version=version,
        source_uri=f"https://example.test/{source_key}/{version}",
        access_tags=("operations",),
        metadata={"owner": "knowledge-team"},
    )


async def _finish_ingestion(
    service: PostgresKnowledgeManagementService,
    *,
    job_id: UUID,
    tenant_id: str,
    agent_id: str,
    batch_size: int = 1,
) -> UUID:
    for _ in range(20):
        record = await service.step_ingestion(
            job_id=job_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            batch_size=batch_size,
        )
        if record.status is KnowledgeIngestionStatus.SUCCEEDED:
            assert record.document_id is not None
            return record.document_id
        assert record.status is KnowledgeIngestionStatus.QUEUED
    raise AssertionError("knowledge ingestion did not finish within the bounded test steps")


@pytest.mark.asyncio
async def test_postgres_knowledge_management_full_lifecycle_and_archive() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key = await _create_scope(database, prefix="knowledge")
    other_uuid, other_slug, _ = await _create_scope(
        database,
        prefix="other-knowledge",
        agent_key=agent_key,
    )
    embeddings = DeterministicHashEmbeddingProvider()
    repository = PostgresKnowledgeRepository(database.sessions, embeddings)
    service = PostgresKnowledgeManagementService(
        sessions=database.sessions,
        writer=repository,
        embeddings=embeddings,
        chunker=TextChunker(max_chars=100, overlap_chars=10),
    )
    unique_phrase = f"ORBIT-{uuid4().hex}"

    try:
        first_request = _request(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            content=(
                f"The active operations phrase is {unique_phrase}. "
                "Operators must preserve the audit trail. "
                "Escalations require an incident reference."
            ).encode(),
        )
        first, replayed = await asyncio.gather(
            service.create_ingestion(first_request, idempotency_key="operations-v1"),
            service.create_ingestion(first_request, idempotency_key="operations-v1"),
        )
        assert replayed.id == first.id
        with pytest.raises(ValueError, match="different ingestion request"):
            await service.create_ingestion(
                first_request.model_copy(
                    update={
                        "source": DocumentSource(
                            filename="operations.txt",
                            media_type="text/plain",
                            content=b"different content",
                        )
                    }
                ),
                idempotency_key="operations-v1",
            )
        with pytest.raises(KeyError, match="requested scope"):
            await service.get_ingestion(
                job_id=first.id,
                tenant_id=other_slug,
                agent_id=agent_key,
            )

        first_document_id = await _finish_ingestion(
            service,
            job_id=first.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        replayed_terminal = await service.step_ingestion(
            job_id=first.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        assert replayed_terminal.document_id == first_document_id
        assert replayed_terminal.status is KnowledgeIngestionStatus.SUCCEEDED

        second = await service.create_ingestion(
            _request(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                content=(
                    f"The current operations phrase is {unique_phrase}. "
                    "The second edition replaces the first edition."
                ).encode(),
                version="2",
            ),
            idempotency_key="operations-v2",
        )
        second_document_id = await _finish_ingestion(
            service,
            job_id=second.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )

        first_page = await service.list_documents(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            namespace="operations",
            limit=1,
        )
        assert len(first_page.items) == 1
        assert first_page.next_cursor is not None
        second_page = await service.list_documents(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            namespace="operations",
            limit=1,
            cursor=first_page.next_cursor,
        )
        assert len(second_page.items) == 1
        assert second_page.items[0].id != first_page.items[0].id
        assert second_page.next_cursor is None
        with pytest.raises(ValueError, match="invalid knowledge document cursor"):
            await service.list_documents(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                domain_id=agent_key,
                cursor="%%%not-base64%%%",
            )
        isolated_page = await service.list_documents(
            tenant_id=other_slug,
            agent_id=agent_key,
            domain_id=agent_key,
        )
        assert isolated_page.items == ()

        before_archive = await repository.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                domain_id=agent_key,
                namespace="operations",
                text=unique_phrase,
                access_tags=("operations",),
                limit=5,
            )
        )
        assert before_archive and before_archive[0].document_id == second_document_id
        with pytest.raises(ValueError, match="only an active"):
            await service.archive_document(
                document_id=first_document_id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
            )
        archived = await service.archive_document(
            document_id=second_document_id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        repeated_archive = await service.archive_document(
            document_id=second_document_id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        assert archived.status == repeated_archive.status == "archived"
        assert await repository.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                domain_id=agent_key,
                namespace="operations",
                text=unique_phrase,
                access_tags=("operations",),
                limit=5,
            )
        ) == ()

        async with database.sessions() as session:
            document_count = await session.scalar(
                select(func.count(KnowledgeDocumentModel.id)).where(
                    KnowledgeDocumentModel.tenant_id == tenant_uuid,
                    KnowledgeDocumentModel.source_key == "operations-guide",
                )
            )
            versions = tuple(
                await session.scalars(
                    select(KnowledgeDocumentModel)
                    .where(
                        KnowledgeDocumentModel.tenant_id == tenant_uuid,
                        KnowledgeDocumentModel.source_key == "operations-guide",
                    )
                    .order_by(KnowledgeDocumentModel.version)
                )
            )
        assert document_count == 2
        assert [(row.version, row.status) for row in versions] == [
            ("1", "superseded"),
            ("2", "archived"),
        ]
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(TenantModel).where(TenantModel.id.in_((tenant_uuid, other_uuid)))
            )
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_knowledge_management_parse_failure_is_safe() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key = await _create_scope(database, prefix="parse-fail")
    embeddings = DeterministicHashEmbeddingProvider()
    repository = PostgresKnowledgeRepository(database.sessions, embeddings)
    service = PostgresKnowledgeManagementService(
        sessions=database.sessions,
        writer=repository,
        embeddings=embeddings,
    )
    secret_marker = "must-not-leak"

    try:
        created = await service.create_ingestion(
            _request(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                content=secret_marker.encode() + b"\xff",
            ),
            idempotency_key="invalid-utf8",
        )
        failed = await service.step_ingestion(
            job_id=created.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        assert failed.status is KnowledgeIngestionStatus.FAILED
        assert failed.error_code == "invalid_encoding"
        assert failed.error_message is not None
        assert secret_marker not in failed.error_message
        assert failed.has_more is False

        async with database.sessions() as session:
            row = await session.get(KnowledgeIngestionJobModel, created.id)
            assert row is not None
            assert row.source_bytes is None
            assert row.parsed_text is None
            assert row.step_token is None
            assert row.step_lease_expires_at is None
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_knowledge_step_lease_reclaim_fences_stale_worker() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key = await _create_scope(database, prefix="step-lease")
    embeddings = _BlockingFirstEmbeddingProvider()
    repository = PostgresKnowledgeRepository(database.sessions, embeddings)
    service = PostgresKnowledgeManagementService(
        sessions=database.sessions,
        writer=repository,
        embeddings=embeddings,
    )

    try:
        created = await service.create_ingestion(
            _request(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                content=b"One bounded chunk for lease fencing.",
            ),
            idempotency_key="lease-fencing",
        )
        parsed = await service.step_ingestion(
            job_id=created.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        assert parsed.status is KnowledgeIngestionStatus.QUEUED

        stale_worker = asyncio.create_task(
            service.step_ingestion(
                job_id=created.id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
                batch_size=1,
                lease_seconds=300,
            )
        )
        await asyncio.wait_for(embeddings.first_started.wait(), timeout=5)
        with pytest.raises(RuntimeError, match="already in progress"):
            await service.step_ingestion(
                job_id=created.id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
            )

        async with database.sessions() as session, session.begin():
            row = await session.get(KnowledgeIngestionJobModel, created.id)
            assert row is not None
            first_token = row.step_token
            assert first_token is not None
            row.step_lease_expires_at = utc_now() - timedelta(seconds=1)

        reclaimed = await service.step_ingestion(
            job_id=created.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            batch_size=1,
        )
        assert reclaimed.status is KnowledgeIngestionStatus.QUEUED
        embeddings.release_first.set()
        with pytest.raises(RuntimeError, match="stale"):
            await stale_worker

        async with database.sessions() as session:
            row = await session.get(KnowledgeIngestionJobModel, created.id)
            assert row is not None
            assert row.status == KnowledgeIngestionStatus.QUEUED.value
            assert row.stage == "publishing"
            assert row.error_code is None
            assert row.step_token is None
            assert row.step_lease_expires_at is None
            assert row.step_token != first_token

        published = await service.step_ingestion(
            job_id=created.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        assert published.status is KnowledgeIngestionStatus.SUCCEEDED
        assert published.document_id is not None
        async with database.sessions() as session:
            document_count = await session.scalar(
                select(func.count(KnowledgeDocumentModel.id)).where(
                    KnowledgeDocumentModel.tenant_id == tenant_uuid,
                    KnowledgeDocumentModel.source_key == "operations-guide",
                )
            )
        assert document_count == 1
    finally:
        embeddings.release_first.set()
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_knowledge_api_runs_init_step_poll_against_real_storage() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key = await _create_scope(database, prefix="knowledge-api")
    embeddings = DeterministicHashEmbeddingProvider()
    repository = PostgresKnowledgeRepository(database.sessions, embeddings)
    service = PostgresKnowledgeManagementService(
        sessions=database.sessions,
        writer=repository,
        embeddings=embeddings,
    )

    async def authenticated_principal() -> KnowledgePrincipal:
        return KnowledgePrincipal(
            subject="postgres-api-test",
            tenant_id=tenant_slug,
            allowed_agent_ids=frozenset({agent_key}),
            permissions=frozenset({"knowledge:read", "knowledge:write"}),
        )

    app = create_app(
        database=database,
        knowledge=service,
        knowledge_principal_dependency=authenticated_principal,
    )
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unsupported = await client.post(
                "/v1/knowledge/ingestions",
                headers={"Idempotency-Key": "unsupported"},
                data={
                    "agent_id": agent_key,
                    "domain_id": agent_key,
                    "namespace": "operations",
                    "source_key": "unsupported",
                },
                files={"file": ("payload.bin", b"binary", "application/octet-stream")},
            )
            assert unsupported.status_code == 400
            assert unsupported.json()["error"]["code"] == "unsupported_media_type"

            created = await client.post(
                "/v1/knowledge/ingestions",
                headers={
                    "Idempotency-Key": "api-guide-v1",
                    "X-Tenant-Id": "untrusted-tenant",
                },
                data={
                    "agent_id": agent_key,
                    "domain_id": agent_key,
                    "namespace": "operations",
                    "source_key": "api-guide",
                    "access_tags": '["operations"]',
                },
                files={
                    "file": (
                        "api-guide.txt",
                        b"API-managed knowledge is published through bounded steps.",
                        "text/plain",
                    )
                },
            )
            assert created.status_code == 202
            ingestion_id = created.json()["id"]
            terminal: dict[str, object] | None = None
            for _ in range(10):
                response = await client.post(
                    f"/v1/knowledge/ingestions/{ingestion_id}/step",
                    json={"agent_id": agent_key, "batch_size": 1},
                )
                assert response.status_code == 200
                terminal = response.json()
                if terminal["status"] == "succeeded":
                    break
            assert terminal is not None
            assert terminal["status"] == "succeeded"
            assert terminal["percent"] == 100
            assert terminal["has_more"] is False

            polled = await client.get(
                f"/v1/knowledge/ingestions/{ingestion_id}",
                params={"agent_id": agent_key},
            )
            assert polled.status_code == 200
            assert polled.json()["document_id"] == terminal["document_id"]
            documents = await client.get(
                "/v1/knowledge/documents",
                params={
                    "agent_id": agent_key,
                    "domain_id": agent_key,
                    "namespace": "operations",
                },
            )
            assert documents.status_code == 200
            assert documents.json()["items"][0]["source_key"] == "api-guide"
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()
