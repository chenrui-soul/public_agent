from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text

from public_agent.config import Settings
from public_agent.operations.application import (
    build_reflection_capacity_governance_application,
)
from public_agent.operations.capacity import (
    ReflectionCapacityBacklog,
    ReflectionCapacityReport,
    ReflectionCapacityStatus,
    ReflectionCapacityThresholds,
    ReflectionCapacityWorkers,
)
from public_agent.operations.capacity_governance import (
    ReflectionCapacityChangeStatus,
    ReflectionCapacityGovernanceConflictError,
    ReflectionCapacityGovernanceNotFoundError,
    ReflectionCapacityGovernanceNotReadyError,
)
from public_agent.operations.capacity_history import (
    ReflectionCapacityCalibrationOptions,
    ReflectionCapacityTrendBucket,
)
from public_agent.operations.outbox_retention import OutboxRetentionPolicy
from public_agent.storage.capacity_governance import (
    PostgresReflectionCapacityGovernance,
)
from public_agent.storage.capacity_history import PostgresReflectionCapacityHistory
from public_agent.storage.database import Database
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    OutboxJobArchiveModel,
    OutboxJobModel,
    ReflectionCapacityCalibrationModel,
    ReflectionCapacityChangeRequestModel,
    ReflectionCapacityObservationModel,
    ReflectionCapacityPolicyModel,
    ReflectionJobRetryRequestModel,
    RunModel,
    TenantModel,
)
from public_agent.storage.outbox import REFLECTION_JOB_TYPE
from public_agent.storage.outbox_retention import PostgresOutboxRetention

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


def _thresholds() -> ReflectionCapacityThresholds:
    return ReflectionCapacityThresholds(
        stale_after_seconds=180,
        minimum_workers=1,
        maximum_workers=10,
        target_jobs_per_worker=20,
        ready_warning=100,
        ready_critical=500,
        oldest_warning_seconds=300,
        oldest_critical_seconds=1_800,
        dead_letter_warning=1,
        dead_letter_critical=10,
    )


def _report(*, handler_version: str, observed_at: datetime, ready: int):
    return ReflectionCapacityReport(
        status=(
            ReflectionCapacityStatus.WARNING
            if ready > 0
            else ReflectionCapacityStatus.HEALTHY
        ),
        handler_version=handler_version,
        observed_at=observed_at,
        backlog=ReflectionCapacityBacklog(
            pending=ready,
            processing=0,
            retry_wait=0,
            ready=ready,
            succeeded=2,
            dead_letter=0,
            oldest_available_at=None,
            oldest_ready_age_seconds=float(ready),
        ),
        workers=ReflectionCapacityWorkers(
            registered=1,
            active=1,
            stale=0,
            stopped=0,
            errored=0,
            processed_jobs=2,
            oldest_last_seen_at=observed_at,
            newest_last_seen_at=observed_at,
        ),
        recommended_workers=1,
        scale_delta=0,
        reasons=("reflection_capacity.scale_out_recommended",) if ready else (),
        thresholds=_thresholds(),
    )


@pytest.mark.asyncio
async def test_capacity_observations_trend_and_real_history_calibration() -> None:
    database = Database(Settings().database_url)
    handler_version = f"capacity-history-{uuid4().hex[:8]}"
    tenant_id = await _create_terminal_jobs(
        database,
        handler_version=handler_version,
        durations=(100, 200, 1_000),
    )
    history = PostgresReflectionCapacityHistory(database.sessions)
    observed_at = datetime(2026, 8, 25, 6, 0, tzinfo=UTC)
    try:
        await history.record_observation(
            _report(handler_version=handler_version, observed_at=observed_at, ready=0)
        )
        await history.record_observation(
            _report(
                handler_version=handler_version,
                observed_at=observed_at + timedelta(hours=1),
                ready=5,
            )
        )
        trend = await history.trend(
            handler_version=handler_version,
            since=observed_at - timedelta(hours=1),
            bucket=ReflectionCapacityTrendBucket.HOUR,
            limit=10,
        )
        assert [point.maximum_ready for point in trend.points] == [0, 5]

        settings = Settings(
            _env_file=None,
            reflection_capacity_minimum_workers=1,
            reflection_capacity_maximum_workers=10,
        )
        application = build_reflection_capacity_governance_application(
            settings,
            handler_version=handler_version,
            database=database,
        )
        calibration = await application.calibrate(
            ReflectionCapacityCalibrationOptions(
                lookback_hours=8_760,
                minimum_samples=3,
                maximum_samples=3,
                target_drain_seconds=300,
                target_utilization=0.70,
            )
        )
        assert calibration.calibration_id is not None
        assert calibration.p95_processing_ms == 1_000
        assert calibration.recommendation.target_jobs_per_worker == 210
        await application.aclose()
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(ReflectionCapacityCalibrationModel).where(
                    ReflectionCapacityCalibrationModel.handler_version
                    == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityObservationModel).where(
                    ReflectionCapacityObservationModel.handler_version
                    == handler_version
                )
            )
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        await database.dispose()


