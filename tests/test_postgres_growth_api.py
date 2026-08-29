from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from public_agent.api.app import create_app
from public_agent.api.growth import GrowthPrincipal
from public_agent.auth import APITokenCodec, PrincipalCreateRequest
from public_agent.config import Settings
from public_agent.growth.management import AgentGrowthManagementService
from public_agent.growth.pipeline import EvidenceBasedCandidateEvaluator
from public_agent.storage.auth import PostgresAPIKeyService
from public_agent.storage.database import Database
from public_agent.storage.growth_management import PostgresGrowthManagementRepository
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    ApprovalModel,
    EvaluationModel,
    LearningCandidateModel,
    MemoryModel,
    RunModel,
    TenantModel,
)
from public_agent.storage.repositories import PostgresKnowledgeAssetPublisher

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


async def _create_scope(
    database: Database,
    *,
    prefix: str,
) -> tuple[UUID, str, UUID, str, str]:
    tenant_id = uuid4()
    agent_id = uuid4()
    tenant_slug = f"{prefix}-tenant-{tenant_id.hex[:8]}"
    agent_key = f"{prefix}-agent-{agent_id.hex[:8]}"
    domain_id = f"{prefix}-domain-{agent_id.hex[:8]}"
    async with database.sessions() as session, session.begin():
        session.add(TenantModel(id=tenant_id, slug=tenant_slug, name=f"{prefix} Tenant"))
        await session.flush()
        session.add(
            AgentModel(
                id=agent_id,
                tenant_id=tenant_id,
                agent_key=agent_key,
                name=f"{prefix} Agent",
                domain_id=domain_id,
            )
        )
    return tenant_id, tenant_slug, agent_id, agent_key, domain_id


def _candidate_row(
    *,
    tenant_id: UUID,
    agent_id: UUID,
    domain_id: str,
    title: str,
    content: str,
    evidence_run_id: UUID | None = None,
    created_at: datetime | None = None,
) -> LearningCandidateModel:
    now = created_at or datetime.now(UTC)
    return LearningCandidateModel(
        id=uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        domain_id=domain_id,
        candidate_type="memory",
        risk="low",
        title=title,
        fingerprint=uuid4().hex + uuid4().hex,
        proposed_change={
            "content": content,
            "namespace": "growth-memory",
            "memory_type": "semantic",
            "confidence": 0.9,
            "importance": 0.8,
            "evidence_event_ids": [str(uuid4())] if evidence_run_id is not None else [],
            "reflection_prompt": "never-return-this-reflection-prompt",
            "provider_state": {"secret": "never-return-provider-state"},
            "checkpoint": {"raw_event": "never-return-unredacted-event"},
        },
        evidence_run_ids=[str(evidence_run_id)] if evidence_run_id is not None else [],
        status="pending",
        version=1,
        created_at=now,
        updated_at=now,
    )


def _management_service(database: Database) -> AgentGrowthManagementService:
    return AgentGrowthManagementService(
        repository=PostgresGrowthManagementRepository(database.sessions),
        evaluator=EvidenceBasedCandidateEvaluator(),
        publisher=PostgresKnowledgeAssetPublisher(database.sessions),
    )


