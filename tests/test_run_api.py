from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from public_agent.api.app import create_app
from public_agent.api.runs import RunPrincipal
from public_agent.application import ApprovalRecord, RunRecord
from public_agent.core.types import ApprovalDecision, RunContext, RunStatus


class _HealthyDatabase:
    async def ping(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class _RunService:
    def __init__(self) -> None:
        self.run_id = uuid4()
        self.approval_id = uuid4()
        self.last_context: RunContext | None = None
        self.last_tenant_id: str | None = None

    async def create_run(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        task: str,
        context: RunContext,
        idempotency_key: str,
    ) -> RunRecord:
        assert agent_id == "support-agent"
        assert task == "answer safely"
        assert idempotency_key == "run-1"
        self.last_tenant_id = tenant_id
        self.last_context = context
        return self._record(status=RunStatus.FAILED)

    async def get_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> RunRecord:
        assert run_id == self.run_id
        assert agent_id == "support-agent"
        self.last_tenant_id = tenant_id
        return self._record(status=RunStatus.WAITING_APPROVAL)

    async def cancel_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
        canceled_by: str,
        cancellation_note: str | None = None,
    ) -> RunRecord:
        assert run_id == self.run_id
        assert agent_id == "support-agent"
        assert canceled_by == "operator-1"
        assert cancellation_note == "stop"
        self.last_tenant_id = tenant_id
        return self._record(status=RunStatus.CANCELED)

    async def get_approval(
        self,
        *,
        approval_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> ApprovalRecord:
        assert approval_id == self.approval_id
        assert agent_id == "support-agent"
        self.last_tenant_id = tenant_id
        return self._approval()

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
        assert approval_id == self.approval_id
        assert agent_id == "support-agent"
        assert decision is ApprovalDecision.APPROVED
        assert decided_by == "operator-1"
        assert decision_note == "approved"
        assert lease_seconds == 60
        self.last_tenant_id = tenant_id
        return self._record(status=RunStatus.SUCCEEDED)

    def _record(self, *, status: RunStatus) -> RunRecord:
        now = datetime.now(UTC)
        return RunRecord(
            id=self.run_id,
            tenant_id="trusted-tenant",
            agent_id="support-agent",
            agent_version="1.0.0",
            status=status,
            output="safe output" if status is RunStatus.SUCCEEDED else None,
            error="provider leaked secret-token-value" if status is RunStatus.FAILED else None,
            steps=2,
            pending_approval=(
                self._approval() if status is RunStatus.WAITING_APPROVAL else None
            ),
            created_at=now,
            updated_at=now,
        )

    def _approval(self) -> ApprovalRecord:
        now = datetime.now(UTC)
        return ApprovalRecord(
            id=self.approval_id,
            run_id=self.run_id,
            tenant_id="trusted-tenant",
            agent_id="support-agent",
            agent_version="1.0.0",
            status="pending",
            reason="High-risk tool call requires human approval",
            tool_call_id="write-1",
            tool_name="approved_write",
            tool_version="1.0.0",
            decided_by=None,
            decision_note=None,
            created_at=now,
            updated_at=now,
        )


def _principal(*, permissions: frozenset[str] | None = None) -> RunPrincipal:
    return RunPrincipal(
        subject="operator-1",
        tenant_id="trusted-tenant",
        allowed_agent_ids=frozenset({"support-agent"}),
        permissions=permissions
        or frozenset({"runs:read", "runs:write", "approvals:decide"}),
    )


def test_run_routes_are_hidden_until_service_and_auth_are_configured() -> None:
    with TestClient(create_app(database=_HealthyDatabase())) as client:
        assert client.get(f"/v1/runs/{uuid4()}").status_code == 404

    with TestClient(
        create_app(database=_HealthyDatabase(), runs=_RunService())
    ) as client:
        assert client.get(f"/v1/runs/{uuid4()}").status_code == 404


def test_run_api_uses_authenticated_scope_and_never_returns_runtime_secrets() -> None:
    service = _RunService()

    async def authenticated_principal() -> RunPrincipal:
        return _principal()

    app = create_app(
        database=_HealthyDatabase(),
        runs=service,
        run_principal_dependency=authenticated_principal,
    )
    with TestClient(app) as client:
        created = client.post(
            "/v1/runs",
            headers={
                "Idempotency-Key": "run-1",
                "X-Tenant-Id": "attacker-tenant",
            },
            json={
                "agent_id": "support-agent",
                "task": "answer safely",
                "session_id": "session-1",
                "metadata": {"request_label": "api-test"},
            },
        )
        assert created.status_code == 200
        assert created.json()["error"] == {
            "code": "run_failed",
            "message": "The run could not be completed.",
        }
        serialized = created.text
        assert "secret-token-value" not in serialized
        assert "checkpoint" not in serialized
        assert "provider_state" not in serialized
        assert "resume_token" not in serialized
        assert service.last_tenant_id == "trusted-tenant"
        assert service.last_context == RunContext(
            tenant_id="trusted-tenant",
            session_id="session-1",
            user_id="operator-1",
            metadata={"request_label": "api-test"},
        )

        fetched = client.get(
            f"/v1/runs/{service.run_id}",
            params={"agent_id": "support-agent"},
        )
        assert fetched.status_code == 200
        approval = fetched.json()["pending_approval"]
        assert approval["tool_name"] == "approved_write"
        assert "arguments" not in approval
        assert "tool_definition_hash" not in approval

        approval_response = client.get(
            f"/v1/approvals/{service.approval_id}",
            params={"agent_id": "support-agent"},
        )
        assert approval_response.status_code == 200
        assert "arguments" not in approval_response.text

        decided = client.post(
            f"/v1/approvals/{service.approval_id}/decide",
            json={
                "agent_id": "support-agent",
                "decision": "approved",
                "note": "approved",
                "lease_seconds": 60,
            },
        )
        assert decided.status_code == 200
        assert decided.json()["status"] == "succeeded"

        canceled = client.post(
            f"/v1/runs/{service.run_id}/cancel",
            json={"agent_id": "support-agent", "note": "stop"},
        )
        assert canceled.status_code == 200
        assert canceled.json()["error"]["code"] == "run_canceled"


def test_run_api_rejects_permissions_and_server_reserved_metadata() -> None:
    service = _RunService()

    async def read_only_principal() -> RunPrincipal:
        return _principal(permissions=frozenset({"runs:read"}))

    with TestClient(
        create_app(
            database=_HealthyDatabase(),
            runs=service,
            run_principal_dependency=read_only_principal,
        )
    ) as client:
        forbidden = client.post(
            "/v1/runs",
            headers={"Idempotency-Key": "run-1"},
            json={"agent_id": "support-agent", "task": "answer safely"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "run_forbidden"

    async def writer_principal() -> RunPrincipal:
        return _principal()

    with TestClient(
        create_app(
            database=_HealthyDatabase(),
            runs=service,
            run_principal_dependency=writer_principal,
        )
    ) as client:
        reserved = client.post(
            "/v1/runs",
            headers={"Idempotency-Key": "run-1"},
            json={
                "agent_id": "support-agent",
                "task": "answer safely",
                "metadata": {"authorized_knowledge_access_tags": ["admin"]},
            },
        )
        assert reserved.status_code == 422
        assert reserved.json()["error"]["code"] == "request_validation_failed"
