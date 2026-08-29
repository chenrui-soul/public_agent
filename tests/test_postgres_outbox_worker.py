from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from public_agent.config import Settings
from public_agent.core.types import AgentSpec, RunContext, RunResult, RunStatus
from public_agent.growth.pipeline import (
    EvidenceBasedCandidateEvaluator,
    ExtractedKnowledge,
    KnowledgeSedimentationPipeline,
    ReflectionContext,
    SuccessfulRunKnowledgeExtractor,
)
from public_agent.growth.service import LearningService
from public_agent.storage.database import Database
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    LearningCandidateModel,
    OutboxJobModel,
    ReflectionWorkerHeartbeatModel,
    RunEventModel,
    TenantModel,
)
from public_agent.storage.outbox import PostgresReflectionJobStore
from public_agent.storage.repositories import (
    PostgresKnowledgeAssetPublisher,
    PostgresLearningStore,
)
from public_agent.storage.runs import PostgresRunPersistence
from public_agent.workers import (
    ReflectionJobLeaseLostError,
    ReflectionJobState,
    ReflectionWorker,
    ReflectionWorkerLifecycleState,
    ReflectionWorkerRegistrationLostError,
)

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


class SecretBearingFailureExtractor:
    async def extract(
        self,
        context: ReflectionContext,
    ) -> tuple[ExtractedKnowledge, ...]:
        del context
        raise RuntimeError(
            "Authorization: Bearer public_agent_sensitive-token provider body"
        )


@pytest.mark.asyncio
async def test_terminal_run_transactionally_enqueues_safe_idempotent_reflection_job() -> None:
    database = Database(Settings().database_url)
    tenant_id, run_id, _, _, _ = await _create_terminal_run(database)
    try:
        async with database.sessions() as session:
            jobs = tuple(
                await session.scalars(
                    select(OutboxJobModel).where(OutboxJobModel.run_id == run_id)
                )
            )
        assert len(jobs) == 1
        assert jobs[0].status == ReflectionJobState.PENDING.value
        assert jobs[0].payload == {"schema_version": 1}
        serialized = json.dumps(jobs[0].payload, sort_keys=True)
        for forbidden in (
            "checkpoint",
            "provider_state",
            "authorization",
            "public_agent_",
            "Reusable async reflection output",
        ):
            assert forbidden not in serialized

        store = PostgresReflectionJobStore(database.sessions)
        replayed_id = await store.ensure_job(run_id=run_id)
        assert replayed_id == jobs[0].id
        async with database.sessions() as session:
            count = await session.scalar(
                select(func.count(OutboxJobModel.id)).where(
                    OutboxJobModel.run_id == run_id
                )
            )
        assert count == 1
    finally:
        await _cleanup(database, tenant_id)


@pytest.mark.asyncio
async def test_reflection_job_rejects_cross_tenant_run_pair() -> None:
    database = Database(Settings().database_url)
    tenant_id, run_id, _, _, _ = await _create_terminal_run(database)
    other_tenant_id = uuid4()
    try:
        async with database.sessions() as session, session.begin():
            session.add(
                TenantModel(
                    id=other_tenant_id,
                    slug=f"outbox-other-{other_tenant_id.hex[:10]}",
                    name="Outbox Other Tenant",
                )
            )

        with pytest.raises(IntegrityError) as exc_info:
            async with database.sessions() as session, session.begin():
                session.add(
                    OutboxJobModel(
                        id=uuid4(),
                        tenant_id=other_tenant_id,
                        run_id=run_id,
                        job_type="cross_tenant_probe",
                        handler_version="reflection-scope-v1",
                        status=ReflectionJobState.PENDING.value,
                        payload={"schema_version": 1},
                        result_metadata={},
                        attempts=0,
                        max_attempts=1,
                    )
                )
                await session.flush()
        assert "fk_outbox_jobs_run_scope" in str(exc_info.value.orig)
    finally:
        await _cleanup(database, tenant_id, other_tenant_id)


