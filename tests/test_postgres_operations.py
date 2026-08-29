from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from public_agent.api.app import create_app
from public_agent.auth import APITokenCodec, PrincipalCreateRequest
from public_agent.config import Settings
from public_agent.core.types import utc_now
from public_agent.operations import (
    OPERATIONS_JOBS_READ,
    OPERATIONS_JOBS_RETRY,
    ReflectionJobAuthorizationError,
    ReflectionJobConflictError,
)
from public_agent.storage.auth import PostgresAPIKeyService
from public_agent.storage.database import Database
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    APIPrincipalModel,
    APITokenModel,
    OutboxJobModel,
    ReflectionJobOperationAuditEventModel,
    ReflectionJobRetryRequestModel,
    RunModel,
    TenantModel,
)
from public_agent.storage.operations import PostgresReflectionJobOperations
from public_agent.storage.outbox import PostgresReflectionJobStore
from public_agent.workers import ReflectionJobLeaseLostError, ReflectionJobState

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)

_TASK_SECRET = "Authorization: Bearer public_agent_task-secret"
_OUTPUT_SECRET = "provider output private body"
_RAW_IDEMPOTENCY_KEY = "retry-public_agent_secret-idempotency"


@dataclass(frozen=True, slots=True)
class _OperationsFixture:
    tenant_id: UUID
    tenant_slug: str
    other_tenant_id: UUID
    agent_a_id: UUID
    agent_a_key: str
    agent_b_id: UUID
    agent_b_key: str
    dead_job_id: UUID
    pending_job_id: UUID
    agent_b_job_id: UUID
    handler_v2_job_id: UUID
    other_tenant_job_id: UUID
    same_key_job_id: UUID
    different_key_job_id: UUID
    lease_job_id: UUID