@pytest.mark.asyncio
async def test_postgres_memory_and_candidate_lists_use_stable_side_effect_free_keysets() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_uuid, agent_key, domain_id = await _create_scope(
        database,
        prefix="growth-list",
    )
    now = datetime.now(UTC)
    memory_ids = [uuid4(), uuid4(), uuid4()]
    candidate_rows = [
        _candidate_row(
            tenant_id=tenant_uuid,
            agent_id=agent_uuid,
            domain_id=domain_id,
            title=f"Candidate {index}",
            content=f"Verified candidate content {index}",
            created_at=now - timedelta(minutes=index),
        )
        for index in range(3)
    ]
    try:
        async with database.sessions() as session, session.begin():
            session.add_all(candidate_rows)
            session.add_all(
                [
                    MemoryModel(
                        id=memory_id,
                        tenant_id=tenant_uuid,
                        agent_id=agent_uuid,
                        domain_id=domain_id,
                        namespace="growth-memory",
                        memory_type="semantic",
                        content=f"Verified memory {index}",
                        status="active",
                        confidence=0.9,
                        importance=0.8,
                        metadata_json={
                            "reflection_prompt": "hidden",
                            "provider_state": "hidden",
                        },
                        recall_count=7,
                        created_at=now - timedelta(minutes=index),
                        updated_at=now - timedelta(minutes=index),
                    )
                    for index, memory_id in enumerate(memory_ids)
                ]
            )

        async def principal() -> GrowthPrincipal:
            return GrowthPrincipal(
                subject="reader",
                tenant_id=tenant_slug,
                allowed_agent_ids=frozenset({agent_key}),
                permissions=frozenset({"memories:read", "candidates:read"}),
            )

        app = create_app(
            database=database,
            growth=_management_service(database),
            growth_principal_dependency=principal,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            first = await client.get(
                "/v1/memories",
                params={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "namespace": "growth-memory",
                    "text": "Verified",
                    "limit": 2,
                },
            )
            assert first.status_code == 200
            first_payload = first.json()
            assert len(first_payload["items"]) == 2
            assert first_payload["next_cursor"]
            second = await client.get(
                "/v1/memories",
                params={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "namespace": "growth-memory",
                    "text": "Verified",
                    "limit": 2,
                    "cursor": first_payload["next_cursor"],
                },
            )
            assert second.status_code == 200
            listed_memory_ids = {
                item["id"] for item in first_payload["items"] + second.json()["items"]
            }
            assert listed_memory_ids == {str(memory_id) for memory_id in memory_ids}
            assert "reflection_prompt" not in first.text
            assert "provider_state" not in first.text

            invalid = await client.get(
                "/v1/memories",
                params={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "cursor": "not-valid-base64!",
                },
            )
            assert invalid.status_code == 400
            assert invalid.json()["error"]["code"] == "invalid_cursor"

            candidate_first = await client.get(
                "/v1/candidates",
                params={"agent_id": agent_key, "domain_id": domain_id, "limit": 2},
            )
            assert candidate_first.status_code == 200
            candidate_second = await client.get(
                "/v1/candidates",
                params={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "limit": 2,
                    "cursor": candidate_first.json()["next_cursor"],
                },
            )
            listed_candidate_ids = {
                item["id"]
                for item in candidate_first.json()["items"]
                + candidate_second.json()["items"]
            }
            assert listed_candidate_ids == {str(row.id) for row in candidate_rows}

        async with database.sessions() as session:
            recall_counts = tuple(
                (
                    await session.scalars(
                        select(MemoryModel.recall_count)
                        .where(MemoryModel.id.in_(memory_ids))
                        .order_by(MemoryModel.id)
                    )
                ).all()
            )
            assert recall_counts == (7, 7, 7)
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_bearer_candidate_evaluation_decision_publish_and_rollback() -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_uuid, agent_key, domain_id = await _create_scope(
        database,
        prefix="growth-flow",
    )
    other_uuid, other_slug, _, _, _ = await _create_scope(database, prefix="growth-other")
    agent_version_id = uuid4()
    evidence_run_id = uuid4()
    candidate = _candidate_row(
        tenant_id=tenant_uuid,
        agent_id=agent_uuid,
        domain_id=domain_id,
        title="Production escalation rule",
        content="Always verify account ownership before escalating a support request.",
        evidence_run_id=evidence_run_id,
    )
    rejected_by_evaluator = _candidate_row(
        tenant_id=tenant_uuid,
        agent_id=agent_uuid,
        domain_id=domain_id,
        title="Weak candidate",
        content="short",
    )
    human_rejected = _candidate_row(
        tenant_id=tenant_uuid,
        agent_id=agent_uuid,
        domain_id=domain_id,
        title="Human review candidate",
        content="Require a second reviewer before changing a protected support policy.",
        evidence_run_id=evidence_run_id,
    )
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("growth-api-bearer-test-pepper"),
    )
    try:
        async with database.sessions() as session, session.begin():
            session.add(
                AgentVersionModel(
                    id=agent_version_id,
                    tenant_id=tenant_uuid,
                    agent_id=agent_uuid,
                    version="1.0.0",
                    instructions="Test growth management safely.",
                    memory_namespace="growth-memory",
                    configuration={},
                )
            )
            await session.flush()
            session.add(
                RunModel(
                    id=evidence_run_id,
                    tenant_id=tenant_uuid,
                    agent_id=agent_uuid,
                    agent_version_id=agent_version_id,
                    status="succeeded",
                    task="Produce verified support guidance.",
                    output="Verified support guidance.",
                    metadata_json={},
                )
            )
            session.add_all((candidate, rejected_by_evaluator, human_rejected))

        reviewer = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="growth-reviewer",
                display_name="Growth Reviewer",
                permissions=(
                    "memories:read",
                    "candidates:read",
                    "candidates:evaluate",
                    "candidates:promote",
                ),
                agent_ids=(agent_key,),
            )
        )
        reviewer_token = await auth.issue_token(
            principal_id=reviewer.id,
            tenant_id=tenant_slug,
            label="growth-reviewer-token",
        )
        other = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=other_slug,
                subject="other-reviewer",
                display_name="Other Reviewer",
                permissions=("candidates:read",),
                all_agents=True,
            )
        )
        other_token = await auth.issue_token(
            principal_id=other.id,
            tenant_id=other_slug,
            label="other-reviewer-token",
        )
        app = create_app(
            database=database,
            growth=_management_service(database),
            api_keys=auth,
        )
        headers = {"Authorization": f"Bearer {reviewer_token.token.get_secret_value()}"}
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            forged = await client.post(
                f"/v1/candidates/{candidate.id}/evaluate",
                headers=headers,
                json={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "expected_version": 1,
                    "passed": True,
                },
            )
            assert forged.status_code == 422

            evaluated = await client.post(
                f"/v1/candidates/{candidate.id}/evaluate",
                headers={**headers, "X-Tenant-Id": other_slug},
                json={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "expected_version": 1,
                },
            )
            assert evaluated.status_code == 200
            assert evaluated.json()["status"] == "awaiting_approval"
            assert evaluated.json()["version"] == 3
            assert evaluated.json()["latest_evaluation"]["passed"] is True
            assert "never-return-this-reflection-prompt" not in evaluated.text
            assert "never-return-provider-state" not in evaluated.text
            assert "never-return-unredacted-event" not in evaluated.text

            evaluation_replay = await client.post(
                f"/v1/candidates/{candidate.id}/evaluate",
                headers=headers,
                json={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "expected_version": 1,
                },
            )
            assert evaluation_replay.status_code == 200
            assert evaluation_replay.json()["version"] == 3

            weak = await client.post(
                f"/v1/candidates/{rejected_by_evaluator.id}/evaluate",
                headers=headers,
                json={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "expected_version": 1,
                },
            )
            assert weak.status_code == 200
            assert weak.json()["status"] == "rejected"
            assert weak.json()["latest_evaluation"]["passed"] is False

            human_evaluated = await client.post(
                f"/v1/candidates/{human_rejected.id}/evaluate",
                headers=headers,
                json={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "expected_version": 1,
                },
            )
            assert human_evaluated.status_code == 200
            human_rejection_body = {
                "agent_id": agent_key,
                "domain_id": domain_id,
                "expected_version": 3,
                "decision": "rejected",
                "note": "Policy impact requires a new proposal.",
            }
            human_rejection = await client.post(
                f"/v1/candidates/{human_rejected.id}/decide",
                headers=headers,
                json=human_rejection_body,
            )
            assert human_rejection.status_code == 200
            assert human_rejection.json()["status"] == "rejected"
            repeated_human_rejection = await client.post(
                f"/v1/candidates/{human_rejected.id}/decide",
                headers=headers,
                json=human_rejection_body,
            )
            assert repeated_human_rejection.status_code == 200
            changed_human_decision = await client.post(
                f"/v1/candidates/{human_rejected.id}/decide",
                headers=headers,
                json={**human_rejection_body, "decision": "approved"},
            )
            assert changed_human_decision.status_code == 409

            approval_body = {
                "agent_id": agent_key,
                "domain_id": domain_id,
                "expected_version": 3,
                "decision": "approved",
                "note": "Evidence verified.",
            }
            approved, approval_replay = await asyncio.gather(
                client.post(
                    f"/v1/candidates/{candidate.id}/decide",
                    headers=headers,
                    json=approval_body,
                ),
                client.post(
                    f"/v1/candidates/{candidate.id}/decide",
                    headers=headers,
                    json=approval_body,
                ),
            )
            assert approved.status_code == 200
            approved_payload = approved.json()
            assert approved_payload["status"] == "active"
            assert approved_payload["version"] == 5
            assert approved_payload["published_memory"]["status"] == "active"
            assert approval_replay.status_code == 200
            assert approval_replay.json()["version"] == 5

            changed_replay = await client.post(
                f"/v1/candidates/{candidate.id}/decide",
                headers=headers,
                json={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "expected_version": 3,
                    "decision": "approved",
                    "note": "Changed note.",
                },
            )
            assert changed_replay.status_code == 409
            assert changed_replay.json()["error"]["code"] == "candidate_state_conflict"

            rolled_back = await client.post(
                f"/v1/candidates/{candidate.id}/rollback",
                headers=headers,
                json={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "expected_version": 5,
                },
            )
            assert rolled_back.status_code == 200
            assert rolled_back.json()["status"] == "rolled_back"
            assert rolled_back.json()["version"] == 6
            assert rolled_back.json()["published_memory"]["status"] == "superseded"

            rollback_replay = await client.post(
                f"/v1/candidates/{candidate.id}/rollback",
                headers=headers,
                json={
                    "agent_id": agent_key,
                    "domain_id": domain_id,
                    "expected_version": 5,
                },
            )
            assert rollback_replay.status_code == 200
            assert rollback_replay.json()["version"] == 6

            hidden = await client.get(
                f"/v1/candidates/{candidate.id}",
                headers={
                    "Authorization": f"Bearer {other_token.token.get_secret_value()}"
                },
                params={"agent_id": agent_key, "domain_id": domain_id},
            )
            assert hidden.status_code == 404
            assert hidden.json()["error"]["code"] == "candidate_not_found"

        async with database.sessions() as session:
            assert await session.scalar(
                select(func.count()).select_from(EvaluationModel).where(
                    EvaluationModel.candidate_id == candidate.id
                )
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(ApprovalModel).where(
                    ApprovalModel.candidate_id == candidate.id
                )
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(MemoryModel).where(
                    MemoryModel.candidate_id == candidate.id
                )
            ) == 1
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(TenantModel).where(TenantModel.id.in_((tenant_uuid, other_uuid)))
            )
        await database.dispose()