@pytest.mark.asyncio
async def test_worker_registration_fences_replaced_instance_and_reports_backlog() -> None:
    database = Database(Settings().database_url)
    tenant_id, _, _, _, _ = await _create_terminal_run(database)
    store = PostgresReflectionJobStore(database.sessions)
    worker_id = f"runtime-worker-{uuid4().hex[:10]}"
    try:
        pending = await store.backlog_snapshot()
        assert pending.pending == 1
        assert pending.processing == 0
        assert pending.oldest_available_at is not None

        first = await store.register_worker(worker_id=worker_id)
        second = await store.register_worker(worker_id=worker_id)
        assert first.instance_token != second.instance_token
        with pytest.raises(
            ReflectionWorkerRegistrationLostError,
            match="registration was replaced",
        ):
            await store.heartbeat_worker(
                first,
                state=ReflectionWorkerLifecycleState.RUNNING,
                processed_jobs=0,
                last_result=None,
            )

        claimed = await store.claim(worker_id=worker_id, lease_seconds=30)
        assert claimed is not None
        processing = await store.backlog_snapshot()
        assert processing.pending == 0
        assert processing.processing == 1
        await store.heartbeat_worker(
            second,
            state=ReflectionWorkerLifecycleState.RUNNING,
            processed_jobs=0,
            last_result=None,
        )
        await store.stop_worker(
            second,
            processed_jobs=0,
            last_result=None,
            error_code=None,
        )
        async with database.sessions() as session:
            heartbeat = await session.get(ReflectionWorkerHeartbeatModel, worker_id)
        assert heartbeat is not None
        assert heartbeat.status == ReflectionWorkerLifecycleState.STOPPED.value
        assert heartbeat.stopped_at is not None
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(ReflectionWorkerHeartbeatModel).where(
                    ReflectionWorkerHeartbeatModel.worker_id == worker_id
                )
            )
        await _cleanup(database, tenant_id)


@pytest.mark.asyncio
async def test_capacity_snapshot_isolates_handler_and_classifies_worker_fleet() -> None:
    database = Database(Settings().database_url)
    tenant_id, run_id, _, _, _ = await _create_terminal_run(database)
    handler_version = f"reflection-capacity-{uuid4().hex[:8]}"
    store = PostgresReflectionJobStore(
        database.sessions,
        handler_version=handler_version,
    )
    observed_at = datetime(2026, 8, 25, 6, 0, tzinfo=UTC)
    worker_ids = [f"capacity-{uuid4().hex[:10]}" for _ in range(5)]
    try:
        await store.ensure_job(run_id=run_id)
        claimed = await store.claim(worker_id="capacity-probe", lease_seconds=30)
        assert claimed is not None
        rows = (
            ReflectionWorkerHeartbeatModel(
                worker_id=worker_ids[0],
                instance_token=uuid4(),
                job_type="run_reflection",
                handler_version=handler_version,
                status=ReflectionWorkerLifecycleState.RUNNING.value,
                processed_jobs=1,
                last_error_code=None,
                started_at=observed_at - timedelta(minutes=10),
                last_seen_at=observed_at - timedelta(seconds=10),
                stopped_at=None,
            ),
            ReflectionWorkerHeartbeatModel(
                worker_id=worker_ids[1],
                instance_token=uuid4(),
                job_type="run_reflection",
                handler_version=handler_version,
                status=ReflectionWorkerLifecycleState.IDLE.value,
                processed_jobs=2,
                last_error_code=None,
                started_at=observed_at - timedelta(minutes=10),
                last_seen_at=observed_at - timedelta(seconds=120),
                stopped_at=None,
            ),
            ReflectionWorkerHeartbeatModel(
                worker_id=worker_ids[2],
                instance_token=uuid4(),
                job_type="run_reflection",
                handler_version=handler_version,
                status=ReflectionWorkerLifecycleState.STOPPED.value,
                processed_jobs=3,
                last_error_code="reflection_worker.clean_stop",
                started_at=observed_at - timedelta(minutes=20),
                last_seen_at=observed_at - timedelta(seconds=600),
                stopped_at=observed_at - timedelta(seconds=600),
            ),
            ReflectionWorkerHeartbeatModel(
                worker_id=worker_ids[3],
                instance_token=uuid4(),
                job_type="run_reflection",
                handler_version=handler_version,
                status=ReflectionWorkerLifecycleState.RUNNING.value,
                processed_jobs=4,
                last_error_code="reflection_worker.scripted_failure",
                started_at=observed_at - timedelta(minutes=10),
                last_seen_at=observed_at - timedelta(seconds=5),
                stopped_at=None,
            ),
            ReflectionWorkerHeartbeatModel(
                worker_id=worker_ids[4],
                instance_token=uuid4(),
                job_type="run_reflection",
                handler_version="reflection-v1",
                status=ReflectionWorkerLifecycleState.RUNNING.value,
                processed_jobs=99,
                last_error_code=None,
                started_at=observed_at - timedelta(minutes=10),
                last_seen_at=observed_at,
                stopped_at=None,
            ),
        )
        async with database.sessions() as session, session.begin():
            session.add_all(rows)

        with patch("public_agent.storage.outbox.utc_now", return_value=observed_at):
            snapshot = await store.capacity_snapshot(stale_after_seconds=60)

        assert snapshot.observed_at == observed_at
        assert snapshot.backlog.pending == 0
        assert snapshot.backlog.processing == 1
        assert snapshot.workers.registered == 4
        assert snapshot.workers.active == 2
        assert snapshot.workers.stale == 1
        assert snapshot.workers.stopped == 1
        assert snapshot.workers.errored == 1
        assert snapshot.workers.processed_jobs == 10
        assert snapshot.workers.oldest_last_seen_at == observed_at - timedelta(
            seconds=600
        )
        assert snapshot.workers.newest_last_seen_at == observed_at - timedelta(seconds=5)
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(ReflectionWorkerHeartbeatModel).where(
                    ReflectionWorkerHeartbeatModel.worker_id.in_(worker_ids)
                )
            )
        await _cleanup(database, tenant_id)


