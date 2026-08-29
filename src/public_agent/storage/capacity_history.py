from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.core.types import utc_now
from public_agent.operations.capacity import ReflectionCapacityReport
from public_agent.operations.capacity_history import (
    ReflectionCapacityCalibrationReport,
    ReflectionCapacityTrendBucket,
    ReflectionCapacityTrendPoint,
    ReflectionCapacityTrendReport,
    ReflectionProcessingSample,
)
from public_agent.storage.models import (
    OutboxJobModel,
    ReflectionCapacityCalibrationModel,
    ReflectionCapacityObservationModel,
)
from public_agent.storage.outbox import REFLECTION_JOB_TYPE


class PostgresReflectionCapacityHistory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record_observation(self, report: ReflectionCapacityReport) -> None:
        statement = (
            postgres_insert(ReflectionCapacityObservationModel)
            .values(
                id=uuid4(),
                job_type=REFLECTION_JOB_TYPE,
                handler_version=report.handler_version,
                observed_at=report.observed_at,
                status=report.status.value,
                ready=report.backlog.ready,
                processing=report.backlog.processing,
                succeeded=report.backlog.succeeded,
                dead_letter=report.backlog.dead_letter,
                oldest_ready_age_seconds=report.backlog.oldest_ready_age_seconds,
                active_workers=report.workers.active,
                stale_workers=report.workers.stale,
                errored_workers=report.workers.errored,
                processed_jobs=report.workers.processed_jobs,
                recommended_workers=report.recommended_workers,
                scale_delta=report.scale_delta,
                reasons=list(report.reasons),
                thresholds=report.thresholds.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(
                constraint="uq_reflection_capacity_observations_sample"
            )
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

    async def trend(
        self,
        *,
        handler_version: str,
        since: datetime,
        bucket: ReflectionCapacityTrendBucket,
        limit: int,
    ) -> ReflectionCapacityTrendReport:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        bucket_start = func.date_trunc(
            bucket.value,
            ReflectionCapacityObservationModel.observed_at,
        ).label("bucket_started_at")
        statement = (
            select(
                bucket_start,
                func.count(ReflectionCapacityObservationModel.id),
                func.avg(ReflectionCapacityObservationModel.ready),
                func.max(ReflectionCapacityObservationModel.ready),
                func.max(
                    ReflectionCapacityObservationModel.oldest_ready_age_seconds
                ),
                func.max(ReflectionCapacityObservationModel.dead_letter),
                func.avg(ReflectionCapacityObservationModel.active_workers),
                func.max(ReflectionCapacityObservationModel.recommended_workers),
                func.count(ReflectionCapacityObservationModel.id).filter(
                    ReflectionCapacityObservationModel.status == "warning"
                ),
                func.count(ReflectionCapacityObservationModel.id).filter(
                    ReflectionCapacityObservationModel.status == "critical"
                ),
            )
            .where(
                ReflectionCapacityObservationModel.job_type == REFLECTION_JOB_TYPE,
                ReflectionCapacityObservationModel.handler_version == handler_version,
                ReflectionCapacityObservationModel.observed_at >= since,
            )
            .group_by(bucket_start)
            .order_by(bucket_start.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        points = tuple(
            ReflectionCapacityTrendPoint(
                bucket_started_at=row[0],
                sample_count=int(row[1]),
                average_ready=float(row[2]),
                maximum_ready=int(row[3]),
                maximum_oldest_ready_age_seconds=float(row[4]),
                maximum_dead_letter=int(row[5]),
                average_active_workers=float(row[6]),
                maximum_recommended_workers=int(row[7]),
                warning_samples=int(row[8]),
                critical_samples=int(row[9]),
            )
            for row in reversed(rows)
        )
        return ReflectionCapacityTrendReport(
            handler_version=handler_version,
            bucket=bucket,
            since=since,
            generated_at=utc_now(),
            points=points,
        )

    async def processing_samples(
        self,
        *,
        handler_version: str,
        since: datetime,
        limit: int,
    ) -> tuple[ReflectionProcessingSample, ...]:
        if not 3 <= limit <= 100_000:
            raise ValueError("limit must be between 3 and 100000")
        statement = (
            select(
                OutboxJobModel.completed_at,
                OutboxJobModel.status,
                OutboxJobModel.total_processing_duration_ms,
            )
            .where(
                OutboxJobModel.job_type == REFLECTION_JOB_TYPE,
                OutboxJobModel.handler_version == handler_version,
                OutboxJobModel.status.in_(("succeeded", "dead_letter")),
                OutboxJobModel.completed_at >= since,
                OutboxJobModel.total_processing_duration_ms > 0,
            )
            .order_by(OutboxJobModel.completed_at.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return tuple(
            ReflectionProcessingSample(
                completed_at=row[0],
                status=row[1],
                total_processing_duration_ms=int(row[2]),
            )
            for row in rows
            if row[0] is not None
        )

    async def record_calibration(
        self,
        report: ReflectionCapacityCalibrationReport,
    ) -> ReflectionCapacityCalibrationReport:
        calibration_id = uuid4()
        async with self._sessions() as session, session.begin():
            session.add(
                ReflectionCapacityCalibrationModel(
                    id=calibration_id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=report.handler_version,
                    window_started_at=report.window_started_at,
                    window_ended_at=report.window_ended_at,
                    sample_count=report.sample_count,
                    succeeded_count=report.succeeded_count,
                    dead_letter_count=report.dead_letter_count,
                    p50_processing_ms=report.p50_processing_ms,
                    p95_processing_ms=report.p95_processing_ms,
                    p99_processing_ms=report.p99_processing_ms,
                    observed_jobs_per_hour=report.observed_jobs_per_hour,
                    recommendation=report.recommendation.model_dump(mode="json"),
                    options=report.options.model_dump(mode="json"),
                )
            )
        return report.model_copy(update={"calibration_id": str(calibration_id)})