@pytest.mark.asyncio
async def test_operations_api_scopes_safe_keysets_and_idempotent_retry() -> None:
    database = Database(Settings().database_url)
    fixture = await _create_fixture(database)
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("operations-api-test-pepper"),
    )
    operations = PostgresReflectionJobOperations(database.sessions)
    try:
        scoped = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=fixture.tenant_slug,
                subject="scoped-operator",
                display_name="Scoped Operator",
                permissions=(OPERATIONS_JOBS_READ, OPERATIONS_JOBS_RETRY),
                agent_ids=(fixture.agent_a_key,),
            )
        )
        scoped_token = await auth.issue_token(
            principal_id=scoped.id,
            tenant_id=fixture.tenant_slug,
            label="scoped-operator-token",
        )
        reader = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=fixture.tenant_slug,
                subject="operations-reader",
                display_name="Operations Reader",
                permissions=(OPERATIONS_JOBS_READ,),
                all_agents=True,
            )
        )
        reader_token = await auth.issue_token(
            principal_id=reader.id,
            tenant_id=fixture.tenant_slug,
            label="operations-reader-token",
        )
        scoped_headers = {
            "Authorization": f"Bearer {scoped_token.token.get_secret_value()}"
        }
        reader_headers = {
            "Authorization": f"Bearer {reader_token.token.get_secret_value()}"
        }
        app = create_app(
            database=database,
            api_keys=auth,
            operations=operations,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            stats = await client.get(
                "/v1/operations/reflection-jobs/stats",
                headers=scoped_headers,
            )
            assert stats.status_code == 200
            assert stats.json()["dead_letter"] == 3
            assert stats.json()["pending"] == 1

            first = await client.get(
                "/v1/operations/reflection-jobs?limit=1",
                headers=scoped_headers,
            )
            assert first.status_code == 200
            assert len(first.json()["items"]) == 1
            cursor = first.json()["next_cursor"]
            assert cursor is not None
            second = await client.get(
                "/v1/operations/reflection-jobs",
                headers=scoped_headers,
                params={"limit": 100, "cursor": cursor},
            )
            assert second.status_code == 200
            page_ids = {
                first.json()["items"][0]["id"],
                *(item["id"] for item in second.json()["items"]),
            }
            assert str(fixture.agent_b_job_id) not in page_ids
            assert str(fixture.other_tenant_job_id) not in page_ids
            assert all(
                item["handler_version"] == "reflection-v1"
                for item in first.json()["items"] + second.json()["items"]
            )

            invalid = await client.get(
                "/v1/operations/reflection-jobs",
                headers=scoped_headers,
                params={"cursor": f"{cursor}=", "limit": 1},
            )
            assert invalid.status_code == 400
            assert invalid.json()["error"]["code"] == "invalid_cursor"
            rebound = await client.get(
                "/v1/operations/reflection-jobs",
                headers=scoped_headers,
                params={"cursor": cursor, "handler_version": "reflection-v2"},
            )
            assert rebound.status_code == 400

            version_two = await client.get(
                "/v1/operations/reflection-jobs",
                headers=scoped_headers,
                params={"handler_version": "reflection-v2"},
            )
            assert version_two.status_code == 200
            assert [item["id"] for item in version_two.json()["items"]] == [
                str(fixture.handler_v2_job_id)
            ]

            forbidden_agent = await client.get(
                f"/v1/operations/reflection-jobs/{fixture.agent_b_job_id}",
                headers=scoped_headers,
            )
            assert forbidden_agent.status_code == 403
            cross_tenant = await client.get(
                f"/v1/operations/reflection-jobs/{fixture.other_tenant_job_id}",
                headers=scoped_headers,
            )
            assert cross_tenant.status_code == 404

            detail = await client.get(
                f"/v1/operations/reflection-jobs/{fixture.dead_job_id}",
                headers=scoped_headers,
            )
            assert detail.status_code == 200
            detail_body = detail.json()
            serialized = json.dumps(detail_body, sort_keys=True).lower()
            for forbidden in (
                _TASK_SECRET.lower(),
                _OUTPUT_SECRET.lower(),
                "authorization",
                "provider body",
                "payload",
                "result_metadata",
                "worker_id",
                "lease_token",
                "checkpoint",
                "provider_state",
                "trace",
                "task",
                "output",
            ):
                assert forbidden not in serialized

            read_only_retry = await client.post(
                f"/v1/operations/reflection-jobs/{fixture.dead_job_id}/retry",
                headers={**reader_headers, "Idempotency-Key": "reader-denied"},
                json={"expected_version": detail_body["version"]},
            )
            assert read_only_retry.status_code == 403
            no_grant_retry = await client.post(
                f"/v1/operations/reflection-jobs/{fixture.agent_b_job_id}/retry",
                headers={**scoped_headers, "Idempotency-Key": "agent-grant-denied"},
                json={"expected_version": 1},
            )
            assert no_grant_retry.status_code == 403

            first_retry = await client.post(
                f"/v1/operations/reflection-jobs/{fixture.dead_job_id}/retry",
                headers={**scoped_headers, "Idempotency-Key": _RAW_IDEMPOTENCY_KEY},
                json={"expected_version": detail_body["version"]},
            )
            replay = await client.post(
                f"/v1/operations/reflection-jobs/{fixture.dead_job_id}/retry",
                headers={**scoped_headers, "Idempotency-Key": _RAW_IDEMPOTENCY_KEY},
                json={"expected_version": detail_body["version"]},
            )
            assert first_retry.status_code == replay.status_code == 200
            assert first_retry.json()["status"] == "pending"
            assert first_retry.json()["idempotent_replay"] is False
            assert replay.json()["idempotent_replay"] is True
            assert first_retry.json()["version"] == replay.json()["version"]

            mismatched_replay = await client.post(
                f"/v1/operations/reflection-jobs/{fixture.dead_job_id}/retry",
                headers={**scoped_headers, "Idempotency-Key": _RAW_IDEMPOTENCY_KEY},
                json={"expected_version": detail_body["version"] + 1},
            )
            assert mismatched_replay.status_code == 409
            assert mismatched_replay.json()["error"]["code"] == "idempotency_conflict"
            state_conflict = await client.post(
                f"/v1/operations/reflection-jobs/{fixture.dead_job_id}/retry",
                headers={**scoped_headers, "Idempotency-Key": "different-retry-key"},
                json={"expected_version": first_retry.json()["version"]},
            )
            assert state_conflict.status_code == 409
            assert (
                state_conflict.json()["error"]["code"]
                == "reflection_job_state_conflict"
            )

        async with database.sessions() as session:
            retried = await session.get(OutboxJobModel, fixture.dead_job_id)
            request = await session.scalar(
                select(ReflectionJobRetryRequestModel).where(
                    ReflectionJobRetryRequestModel.job_id == fixture.dead_job_id,
                    ReflectionJobRetryRequestModel.outcome == "success",
                )
            )
            audit_rows = tuple(
                await session.scalars(
                    select(ReflectionJobOperationAuditEventModel).where(
                        ReflectionJobOperationAuditEventModel.job_id
                        == fixture.dead_job_id
                    )
                )
            )
        assert retried is not None
        assert retried.status == ReflectionJobState.PENDING.value
        assert retried.attempts == 3
        assert retried.attempts_in_cycle == 0
        assert retried.lease_token is None
        assert retried.worker_id is None
        assert retried.last_error_code is None
        assert retried.completed_at is None
        assert retried.result_metadata == {}
        assert request is not None
        persisted = json.dumps(
            {
                "request": {
                    column.name: getattr(request, column.name)
                    for column in request.__table__.columns
                },
                "audit": [
                    {
                        column.name: getattr(row, column.name)
                        for column in row.__table__.columns
                    }
                    for row in audit_rows
                ],
            },
            default=str,
            sort_keys=True,
        ).lower()
        assert _RAW_IDEMPOTENCY_KEY.lower() not in persisted
        assert _TASK_SECRET.lower() not in persisted
        assert _OUTPUT_SECRET.lower() not in persisted
        assert "authorization" not in persisted
    finally:
        await _cleanup(database, fixture.tenant_id, fixture.other_tenant_id)


@pytest.mark.asyncio
async def test_operations_retry_revalidates_actor_and_handles_concurrency_and_fencing() -> None:
    database = Database(Settings().database_url)
    fixture = await _create_fixture(database)
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("operations-concurrency-test-pepper"),
    )
    operations = PostgresReflectionJobOperations(database.sessions)
    try:
        first_principal = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=fixture.tenant_slug,
                subject="operations-admin-a",
                display_name="Operations Admin A",
                permissions=(OPERATIONS_JOBS_READ, OPERATIONS_JOBS_RETRY),
                all_agents=True,
            )
        )
        second_principal = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=fixture.tenant_slug,
                subject="operations-admin-b",
                display_name="Operations Admin B",
                permissions=(OPERATIONS_JOBS_READ, OPERATIONS_JOBS_RETRY),
                all_agents=True,
            )
        )
        first_token = await auth.issue_token(
            principal_id=first_principal.id,
            tenant_id=fixture.tenant_slug,
            label="operations-admin-a-token",
        )
        second_token = await auth.issue_token(
            principal_id=second_principal.id,
            tenant_id=fixture.tenant_slug,
            label="operations-admin-b-token",
        )
        first_actor = await auth.authenticate(first_token.token.get_secret_value())
        second_actor = await auth.authenticate(second_token.token.get_secret_value())

        same_version = await _job_version(database, fixture.same_key_job_id)
        same_results = await asyncio.gather(
            operations.retry_job(
                job_id=fixture.same_key_job_id,
                expected_version=same_version,
                idempotency_key="same-concurrent-request",
                actor=first_actor,
            ),
            operations.retry_job(
                job_id=fixture.same_key_job_id,
                expected_version=same_version,
                idempotency_key="same-concurrent-request",
                actor=first_actor,
            ),
        )
        assert {item.idempotent_replay for item in same_results} == {False, True}
        assert len({item.version for item in same_results}) == 1

        different_version = await _job_version(database, fixture.different_key_job_id)
        different_results = await asyncio.gather(
            operations.retry_job(
                job_id=fixture.different_key_job_id,
                expected_version=different_version,
                idempotency_key="different-key-a",
                actor=first_actor,
            ),
            operations.retry_job(
                job_id=fixture.different_key_job_id,
                expected_version=different_version,
                idempotency_key="different-key-b",
                actor=second_actor,
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in different_results) == 1
        conflicts = [
            item for item in different_results if isinstance(item, ReflectionJobConflictError)
        ]
        assert len(conflicts) == 1
        assert conflicts[0].code in {
            "operations.jobs.state_conflict",
            "operations.jobs.version_conflict",
        }

        stale_version = await _job_version(database, fixture.handler_v2_job_id)
        with pytest.raises(
            ReflectionJobConflictError,
            match=r"operations\.jobs\.version_conflict",
        ):
            await operations.retry_job(
                job_id=fixture.handler_v2_job_id,
                expected_version=stale_version + 1,
                idempotency_key="stale-version-request",
                actor=first_actor,
            )

        revoked_principal = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=fixture.tenant_slug,
                subject="revoked-after-auth",
                display_name="Revoked After Auth",
                permissions=(OPERATIONS_JOBS_RETRY,),
                all_agents=True,
            )
        )
        revoked_token = await auth.issue_token(
            principal_id=revoked_principal.id,
            tenant_id=fixture.tenant_slug,
            label="revoked-after-auth-token",
        )
        revoked_actor = await auth.authenticate(revoked_token.token.get_secret_value())
        async with database.sessions() as session, session.begin():
            await session.execute(
                update(APITokenModel)
                .where(APITokenModel.id == revoked_token.id)
                .values(revoked_at=utc_now())
            )
        with pytest.raises(ReflectionJobAuthorizationError):
            await operations.retry_job(
                job_id=fixture.handler_v2_job_id,
                expected_version=stale_version,
                idempotency_key="revoked-actor-request",
                actor=revoked_actor,
            )

        disabled_principal = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=fixture.tenant_slug,
                subject="disabled-after-auth",
                display_name="Disabled After Auth",
                permissions=(OPERATIONS_JOBS_RETRY,),
                all_agents=True,
            )
        )
        disabled_token = await auth.issue_token(
            principal_id=disabled_principal.id,
            tenant_id=fixture.tenant_slug,
            label="disabled-after-auth-token",
        )
        disabled_actor = await auth.authenticate(disabled_token.token.get_secret_value())
        async with database.sessions() as session, session.begin():
            await session.execute(
                update(APIPrincipalModel)
                .where(APIPrincipalModel.id == disabled_principal.id)
                .values(status="disabled")
            )
        with pytest.raises(ReflectionJobAuthorizationError):
            await operations.retry_job(
                job_id=fixture.handler_v2_job_id,
                expected_version=stale_version,
                idempotency_key="disabled-actor-request",
                actor=disabled_actor,
            )

        await operations.retry_job(
            job_id=fixture.handler_v2_job_id,
            expected_version=stale_version,
            idempotency_key="new-cycle-backoff-request",
            actor=first_actor,
        )
        backoff_store = PostgresReflectionJobStore(
            database.sessions,
            handler_version="reflection-v2",
            max_attempts=3,
            retry_base_seconds=10,
            retry_max_seconds=1_000,
        )
        new_cycle_work = await backoff_store.claim(
            worker_id="new-cycle-worker",
            lease_seconds=30,
        )
        assert new_cycle_work is not None
        assert new_cycle_work.job_id == fixture.handler_v2_job_id
        assert new_cycle_work.attempts == 1
        state = await backoff_store.fail(
            new_cycle_work,
            error_code="reflection_worker.scripted_failure",
        )
        assert state is ReflectionJobState.RETRY_WAIT
        async with database.sessions() as session:
            new_cycle_job = await session.get(
                OutboxJobModel,
                fixture.handler_v2_job_id,
            )
            assert new_cycle_job is not None
            assert new_cycle_job.attempts == 4
            assert new_cycle_job.attempts_in_cycle == 1
            assert new_cycle_job.available_at - new_cycle_job.updated_at == timedelta(
                seconds=10
            )

        lease_store = PostgresReflectionJobStore(
            database.sessions,
            handler_version="reflection-lease-v1",
            max_attempts=1,
        )
        old_work = await lease_store.claim(worker_id="old-lease-worker", lease_seconds=30)
        assert old_work is not None
        assert old_work.job_id == fixture.lease_job_id
        state = await lease_store.fail(
            old_work,
            error_code="reflection_worker.scripted_failure",
        )
        assert state is ReflectionJobState.DEAD_LETTER
        lease_version = await _job_version(database, fixture.lease_job_id)
        await operations.retry_job(
            job_id=fixture.lease_job_id,
            expected_version=lease_version,
            idempotency_key="lease-fencing-retry",
            actor=first_actor,
        )
        with pytest.raises(ReflectionJobLeaseLostError, match="stale or expired"):
            await lease_store.complete(old_work, candidates=())

        async with database.sessions() as session:
            audit_id = await session.scalar(
                select(ReflectionJobOperationAuditEventModel.id).where(
                    ReflectionJobOperationAuditEventModel.tenant_id == fixture.tenant_id
                )
            )
            assert audit_id is not None
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(
                    update(ReflectionJobOperationAuditEventModel)
                    .where(ReflectionJobOperationAuditEventModel.id == audit_id)
                    .values(action="tampered")
                )
            await session.rollback()
        async with database.sessions() as session:
            retry_request_id = await session.scalar(
                select(ReflectionJobRetryRequestModel.id).where(
                    ReflectionJobRetryRequestModel.tenant_id == fixture.tenant_id
                )
            )
            assert retry_request_id is not None
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(
                    update(ReflectionJobRetryRequestModel)
                    .where(ReflectionJobRetryRequestModel.id == retry_request_id)
                    .values(outcome="conflict")
                )
            await session.rollback()
    finally:
        await _cleanup(database, fixture.tenant_id, fixture.other_tenant_id)