@pytest.mark.asyncio
async def test_capacity_snapshot_rejects_invalid_stale_boundary() -> None:
    database = Database(Settings().database_url)
    store = PostgresReflectionJobStore(database.sessions)
    try:
        with pytest.raises(ValueError, match="stale_after_seconds"):
            await store.capacity_snapshot(stale_after_seconds=4)
        with pytest.raises(ValueError, match="stale_after_seconds"):
            await store.capacity_snapshot(stale_after_seconds=3_601)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_reflection_job_claim_reclaims_expired_lease_and_fences_old_worker() -> None:
    database = Database(Settings().database_url)
    tenant_id, run_id, _, _, _ = await _create_terminal_run(database)
    store = PostgresReflectionJobStore(database.sessions)
    try:
        claims = await asyncio.gather(
            store.claim(worker_id="worker-a", lease_seconds=5),
            store.claim(worker_id="worker-b", lease_seconds=5),
        )
        claimed = [item for item in claims if item is not None]
        assert len(claimed) == 1
        first = claimed[0]
        assert first.run_id == run_id

        async with database.sessions() as session:
            row = await session.get(OutboxJobModel, first.job_id)
            assert row is not None and row.lease_expires_at is not None
            original_expiry = row.lease_expires_at
        await store.heartbeat(first, lease_seconds=10)
        async with database.sessions() as session:
            row = await session.get(OutboxJobModel, first.job_id)
            assert row is not None and row.lease_expires_at is not None
            assert row.lease_expires_at > original_expiry
            after_expiry = row.lease_expires_at + timedelta(seconds=1)
        with patch("public_agent.storage.outbox.utc_now", return_value=after_expiry):
            reclaimed = await store.claim(worker_id="worker-c", lease_seconds=5)
            assert reclaimed is not None
            assert reclaimed.job_id == first.job_id
            assert reclaimed.lease_token != first.lease_token
            assert reclaimed.attempts == 2
            with pytest.raises(ReflectionJobLeaseLostError, match="stale or expired"):
                await store.complete(first, candidates=())
            await store.complete(reclaimed, candidates=())
    finally:
        await _cleanup(database, tenant_id)


@pytest.mark.asyncio
async def test_reflection_job_records_real_processing_duration_for_calibration() -> None:
    database = Database(Settings().database_url)
    tenant_id, _, _, _, _ = await _create_terminal_run(database)
    store = PostgresReflectionJobStore(
        database.sessions,
        handler_version="reflection-duration-v1",
    )
    try:
        async with database.sessions() as session:
            run_id = await session.scalar(
                select(OutboxJobModel.run_id).where(
                    OutboxJobModel.tenant_id == tenant_id
                )
            )
        assert run_id is not None
        await store.ensure_job(run_id=run_id)
        started_at = datetime.now(UTC) + timedelta(seconds=1)
        with patch("public_agent.storage.outbox.utc_now", return_value=started_at):
            work = await store.claim(worker_id="duration-worker", lease_seconds=30)
        assert work is not None
        with patch(
            "public_agent.storage.outbox.utc_now",
            return_value=started_at + timedelta(milliseconds=1_250),
        ):
            await store.complete(work, candidates=())

        async with database.sessions() as session:
            row = await session.get(OutboxJobModel, work.job_id)
        assert row is not None
        assert row.last_started_at == started_at
        assert row.last_processing_duration_ms == 1_250
        assert row.total_processing_duration_ms == 1_250
    finally:
        await _cleanup(database, tenant_id)