@pytest.mark.asyncio
async def test_partitioned_archive_and_guarded_prune_preserve_retry_history() -> None:
    database = Database(Settings().database_url)
    handler_version = f"retention-{uuid4().hex[:8]}"
    tenant_id = await _create_terminal_jobs(
        database,
        handler_version=handler_version,
        durations=(500,),
        completed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    retention = PostgresOutboxRetention(
        database.sessions,
        handler_version=handler_version,
    )
    policy = OutboxRetentionPolicy(
        archive_after_days=1,
        purge_after_days=2,
        batch_size=10,
        maximum_batches=2,
    )
    try:
        preview = await retention.preview(policy)
        assert preview.archive_eligible == 1
        archived = await retention.maintain(policy, prune=False)
        assert archived.archived_jobs == 1
        async with database.sessions() as session:
            job = await session.scalar(
                select(OutboxJobModel).where(
                    OutboxJobModel.handler_version == handler_version
                )
            )
            partition_name = await session.scalar(
                text(
                    "SELECT tableoid::regclass::text FROM outbox_job_archives "
                    "WHERE handler_version = :handler_version"
                ),
                {"handler_version": handler_version},
            )
            agent_id = await session.scalar(
                select(RunModel.agent_id).where(RunModel.id == job.run_id)
            ) if job is not None else None
        assert job is not None
        assert agent_id is not None
        assert partition_name == "outbox_job_archives_2020_2030"

        async with database.sessions() as session, session.begin():
            session.add(
                ReflectionJobRetryRequestModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    job_id=job.id,
                    run_id=job.run_id,
                    actor_principal_id=uuid4(),
                    idempotency_key_hash="a" * 64,
                    expected_version=job.version,
                    previous_status="dead_letter",
                    result_status="pending",
                    result_version=job.version + 1,
                    outcome="success",
                    error_code=None,
                )
            )
        blocked = await retention.maintain(policy, prune=True)
        assert blocked.purged_jobs == 0
        assert blocked.after.purge_blocked_by_retry_requests == 1

        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(ReflectionJobRetryRequestModel).where(
                    ReflectionJobRetryRequestModel.job_id == job.id
                )
            )
        pruned = await retention.maintain(policy, prune=True)
        assert pruned.purged_jobs == 1
        async with database.sessions() as session:
            assert await session.get(OutboxJobModel, job.id) is None
            archived_row = await session.scalar(
                select(OutboxJobArchiveModel).where(
                    OutboxJobArchiveModel.id == job.id
                )
            )
            assert archived_row is not None
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(OutboxJobArchiveModel).where(
                    OutboxJobArchiveModel.tenant_id == tenant_id
                )
            )
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        await database.dispose()


