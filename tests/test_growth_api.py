from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from public_agent.api.app import create_app
from public_agent.api.growth import GrowthPrincipal
from public_agent.growth.management import (
    CandidateDecision,
    CandidateEvaluationRecord,
    CandidateManagementPage,
    CandidateManagementQuery,
    CandidateManagementRecord,
    MemoryManagementPage,
    MemoryManagementQuery,
    MemoryManagementRecord,
)
from public_agent.growth.models import (
    CandidateRisk,
    CandidateStatus,
    CandidateType,
    LearningCandidate,
)
from public_agent.memory.base import MemoryType


class _HealthyDatabase:
    async def ping(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class _GrowthService:
    def __init__(self) -> None:
        self.candidate_id = uuid4()
        self.memory_id = uuid4()
        self.last_tenant_id: str | None = None

    async def list_memories(self, query: MemoryManagementQuery) -> MemoryManagementPage:
        self.last_tenant_id = query.tenant_id
        assert query.agent_id == "support-agent"
        assert query.domain_id == "support-domain"
        return MemoryManagementPage(items=(self._memory(),), next_cursor="next-memory")

    async def list_candidates(
        self,
        query: CandidateManagementQuery,
    ) -> CandidateManagementPage:
        self.last_tenant_id = query.tenant_id
        assert query.agent_id == "support-agent"
        return CandidateManagementPage(items=(self._candidate(),), next_cursor="next-candidate")

    async def get_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
    ) -> CandidateManagementRecord:
        assert candidate_id == self.candidate_id
        assert agent_id == "support-agent"
        assert domain_id == "support-domain"
        self.last_tenant_id = tenant_id
        return self._candidate()

    async def evaluate_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
    ) -> CandidateManagementRecord:
        assert candidate_id == self.candidate_id
        assert expected_version == 1
        self.last_tenant_id = tenant_id
        return self._candidate(status=CandidateStatus.AWAITING_APPROVAL, version=3)

    async def decide_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
        decision: CandidateDecision,
        decided_by: str,
        decision_note: str | None = None,
    ) -> CandidateManagementRecord:
        assert candidate_id == self.candidate_id
        assert expected_version == 3
        assert decision is CandidateDecision.APPROVED
        assert decided_by == "reviewer-1"
        assert decision_note == "verified"
        self.last_tenant_id = tenant_id
        return self._candidate(status=CandidateStatus.ACTIVE, version=5)

    async def rollback_candidate(
        self,
        *,
        candidate_id: UUID,
        tenant_id: str,
        agent_id: str,
        domain_id: str,
        expected_version: int,
    ) -> CandidateManagementRecord:
        assert candidate_id == self.candidate_id
        assert expected_version == 5
        self.last_tenant_id = tenant_id
        return self._candidate(status=CandidateStatus.ROLLED_BACK, version=6)

    def _memory(self) -> MemoryManagementRecord:
        now = datetime.now(UTC)
        return MemoryManagementRecord(
            id=self.memory_id,
            tenant_id="trusted-tenant",
            agent_id="support-agent",
            domain_id="support-domain",
            namespace="support-memory",
            memory_type=MemoryType.SEMANTIC,
            content="Use the verified support escalation procedure.",
            status="active",
            confidence=0.9,
            importance=0.8,
            candidate_id=self.candidate_id,
            source_run_id=uuid4(),
            recall_count=4,
            created_at=now,
            updated_at=now,
        )

    def _candidate(
        self,
        *,
        status: CandidateStatus = CandidateStatus.PENDING,
        version: int = 1,
    ) -> CandidateManagementRecord:
        now = datetime.now(UTC)
        return CandidateManagementRecord(
            candidate=LearningCandidate(
                id=self.candidate_id,
                tenant_id="trusted-tenant",
                agent_id="support-agent",
                domain_id="support-domain",
                candidate_type=CandidateType.MEMORY,
                risk=CandidateRisk.LOW,
                title="Verified escalation procedure",
                fingerprint="a" * 64,
                proposed_change={
                    "content": "Use the verified support escalation procedure.",
                    "namespace": "support-memory",
                    "memory_type": "semantic",
                    "confidence": 0.9,
                    "importance": 0.8,
                    "tags": ["support"],
                    "evidence_event_ids": [str(uuid4())],
                    "reflection_prompt": "secret-reflection-prompt",
                    "reflection_engine": "internal-engine",
                    "provider_state": {"secret": "provider-secret"},
                    "checkpoint": {"messages": ["unredacted-event-body"]},
                },
                evidence_run_ids=(uuid4(),),
                status=status,
                version=version,
                created_at=now,
                updated_at=now,
            ),
            latest_evaluation=CandidateEvaluationRecord(
                id=uuid4(),
                passed=True,
                score=1.0,
                summary="Trusted evaluator passed the candidate.",
                metrics={"has_evidence": 1.0},
                candidate_version=1,
                created_at=now,
            ),
        )