@pytest.mark.asyncio
async def test_reflection_job_uses_bounded_retry_then_dead_letters_safe_error() -> None:
    database = Database(Settings().database_url)
    tenant_id, run_id, _, _, _ = await _create_terminal_run(database)
    store = PostgresReflectionJobStore(
        database.sessions,
        handler_version="reflection-retry-v1",
        max_attempts=2,
        retry_base_seconds=1,
        retry_max_seconds=2,
    )
    try:
        await store.ensure_job(run_id=run_id)
        first = await store.claim(worker_id="retry-worker", lease_seconds=5)
        assert first is not None
        state = await store.fail(
            first,
            error_code="reflection_worker.scripted_failure",
        )
        assert state is ReflectionJobState.RETRY_WAIT
        async with database.sessions() as session:
            row = await session.get(OutboxJobModel, first.job_id)
            assert row is not None
            after_backoff = row.available_at + timedelta(milliseconds=1)
        with patch("public_agent.storage.outbox.utc_now", return_value=after_backoff):
            second = await store.claim(worker_id="retry-worker", lease_seconds=5)
            assert second is not None
            assert second.attempts == 2
            state = await store.fail(
                second,
                error_code="reflection_worker.scripted_failure",
            )
        assert state is ReflectionJobState.DEAD_LETTER
        async with database.sessions() as session:
            row = await session.get(OutboxJobModel, first.job_id)
            assert row is not None
            assert row.status == ReflectionJobState.DEAD_LETTER.value
            assert row.last_error_code == "reflection_worker.scripted_failure"
            assert row.lease_token is None
            assert row.completed_at is not None
            serialized = json.dumps(row.payload, sort_keys=True)
        assert "scripted" not in serialized
    finally:
        await _cleanup(database, tenant_id)


@pytest.mark.asyncio
async def test_reflection_worker_creates_one_candidate_and_version_replay_is_idempotent() -> None:
    database = Database(Settings().database_url)
    tenant_id, run_id, _, _, _ = await _create_terminal_run(database)
    learning_store = PostgresLearningStore(database.sessions)
    pipeline = KnowledgeSedimentationPipeline(
        learning=LearningService(learning_store),
        learning_store=learning_store,
        extractor=SuccessfulRunKnowledgeExtractor(),
        evaluator=EvidenceBasedCandidateEvaluator(),
        publisher=PostgresKnowledgeAssetPublisher(database.sessions),
    )
    try:
        worker = ReflectionWorker(
            jobs=PostgresReflectionJobStore(database.sessions),
            sedimentation=pipeline,
            lease_seconds=30,
            heartbeat_seconds=5,
        )
        first = await worker.process_one(worker_id="reflection-worker-a")
        assert first is not None
        assert first.state is ReflectionJobState.SUCCEEDED
        assert len(first.candidate_ids) == 1

        replay_store = PostgresReflectionJobStore(
            database.sessions,
            handler_version="reflection-v2",
        )
        await replay_store.ensure_job(run_id=run_id)
        replay_worker = ReflectionWorker(
            jobs=replay_store,
            sedimentation=pipeline,
            lease_seconds=30,
            heartbeat_seconds=5,
        )
        replay = await replay_worker.process_one(worker_id="reflection-worker-b")
        assert replay is not None
        assert replay.state is ReflectionJobState.SUCCEEDED
        assert replay.candidate_ids == ()

        async with database.sessions() as session:
            candidate_count = await session.scalar(
                select(func.count(LearningCandidateModel.id)).where(
                        LearningCandidateModel.tenant_id == tenant_id,
                        LearningCandidateModel.evidence_run_ids.contains([str(run_id)]),
                )
            )
            jobs = tuple(
                await session.scalars(
                    select(OutboxJobModel)
                    .where(OutboxJobModel.run_id == run_id)
                    .order_by(OutboxJobModel.handler_version)
                )
            )
            event_types = tuple(
                await session.scalars(
                    select(RunEventModel.event_type)
                    .where(RunEventModel.run_id == run_id)
                    .order_by(RunEventModel.sequence)
                )
            )
        assert candidate_count == 1
        assert [job.status for job in jobs] == ["succeeded", "succeeded"]
        assert event_types.count("knowledge.candidates.created") == 2
    finally:
        await _cleanup(database, tenant_id)