async def _create_fixture(database: Database) -> _OperationsFixture:
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    tenant_slug = f"operations-{tenant_id.hex[:10]}"
    agent_a_id = uuid4()
    agent_b_id = uuid4()
    other_agent_id = uuid4()
    agent_a_key = f"operations-agent-a-{agent_a_id.hex[:8]}"
    agent_b_key = f"operations-agent-b-{agent_b_id.hex[:8]}"
    now = utc_now()
    async with database.sessions() as session, session.begin():
        session.add_all(
            [
                TenantModel(id=tenant_id, slug=tenant_slug, name="Operations Tenant"),
                TenantModel(
                    id=other_tenant_id,
                    slug=f"operations-other-{other_tenant_id.hex[:10]}",
                    name="Operations Other Tenant",
                ),
            ]
        )
        await session.flush()
        agents = (
            AgentModel(
                id=agent_a_id,
                tenant_id=tenant_id,
                agent_key=agent_a_key,
                name="Operations Agent A",
                domain_id=agent_a_key,
            ),
            AgentModel(
                id=agent_b_id,
                tenant_id=tenant_id,
                agent_key=agent_b_key,
                name="Operations Agent B",
                domain_id=agent_b_key,
            ),
            AgentModel(
                id=other_agent_id,
                tenant_id=other_tenant_id,
                agent_key=f"operations-other-agent-{other_agent_id.hex[:8]}",
                name="Operations Other Agent",
                domain_id="operations-other",
            ),
        )
        session.add_all(agents)
        await session.flush()
        versions: dict[UUID, UUID] = {}
        for agent in agents:
            version_id = uuid4()
            versions[agent.id] = version_id
            session.add(
                AgentVersionModel(
                    id=version_id,
                    tenant_id=agent.tenant_id,
                    agent_id=agent.id,
                    version="1.0.0",
                    instructions="Operate safely.",
                    memory_namespace=f"operations-{agent.id}",
                    configuration={},
                )
            )
        await session.flush()

        dead_job_id = await _add_job(
            session,
            tenant_id=tenant_id,
            agent_id=agent_a_id,
            agent_version_id=versions[agent_a_id],
            handler_version="reflection-v1",
            status=ReflectionJobState.DEAD_LETTER,
            created_at=now - timedelta(minutes=8),
        )
        pending_job_id = await _add_job(
            session,
            tenant_id=tenant_id,
            agent_id=agent_a_id,
            agent_version_id=versions[agent_a_id],
            handler_version="reflection-v1",
            status=ReflectionJobState.PENDING,
            created_at=now - timedelta(minutes=7),
        )
        agent_b_job_id = await _add_job(
            session,
            tenant_id=tenant_id,
            agent_id=agent_b_id,
            agent_version_id=versions[agent_b_id],
            handler_version="reflection-v1",
            status=ReflectionJobState.DEAD_LETTER,
            created_at=now - timedelta(minutes=6),
        )
        handler_v2_job_id = await _add_job(
            session,
            tenant_id=tenant_id,
            agent_id=agent_a_id,
            agent_version_id=versions[agent_a_id],
            handler_version="reflection-v2",
            status=ReflectionJobState.DEAD_LETTER,
            created_at=now - timedelta(minutes=5),
        )
        other_tenant_job_id = await _add_job(
            session,
            tenant_id=other_tenant_id,
            agent_id=other_agent_id,
            agent_version_id=versions[other_agent_id],
            handler_version="reflection-v1",
            status=ReflectionJobState.DEAD_LETTER,
            created_at=now - timedelta(minutes=4),
        )
        same_key_job_id = await _add_job(
            session,
            tenant_id=tenant_id,
            agent_id=agent_a_id,
            agent_version_id=versions[agent_a_id],
            handler_version="reflection-v1",
            status=ReflectionJobState.DEAD_LETTER,
            created_at=now - timedelta(minutes=3),
        )
        different_key_job_id = await _add_job(
            session,
            tenant_id=tenant_id,
            agent_id=agent_a_id,
            agent_version_id=versions[agent_a_id],
            handler_version="reflection-v1",
            status=ReflectionJobState.DEAD_LETTER,
            created_at=now - timedelta(minutes=2),
        )
        lease_job_id = await _add_job(
            session,
            tenant_id=tenant_id,
            agent_id=agent_a_id,
            agent_version_id=versions[agent_a_id],
            handler_version="reflection-lease-v1",
            status=ReflectionJobState.PENDING,
            created_at=now - timedelta(minutes=1),
            max_attempts=1,
        )
    return _OperationsFixture(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        other_tenant_id=other_tenant_id,
        agent_a_id=agent_a_id,
        agent_a_key=agent_a_key,
        agent_b_id=agent_b_id,
        agent_b_key=agent_b_key,
        dead_job_id=dead_job_id,
        pending_job_id=pending_job_id,
        agent_b_job_id=agent_b_job_id,
        handler_v2_job_id=handler_v2_job_id,
        other_tenant_job_id=other_tenant_job_id,
        same_key_job_id=same_key_job_id,
        different_key_job_id=different_key_job_id,
        lease_job_id=lease_job_id,
    )