def _principal(*, permissions: frozenset[str] | None = None) -> GrowthPrincipal:
    return GrowthPrincipal(
        subject="reviewer-1",
        tenant_id="trusted-tenant",
        allowed_agent_ids=frozenset({"support-agent"}),
        permissions=permissions
        or frozenset(
            {
                "memories:read",
                "candidates:read",
                "candidates:evaluate",
                "candidates:promote",
            }
        ),
    )


def test_growth_routes_are_hidden_until_service_and_auth_are_configured() -> None:
    with TestClient(create_app(database=_HealthyDatabase())) as client:
        assert client.get("/v1/memories").status_code == 404

    with TestClient(
        create_app(database=_HealthyDatabase(), growth=_GrowthService())
    ) as client:
        assert client.get("/v1/candidates").status_code == 404


def test_growth_api_uses_authenticated_scope_and_safe_candidate_projection() -> None:
    service = _GrowthService()

    async def authenticated_principal() -> GrowthPrincipal:
        return _principal()

    app = create_app(
        database=_HealthyDatabase(),
        growth=service,
        growth_principal_dependency=authenticated_principal,
    )
    with TestClient(app) as client:
        memories = client.get(
            "/v1/memories",
            headers={"X-Tenant-Id": "attacker-tenant"},
            params={
                "agent_id": "support-agent",
                "domain_id": "support-domain",
                "namespace": "support-memory",
            },
        )
        assert memories.status_code == 200
        assert memories.json()["next_cursor"] == "next-memory"
        assert service.last_tenant_id == "trusted-tenant"

        candidates = client.get(
            "/v1/candidates",
            params={"agent_id": "support-agent", "domain_id": "support-domain"},
        )
        assert candidates.status_code == 200
        assert candidates.json()["items"][0]["content_preview"].startswith("Use the verified")

        detail = client.get(
            f"/v1/candidates/{service.candidate_id}",
            params={"agent_id": "support-agent", "domain_id": "support-domain"},
        )
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["proposal"]["content"].startswith("Use the verified")
        serialized = detail.text
        assert "secret-reflection-prompt" not in serialized
        assert "provider-secret" not in serialized
        assert "unredacted-event-body" not in serialized
        assert "reflection_engine" not in serialized
        assert "candidate_version" not in serialized

        evaluated = client.post(
            f"/v1/candidates/{service.candidate_id}/evaluate",
            json={
                "agent_id": "support-agent",
                "domain_id": "support-domain",
                "expected_version": 1,
            },
        )
        assert evaluated.status_code == 200
        assert evaluated.json()["status"] == "awaiting_approval"

        decided = client.post(
            f"/v1/candidates/{service.candidate_id}/decide",
            json={
                "agent_id": "support-agent",
                "domain_id": "support-domain",
                "expected_version": 3,
                "decision": "approved",
                "note": "verified",
            },
        )
        assert decided.status_code == 200
        assert decided.json()["status"] == "active"

        rolled_back = client.post(
            f"/v1/candidates/{service.candidate_id}/rollback",
            json={
                "agent_id": "support-agent",
                "domain_id": "support-domain",
                "expected_version": 5,
            },
        )
        assert rolled_back.status_code == 200
        assert rolled_back.json()["status"] == "rolled_back"


def test_growth_api_enforces_separate_read_evaluate_and_promote_permissions() -> None:
    service = _GrowthService()

    async def memory_reader() -> GrowthPrincipal:
        return _principal(permissions=frozenset({"memories:read"}))

    app = create_app(
        database=_HealthyDatabase(),
        growth=service,
        growth_principal_dependency=memory_reader,
    )
    with TestClient(app) as client:
        allowed = client.get(
            "/v1/memories",
            params={"agent_id": "support-agent", "domain_id": "support-domain"},
        )
        assert allowed.status_code == 200

        candidates = client.get(
            "/v1/candidates",
            params={"agent_id": "support-agent", "domain_id": "support-domain"},
        )
        assert candidates.status_code == 403
        assert candidates.json()["error"]["code"] == "growth_forbidden"

        evaluated = client.post(
            f"/v1/candidates/{service.candidate_id}/evaluate",
            json={
                "agent_id": "support-agent",
                "domain_id": "support-domain",
                "expected_version": 1,
            },
        )
        assert evaluated.status_code == 403

        decided = client.post(
            f"/v1/candidates/{service.candidate_id}/decide",
            json={
                "agent_id": "support-agent",
                "domain_id": "support-domain",
                "expected_version": 3,
                "decision": "approved",
            },
        )
        assert decided.status_code == 403
