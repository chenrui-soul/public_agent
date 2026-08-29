from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from public_agent.api import KnowledgePrincipal
from public_agent.api.app import create_app
from public_agent.knowledge import (
    KnowledgeDocumentPage,
    KnowledgeDocumentRecord,
    KnowledgeFileInput,
    KnowledgeIdempotencyConflictError,
    KnowledgeIngestionRecord,
    KnowledgeIngestionStage,
    KnowledgeIngestionStatus,
)


class _HealthyDatabase:
    async def ping(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class _BrokenAuthenticator:
    async def authenticate(self, _: str) -> KnowledgePrincipal:
        raise ConnectionError("database unavailable")


class _KnowledgeService:
    def __init__(self) -> None:
        self.job_id = uuid4()
        self.document_id = uuid4()
        self.last_request: KnowledgeFileInput | None = None
        self.last_tenant_id: str | None = None

    async def create_ingestion(
        self,
        request: KnowledgeFileInput,
        *,
        idempotency_key: str,
    ) -> KnowledgeIngestionRecord:
        if idempotency_key == "conflict":
            raise KnowledgeIdempotencyConflictError(
                "idempotency key is already bound to a different ingestion request"
            )
        self.last_request = request
        self.last_tenant_id = request.tenant_id
        return self._ingestion_record()

    async def get_ingestion(
        self,
        *,
        job_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> KnowledgeIngestionRecord:
        assert job_id == self.job_id
        assert agent_id == "support-agent"
        self.last_tenant_id = tenant_id
        return self._ingestion_record()

    async def step_ingestion(
        self,
        *,
        job_id: UUID,
        tenant_id: str,
        agent_id: str,
        batch_size: int = 32,
        lease_seconds: int = 300,
    ) -> KnowledgeIngestionRecord:
        assert job_id == self.job_id
        assert agent_id == "support-agent"
        assert batch_size == 8
        assert lease_seconds == 60
        self.last_tenant_id = tenant_id
        return self._ingestion_record()

    async def list_documents(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        namespace: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> KnowledgeDocumentPage:
        assert agent_id == domain_id == "support-agent"
        assert namespace == "manuals"
        assert status is None
        assert limit == 10
        assert cursor is None
        self.last_tenant_id = tenant_id
        return KnowledgeDocumentPage(items=(self._document_record(),))

    async def archive_document(
        self,
        *,
        document_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> KnowledgeDocumentRecord:
        assert document_id == self.document_id
        assert agent_id == "support-agent"
        self.last_tenant_id = tenant_id
        return self._document_record(status="archived")

    def _ingestion_record(self) -> KnowledgeIngestionRecord:
        now = datetime.now(UTC)
        return KnowledgeIngestionRecord(
            id=self.job_id,
            tenant_id="trusted-tenant",
            agent_id="support-agent",
            domain_id="support-agent",
            namespace="manuals",
            source_key="refund-guide",
            version="1",
            filename="guide.txt",
            media_type="text/plain",
            status=KnowledgeIngestionStatus.QUEUED,
            stage=KnowledgeIngestionStage.PARSING,
            processed_chunks=0,
            total_chunks=0,
            attempts=0,
            created_at=now,
            updated_at=now,
        )

    def _document_record(self, *, status: str = "active") -> KnowledgeDocumentRecord:
        return KnowledgeDocumentRecord(
            id=self.document_id,
            tenant_id="trusted-tenant",
            agent_id="support-agent",
            domain_id="support-agent",
            namespace="manuals",
            source_key="refund-guide",
            title="Refund guide",
            version="1",
            content_hash="a" * 64,
            chunk_count=1,
            status=status,
            access_tags=("support",),
        )


def _principal(*, permissions: frozenset[str] | None = None) -> KnowledgePrincipal:
    return KnowledgePrincipal(
        subject="operator-1",
        tenant_id="trusted-tenant",
        allowed_agent_ids=frozenset({"support-agent"}),
        permissions=permissions or frozenset({"knowledge:read", "knowledge:write"}),
    )


def test_knowledge_routes_are_hidden_until_service_and_auth_are_configured() -> None:
    with TestClient(create_app(database=_HealthyDatabase())) as client:
        assert client.get("/v1/knowledge/documents").status_code == 404

    service = _KnowledgeService()
    with TestClient(
        create_app(database=_HealthyDatabase(), knowledge=service)
    ) as client:
        assert client.get("/v1/knowledge/documents").status_code == 404


def test_knowledge_api_uses_authenticated_tenant_and_exposes_init_step_poll() -> None:
    service = _KnowledgeService()

    async def authenticated_principal() -> KnowledgePrincipal:
        return _principal()

    app = create_app(
        database=_HealthyDatabase(),
        knowledge=service,
        knowledge_principal_dependency=authenticated_principal,
    )
    with TestClient(app) as client:
        created = client.post(
            "/v1/knowledge/ingestions",
            headers={
                "Idempotency-Key": "create-guide-1",
                "X-Tenant-Id": "attacker-controlled-tenant",
            },
            data={
                "agent_id": "support-agent",
                "domain_id": "support-agent",
                "namespace": "manuals",
                "source_key": "refund-guide",
                "access_tags": '["support"]',
                "metadata": '{"owner":"support"}',
            },
            files={"file": ("guide.txt", b"Refunds require a receipt.", "text/plain")},
        )
        assert created.status_code == 202
        assert created.json()["status"] == "queued"
        assert created.json()["percent"] == 0
        assert created.json()["has_more"] is True
        assert service.last_request is not None
        assert service.last_request.tenant_id == "trusted-tenant"
        assert service.last_request.access_tags == ("support",)

        stepped = client.post(
            f"/v1/knowledge/ingestions/{service.job_id}/step",
            json={"agent_id": "support-agent", "batch_size": 8, "lease_seconds": 60},
        )
        assert stepped.status_code == 200
        assert stepped.json()["processed"] == 0
        assert stepped.json()["total"] == 0

        polled = client.get(
            f"/v1/knowledge/ingestions/{service.job_id}",
            params={"agent_id": "support-agent"},
        )
        assert polled.status_code == 200

        documents = client.get(
            "/v1/knowledge/documents",
            params={
                "agent_id": "support-agent",
                "domain_id": "support-agent",
                "namespace": "manuals",
                "limit": 10,
            },
        )
        assert documents.status_code == 200
        assert documents.json()["items"][0]["source_key"] == "refund-guide"

        archived = client.post(
            f"/v1/knowledge/documents/{service.document_id}/archive",
            json={"agent_id": "support-agent"},
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        assert service.last_tenant_id == "trusted-tenant"


def test_knowledge_api_returns_stable_permission_validation_and_conflict_errors() -> None:
    service = _KnowledgeService()

    async def read_only_principal() -> KnowledgePrincipal:
        return _principal(permissions=frozenset({"knowledge:read"}))

    with TestClient(
        create_app(
            database=_HealthyDatabase(),
            knowledge=service,
            knowledge_principal_dependency=read_only_principal,
        )
    ) as client:
        forbidden = client.post(
            "/v1/knowledge/ingestions",
            headers={"Idempotency-Key": "forbidden"},
            data={
                "agent_id": "support-agent",
                "domain_id": "support-agent",
                "namespace": "manuals",
                "source_key": "refund-guide",
            },
            files={"file": ("guide.txt", b"content", "text/plain")},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "knowledge_forbidden"

        invalid = client.get(
            "/v1/knowledge/documents",
            params={
                "agent_id": "support-agent",
                "domain_id": "support-agent",
                "limit": 101,
            },
        )
        assert invalid.status_code == 422
        assert invalid.json() == {
            "error": {
                "code": "request_validation_failed",
                "message": "Request validation failed.",
            }
        }

    async def writer_principal() -> KnowledgePrincipal:
        return _principal()

    with TestClient(
        create_app(
            database=_HealthyDatabase(),
            knowledge=service,
            knowledge_principal_dependency=writer_principal,
        )
    ) as client:
        conflict = client.post(
            "/v1/knowledge/ingestions",
            headers={"Idempotency-Key": "conflict"},
            data={
                "agent_id": "support-agent",
                "domain_id": "support-agent",
                "namespace": "manuals",
                "source_key": "refund-guide",
            },
            files={"file": ("guide.txt", b"content", "text/plain")},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_knowledge_api_authentication_outage_returns_safe_503() -> None:
    with TestClient(
        create_app(
            database=_HealthyDatabase(),
            knowledge=_KnowledgeService(),
            api_keys=_BrokenAuthenticator(),
        )
    ) as client:
        response = client.get(
            "/v1/knowledge/documents",
            headers={"Authorization": "Bearer opaque-token"},
            params={
                "agent_id": "support-agent",
                "domain_id": "support-agent",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "authentication_unavailable",
            "message": "Authentication is temporarily unavailable.",
        }
    }