@pytest.mark.asyncio
async def test_reflection_worker_dead_letter_redacts_exception_body() -> None:
    database = Database(Settings().database_url)
    tenant_id, run_id, _, _, _ = await _create_terminal_run(database)
    learning_store = PostgresLearningStore(database.sessions)
    pipeline = KnowledgeSedimentationPipeline(
        learning=LearningService(learning_store),
        learning_store=learning_store,
        extractor=SecretBearingFailureExtractor(),
        evaluator=EvidenceBasedCandidateEvaluator(),
        publisher=PostgresKnowledgeAssetPublisher(database.sessions),
    )
    store = PostgresReflectionJobStore(
        database.sessions,
        handler_version="reflection-redaction-v1",
        max_attempts=1,
    )
    try:
        await store.ensure_job(run_id=run_id)
        worker = ReflectionWorker(
            jobs=store,
            sedimentation=pipeline,
            lease_seconds=30,
            heartbeat_seconds=5,
        )
        result = await worker.process_one(worker_id="redaction-worker")
        assert result is not None
        assert result.state is ReflectionJobState.DEAD_LETTER
        assert result.error_code == "reflection_worker.runtime_error"
        async with database.sessions() as session:
            row = await session.scalar(
                select(OutboxJobModel).where(
                    OutboxJobModel.run_id == run_id,
                    OutboxJobModel.handler_version == "reflection-redaction-v1",
                )
            )
            event_payloads = tuple(
                await session.scalars(
                    select(RunEventModel.payload).where(RunEventModel.run_id == run_id)
                )
            )
        assert row is not None
        persisted = json.dumps(
            {
                "payload": row.payload,
                "result": row.result_metadata,
                "error": row.last_error_code,
                "events": event_payloads,
            },
            sort_keys=True,
        ).lower()
        assert "public_agent_sensitive" not in persisted
        assert "authorization" not in persisted
        assert "provider body" not in persisted
    finally:
        await _cleanup(database, tenant_id)


async def _create_terminal_run(
    database: Database,
) -> tuple[UUID, UUID, str, str, AgentSpec]:
    tenant_id = uuid4()
    agent_id = uuid4()
    version_id = uuid4()
    tenant_slug = f"outbox-tenant-{tenant_id.hex[:10]}"
    agent_key = f"outbox-agent-{agent_id.hex[:10]}"
    spec = AgentSpec(
        id=agent_key,
        name="Outbox Agent",
        version="1.0.0",
        instructions="Extract reusable knowledge after the run commits.",
        memory_namespace="outbox-memory",
    )
    async with database.sessions() as session, session.begin():
        session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Outbox Tenant"))
        await session.flush()
        session.add(
            AgentModel(
                id=agent_id,
                tenant_id=tenant_id,
                agent_key=agent_key,
                name=spec.name,
                domain_id=agent_key,
            )
        )
        await session.flush()
        session.add(
            AgentVersionModel(
                id=version_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                version=spec.version,
                instructions=spec.instructions,
                memory_namespace=spec.memory_namespace,
                configuration={},
            )
        )
    runs = PostgresRunPersistence(database.sessions)
    run_id = uuid4()
    context = RunContext(
        tenant_id=tenant_slug,
        user_id="outbox-test-user",
        metadata={"access_tags": ["internal"]},
    )
    handle = await runs.start(
        run_id=run_id,
        agent=spec,
        context=context,
        task="Produce a reusable answer without leaking the task into the outbox payload.",
        idempotency_key=f"outbox-{run_id}",
    )
    await runs.finish(
        RunResult(
            run_id=handle.run_id,
            status=RunStatus.SUCCEEDED,
            output="Reusable async reflection output backed by a committed run trace.",
            steps=2,
        )
    )
    return tenant_id, handle.run_id, tenant_slug, agent_key, spec


async def _cleanup(database: Database, *tenant_ids: UUID) -> None:
    async with database.sessions() as session, session.begin():
        await session.execute(
            delete(TenantModel).where(TenantModel.id.in_(tenant_ids))
        )
    await database.dispose()
