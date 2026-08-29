from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from public_agent.config import Settings
from public_agent.workers.runner import ReflectionCapacitySnapshot


class ReflectionCapacityStatus(StrEnum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


class ReflectionCapacityThresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stale_after_seconds: int = Field(ge=5, le=3_600)
    minimum_workers: int = Field(ge=1, le=100)
    maximum_workers: int = Field(ge=1, le=1_000)
    target_jobs_per_worker: int = Field(ge=1, le=10_000)
    ready_warning: int = Field(ge=1, le=1_000_000)
    ready_critical: int = Field(ge=1, le=1_000_000)
    oldest_warning_seconds: int = Field(ge=1, le=604_800)
    oldest_critical_seconds: int = Field(ge=1, le=604_800)
    dead_letter_warning: int = Field(ge=1, le=1_000_000)
    dead_letter_critical: int = Field(ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_relationships(self) -> ReflectionCapacityThresholds:
        if self.minimum_workers > self.maximum_workers:
            raise ValueError("minimum_workers must not exceed maximum_workers")
        if self.ready_warning > self.ready_critical:
            raise ValueError("ready_warning must not exceed ready_critical")
        if self.oldest_warning_seconds > self.oldest_critical_seconds:
            raise ValueError(
                "oldest_warning_seconds must not exceed oldest_critical_seconds"
            )
        if self.dead_letter_warning > self.dead_letter_critical:
            raise ValueError("dead_letter_warning must not exceed dead_letter_critical")
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> ReflectionCapacityThresholds:
        return cls(
            stale_after_seconds=settings.reflection_capacity_stale_after_seconds,
            minimum_workers=settings.reflection_capacity_minimum_workers,
            maximum_workers=settings.reflection_capacity_maximum_workers,
            target_jobs_per_worker=(
                settings.reflection_capacity_target_jobs_per_worker
            ),
            ready_warning=settings.reflection_capacity_ready_warning,
            ready_critical=settings.reflection_capacity_ready_critical,
            oldest_warning_seconds=(
                settings.reflection_capacity_oldest_warning_seconds
            ),
            oldest_critical_seconds=(
                settings.reflection_capacity_oldest_critical_seconds
            ),
            dead_letter_warning=(
                settings.reflection_capacity_dead_letter_warning
            ),
            dead_letter_critical=(
                settings.reflection_capacity_dead_letter_critical
            ),
        )


class ReflectionCapacityBacklog(BaseModel):
    model_config = ConfigDict(frozen=True)

    pending: int
    processing: int
    retry_wait: int
    ready: int
    succeeded: int
    dead_letter: int
    oldest_available_at: datetime | None
    oldest_ready_age_seconds: float


class ReflectionCapacityWorkers(BaseModel):
    model_config = ConfigDict(frozen=True)

    registered: int
    active: int
    stale: int
    stopped: int
    errored: int
    processed_jobs: int
    oldest_last_seen_at: datetime | None
    newest_last_seen_at: datetime | None


class ReflectionCapacityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ReflectionCapacityStatus
    handler_version: str
    observed_at: datetime
    backlog: ReflectionCapacityBacklog
    workers: ReflectionCapacityWorkers
    recommended_workers: int
    scale_delta: int
    reasons: tuple[str, ...]
    thresholds: ReflectionCapacityThresholds


def assess_reflection_capacity(
    snapshot: ReflectionCapacitySnapshot,
    *,
    handler_version: str,
    thresholds: ReflectionCapacityThresholds,
) -> ReflectionCapacityReport:
    backlog = snapshot.backlog
    workers = snapshot.workers
    ready = backlog.pending + backlog.retry_wait
    oldest_age = _age_seconds(
        observed_at=snapshot.observed_at,
        timestamp=backlog.oldest_available_at,
    )
    workload = ready + backlog.processing
    recommended_workers = min(
        thresholds.maximum_workers,
        max(
            thresholds.minimum_workers,
            math.ceil(workload / thresholds.target_jobs_per_worker),
        ),
    )

    critical: list[str] = []
    warning: list[str] = []
    if ready >= thresholds.ready_critical:
        critical.append("reflection_capacity.ready_backlog_critical")
    elif ready >= thresholds.ready_warning:
        warning.append("reflection_capacity.ready_backlog_warning")
    if oldest_age >= thresholds.oldest_critical_seconds:
        critical.append("reflection_capacity.oldest_ready_critical")
    elif oldest_age >= thresholds.oldest_warning_seconds:
        warning.append("reflection_capacity.oldest_ready_warning")
    if backlog.dead_letter >= thresholds.dead_letter_critical:
        critical.append("reflection_capacity.dead_letter_critical")
    elif backlog.dead_letter >= thresholds.dead_letter_warning:
        warning.append("reflection_capacity.dead_letter_warning")
    if workers.active == 0 and workload > 0:
        critical.append("reflection_capacity.no_active_worker_with_backlog")
    elif workers.active < thresholds.minimum_workers:
        warning.append("reflection_capacity.active_workers_below_minimum")
    if workers.stale > 0:
        warning.append("reflection_capacity.stale_workers_present")
    if workers.errored > 0:
        warning.append("reflection_capacity.worker_errors_present")
    if workers.active > thresholds.maximum_workers:
        warning.append("reflection_capacity.active_workers_above_maximum")
    if recommended_workers > workers.active and not critical:
        warning.append("reflection_capacity.scale_out_recommended")

    reasons = tuple(dict.fromkeys([*critical, *warning]))
    if critical:
        status = ReflectionCapacityStatus.CRITICAL
    elif warning:
        status = ReflectionCapacityStatus.WARNING
    else:
        status = ReflectionCapacityStatus.HEALTHY
    return ReflectionCapacityReport(
        status=status,
        handler_version=handler_version,
        observed_at=snapshot.observed_at,
        backlog=ReflectionCapacityBacklog(
            pending=backlog.pending,
            processing=backlog.processing,
            retry_wait=backlog.retry_wait,
            ready=ready,
            succeeded=backlog.succeeded,
            dead_letter=backlog.dead_letter,
            oldest_available_at=backlog.oldest_available_at,
            oldest_ready_age_seconds=oldest_age,
        ),
        workers=ReflectionCapacityWorkers(
            registered=workers.registered,
            active=workers.active,
            stale=workers.stale,
            stopped=workers.stopped,
            errored=workers.errored,
            processed_jobs=workers.processed_jobs,
            oldest_last_seen_at=workers.oldest_last_seen_at,
            newest_last_seen_at=workers.newest_last_seen_at,
        ),
        recommended_workers=recommended_workers,
        scale_delta=recommended_workers - workers.active,
        reasons=reasons,
        thresholds=thresholds,
    )


def _age_seconds(*, observed_at: datetime, timestamp: datetime | None) -> float:
    if timestamp is None:
        return 0.0
    return max(0.0, (observed_at - timestamp).total_seconds())