async def _add_job(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: UUID,
    agent_version_id: UUID,
    handler_version: str,
    status: ReflectionJobState,
    created_at: datetime,
    max_attempts: int = 3,
) -> UUID:
    run_id = uuid4()
    job_id = uuid4()
    session.add(
        RunModel(
            id=run_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            status="succeeded",
            task=_TASK_SECRET,
            output=_OUTPUT_SECRET,
            error=None,
            idempotency_key=f"operations-run-{run_id}",
            metadata_json={"steps": 1},
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await session.flush()
    attempts = max_attempts if status is ReflectionJobState.DEAD_LETTER else 0
    session.add(
        OutboxJobModel(
            id=job_id,
            tenant_id=tenant_id,
            run_id=run_id,
            job_type="run_reflection",
            handler_version=handler_version,
            status=status.value,
            payload={"schema_version": 1},
            result_metadata={"unsafe_candidate_detail": "must not be projected"},
            version=1,
            attempts=attempts,
            attempts_in_cycle=attempts,
            max_attempts=max_attempts,
            available_at=created_at,
            last_error_code=(
                "reflection_worker.scripted_failure"
                if status is ReflectionJobState.DEAD_LETTER
                else None
            ),
            completed_at=(created_at if status is ReflectionJobState.DEAD_LETTER else None),
            created_at=created_at,
            updated_at=created_at,
        )
    )
    return job_id


async def _job_version(database: Database, job_id: UUID) -> int:
    async with database.sessions() as session:
        version = await session.scalar(
            select(OutboxJobModel.version).where(OutboxJobModel.id == job_id)
        )
    assert version is not None
    return version


async def _cleanup(database: Database, *tenant_ids: UUID) -> None:
    async with database.sessions() as session, session.begin():
        await session.execute(delete(TenantModel).where(TenantModel.id.in_(tenant_ids)))
    await database.dispose()