@pytest.mark.asyncio
async def test_capacity_policy_governance_enforces_window_cooldown_and_exact_rollback() -> None:
    database = Database(Settings().database_url)
    handler_version = f"capacity-policy-{uuid4().hex[:8]}"
    governance = PostgresReflectionCapacityGovernance(
        database.sessions,
        handler_version=handler_version,
    )
    history = PostgresReflectionCapacityHistory(database.sessions)
    calibration_ids = (uuid4(), uuid4(), uuid4())
    fallback = _thresholds()
    now = datetime.now(UTC)
    try:
        async with database.sessions() as session, session.begin():
            for index, calibration_id in enumerate(calibration_ids, start=1):
                session.add(
                    ReflectionCapacityCalibrationModel(
                        id=calibration_id,
                        job_type=REFLECTION_JOB_TYPE,
                        handler_version=handler_version,
                        window_started_at=now - timedelta(hours=1),
                        window_ended_at=now,
                        sample_count=30,
                        succeeded_count=30,
                        dead_letter_count=0,
                        p50_processing_ms=100,
                        p95_processing_ms=200,
                        p99_processing_ms=300,
                        observed_jobs_per_hour=30,
                        recommendation={
                            "target_jobs_per_worker": 20 + index,
                            "ready_warning": 100 + index,
                            "ready_critical": 500 + index,
                            "oldest_warning_seconds": 300,
                            "oldest_critical_seconds": 900,
                        },
                        options={"minimum_samples": 30},
                    )
                )

        first = await governance.create_change_request(
            calibration_id=calibration_ids[0],
            fallback_thresholds=fallback,
            requested_by="requester@example.com",
            window_required_seconds=60,
            window_minimum_observations=3,
        )
        second = await governance.create_change_request(
            calibration_id=calibration_ids[1],
            fallback_thresholds=fallback,
            requested_by="requester@example.com",
            window_required_seconds=60,
            window_minimum_observations=3,
        )
        rejected_request = await governance.create_change_request(
            calibration_id=calibration_ids[2],
            fallback_thresholds=fallback,
            requested_by="requester@example.com",
            window_required_seconds=60,
            window_minimum_observations=3,
        )
        baseline = await governance.active_policy()
        assert baseline is not None
        assert baseline.policy_version == 1
        assert baseline.thresholds == fallback
        assert first.base_policy_id == baseline.id == second.base_policy_id
        assert rejected_request.base_policy_id == baseline.id

        with pytest.raises(ReflectionCapacityGovernanceConflictError):
            await governance.publish(
                request_id=first.id,
                expected_version=first.version,
                published_by="publisher@example.com",
                cooldown_seconds=60,
            )

        window_start = now - timedelta(seconds=121)
        async with database.sessions() as session, session.begin():
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityChangeRequestModel).where(
                        ReflectionCapacityChangeRequestModel.id.in_(
                            (first.id, second.id, rejected_request.id)
                        )
                    )
                )
            )
            for row in rows:
                row.window_started_at = window_start
        for offset, ready in ((121, 1), (60, 1), (0, 1)):
            await history.record_observation(
                _report(
                    handler_version=handler_version,
                    observed_at=now - timedelta(seconds=offset),
                    ready=ready,
                )
            )

        first = await governance.validate_window(
            request_id=first.id,
            expected_version=1,
        )
        second = await governance.validate_window(
            request_id=second.id,
            expected_version=1,
        )
        rejected_request = await governance.validate_window(
            request_id=rejected_request.id,
            expected_version=1,
        )
        rejected_request = await governance.reject(
            request_id=rejected_request.id,
            expected_version=2,
            rejected_by="reviewer@example.com",
        )
        assert rejected_request.status is ReflectionCapacityChangeStatus.REJECTED
        assert rejected_request.rejected_by == "reviewer@example.com"
        assert rejected_request.approved_by is None
        assert first.status is ReflectionCapacityChangeStatus.AWAITING_APPROVAL
        with pytest.raises(ReflectionCapacityGovernanceConflictError):
            await governance.approve(
                request_id=first.id,
                expected_version=2,
                approved_by="requester@example.com",
            )
        first = await governance.approve(
            request_id=first.id,
            expected_version=2,
            approved_by="reviewer@example.com",
        )
        second = await governance.approve(
            request_id=second.id,
            expected_version=2,
            approved_by="reviewer@example.com",
        )
        with pytest.raises(ReflectionCapacityGovernanceConflictError):
            await governance.approve(
                request_id=first.id,
                expected_version=2,
                approved_by="reviewer@example.com",
            )

        first = await governance.publish(
            request_id=first.id,
            expected_version=3,
            published_by="publisher@example.com",
            cooldown_seconds=60,
        )
        assert first.status is ReflectionCapacityChangeStatus.COOLING_DOWN
        active = await governance.active_policy()
        assert active is not None
        assert active.id == first.published_policy_id
        assert active.previous_policy_id == baseline.id
        replayed = await governance.create_change_request(
            calibration_id=calibration_ids[0],
            fallback_thresholds=fallback,
            requested_by="requester@example.com",
            window_required_seconds=60,
            window_minimum_observations=3,
        )
        assert replayed.id == first.id
        assert replayed.version == first.version
        with pytest.raises(ReflectionCapacityGovernanceConflictError):
            await governance.publish(
                request_id=second.id,
                expected_version=3,
                published_by="publisher@example.com",
                cooldown_seconds=60,
            )

        other_handler = PostgresReflectionCapacityGovernance(
            database.sessions,
            handler_version=f"other-{uuid4().hex[:8]}",
        )
        with pytest.raises(ReflectionCapacityGovernanceNotFoundError):
            await other_handler.get_change_request(first.id)

        published_at = now - timedelta(seconds=120)
        async with database.sessions() as session, session.begin():
            row = await session.get(ReflectionCapacityChangeRequestModel, first.id)
            assert row is not None
            row.published_at = published_at
            row.cooldown_until = now - timedelta(seconds=1)
        with pytest.raises(ReflectionCapacityGovernanceConflictError):
            await governance.publish(
                request_id=second.id,
                expected_version=3,
                published_by="publisher@example.com",
                cooldown_seconds=60,
            )
        with pytest.raises(ReflectionCapacityGovernanceNotReadyError) as exc_info:
            await governance.review(
                request_id=first.id,
                expected_version=4,
                reviewed_by="reviewer@example.com",
            )
        assert exc_info.value.code == (
            "reflection_capacity_policy.review_samples_insufficient"
        )
        unchanged = await governance.get_change_request(first.id)
        assert unchanged.status is ReflectionCapacityChangeStatus.COOLING_DOWN
        assert unchanged.version == 4

        await history.record_observation(
            _report(
                handler_version=handler_version,
                observed_at=published_at,
                ready=1_000,
            )
        )
        reviewed = await governance.review(
            request_id=first.id,
            expected_version=4,
            reviewed_by="reviewer@example.com",
        )
        assert reviewed.status is ReflectionCapacityChangeStatus.INEFFECTIVE
        assert reviewed.effect is not None
        assert "reflection_capacity_policy.ready_regressed" in reviewed.effect.reasons

        rolled_back = await governance.rollback(
            request_id=first.id,
            expected_version=5,
            rolled_back_by="publisher@example.com",
            reason="post-release capacity regression",
        )
        assert rolled_back.status is ReflectionCapacityChangeStatus.ROLLED_BACK
        restored = await governance.active_policy()
        assert restored is not None
        assert restored.id == baseline.id
        assert restored.thresholds == fallback
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(ReflectionCapacityChangeRequestModel).where(
                    ReflectionCapacityChangeRequestModel.handler_version
                    == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityPolicyModel).where(
                    ReflectionCapacityPolicyModel.handler_version == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityObservationModel).where(
                    ReflectionCapacityObservationModel.handler_version
                    == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityCalibrationModel).where(
                    ReflectionCapacityCalibrationModel.handler_version
                    == handler_version
                )
            )
        await database.dispose()


async def _create_terminal_jobs(
    database: Database,
    *,
    handler_version: str,
    durations: tuple[int, ...],
    completed_at: datetime | None = None,
) -> UUID:
    tenant_id = uuid4()
    agent_id = uuid4()
    version_id = uuid4()
    now = completed_at or datetime.now(UTC) - timedelta(minutes=1)
    async with database.sessions() as session, session.begin():
        session.add(
            TenantModel(
                id=tenant_id,
                slug=f"capacity-{tenant_id.hex[:10]}",
                name="Capacity Governance Tenant",
            )
        )
        await session.flush()
        session.add(
            AgentModel(
                id=agent_id,
                tenant_id=tenant_id,
                agent_key=f"capacity-{agent_id.hex[:10]}",
                name="Capacity Governance Agent",
                domain_id="capacity-governance",
            )
        )
        await session.flush()
        session.add(
            AgentVersionModel(
                id=version_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                version="1.0.0",
                instructions="Capacity governance test fixture.",
                memory_namespace="capacity-governance",
                configuration={},
            )
        )
        await session.flush()
        for index, duration in enumerate(durations):
            run_id = uuid4()
            run = RunModel(
                    id=run_id,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    agent_version_id=version_id,
                    status="succeeded",
                    task="capacity calibration fixture",
                    output="ok",
                    idempotency_key=f"capacity-{run_id}",
                    metadata_json={},
                    created_at=now - timedelta(seconds=duration / 1_000),
                    updated_at=now,
                )
            session.add(run)
            await session.flush()
            session.add(
                OutboxJobModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    run_id=run_id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=handler_version,
                    status="succeeded",
                    payload={"schema_version": 1},
                    result_metadata={"candidate_count": 0, "candidate_ids": []},
                    version=2,
                    attempts=1,
                    attempts_in_cycle=1,
                    max_attempts=5,
                    available_at=now,
                    last_started_at=now - timedelta(milliseconds=duration),
                    last_processing_duration_ms=duration,
                    total_processing_duration_ms=duration,
                    completed_at=now + timedelta(milliseconds=index),
                    created_at=now - timedelta(seconds=duration / 1_000),
                    updated_at=now,
                )
            )
    return tenant_id
