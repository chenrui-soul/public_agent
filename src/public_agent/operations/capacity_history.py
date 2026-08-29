from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from public_agent.operations.capacity import ReflectionCapacityThresholds


class ReflectionCapacityTrendBucket(StrEnum):
    HOUR = "hour"
    DAY = "day"


class ReflectionCapacityTrendPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket_started_at: datetime
    sample_count: int
    average_ready: float
    maximum_ready: int
    maximum_oldest_ready_age_seconds: float
    maximum_dead_letter: int
    average_active_workers: float
    maximum_recommended_workers: int
    warning_samples: int
    critical_samples: int


class ReflectionCapacityTrendReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    handler_version: str
    bucket: ReflectionCapacityTrendBucket
    since: datetime
    generated_at: datetime
    points: tuple[ReflectionCapacityTrendPoint, ...]


class ReflectionProcessingSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    completed_at: datetime
    status: str
    total_processing_duration_ms: int = Field(gt=0)


class ReflectionCapacityCalibrationOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lookback_hours: int = Field(default=168, ge=1, le=8_760)
    minimum_samples: int = Field(default=30, ge=3, le=100_000)
    maximum_samples: int = Field(default=10_000, ge=3, le=100_000)
    target_drain_seconds: int = Field(default=300, ge=10, le=86_400)
    target_utilization: float = Field(default=0.70, ge=0.10, le=0.95)

    @model_validator(mode="after")
    def validate_sample_bounds(self) -> ReflectionCapacityCalibrationOptions:
        if self.minimum_samples > self.maximum_samples:
            raise ValueError("minimum_samples must not exceed maximum_samples")
        return self


class ReflectionCapacityThresholdRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_jobs_per_worker: int
    ready_warning: int
    ready_critical: int
    oldest_warning_seconds: int
    oldest_critical_seconds: int


class ReflectionCapacityCalibrationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    calibration_id: str | None = None
    handler_version: str
    calibrated_at: datetime
    window_started_at: datetime
    window_ended_at: datetime
    sample_count: int
    succeeded_count: int
    dead_letter_count: int
    p50_processing_ms: float
    p95_processing_ms: float
    p99_processing_ms: float
    observed_jobs_per_hour: float
    recommendation: ReflectionCapacityThresholdRecommendation
    options: ReflectionCapacityCalibrationOptions


class ReflectionCapacityInsufficientSamplesError(RuntimeError):
    def __init__(self, *, available: int, required: int) -> None:
        super().__init__("reflection capacity calibration has insufficient samples")
        self.available = available
        self.required = required
        self.code = "reflection_capacity_calibration.insufficient_samples"


def calibrate_reflection_capacity(
    samples: tuple[ReflectionProcessingSample, ...],
    *,
    handler_version: str,
    calibrated_at: datetime,
    options: ReflectionCapacityCalibrationOptions,
    current_thresholds: ReflectionCapacityThresholds,
) -> ReflectionCapacityCalibrationReport:
    if len(samples) < options.minimum_samples:
        raise ReflectionCapacityInsufficientSamplesError(
            available=len(samples),
            required=options.minimum_samples,
        )
    bounded = tuple(
        sorted(samples, key=lambda sample: sample.completed_at, reverse=True)[
            : options.maximum_samples
        ]
    )
    durations = sorted(sample.total_processing_duration_ms for sample in bounded)
    window_started_at = min(sample.completed_at for sample in bounded)
    window_ended_at = max(sample.completed_at for sample in bounded)
    observed_seconds = max(
        1.0,
        (window_ended_at - window_started_at).total_seconds(),
    )
    p50 = _nearest_rank(durations, 0.50)
    p95 = _nearest_rank(durations, 0.95)
    p99 = _nearest_rank(durations, 0.99)
    jobs_per_worker = math.floor(
        (options.target_drain_seconds * 1_000 / max(p95, 1.0))
        * options.target_utilization
    )
    target_jobs_per_worker = min(10_000, max(1, jobs_per_worker))
    ready_warning = min(
        1_000_000,
        max(1, target_jobs_per_worker * current_thresholds.minimum_workers),
    )
    ready_critical = min(1_000_000, max(ready_warning, ready_warning * 3))
    oldest_warning_seconds = min(604_800, options.target_drain_seconds)
    oldest_critical_seconds = min(604_800, options.target_drain_seconds * 3)
    return ReflectionCapacityCalibrationReport(
        handler_version=handler_version,
        calibrated_at=calibrated_at,
        window_started_at=window_started_at,
        window_ended_at=window_ended_at,
        sample_count=len(bounded),
        succeeded_count=sum(sample.status == "succeeded" for sample in bounded),
        dead_letter_count=sum(sample.status == "dead_letter" for sample in bounded),
        p50_processing_ms=p50,
        p95_processing_ms=p95,
        p99_processing_ms=p99,
        observed_jobs_per_hour=len(bounded) * 3_600 / observed_seconds,
        recommendation=ReflectionCapacityThresholdRecommendation(
            target_jobs_per_worker=target_jobs_per_worker,
            ready_warning=ready_warning,
            ready_critical=ready_critical,
            oldest_warning_seconds=oldest_warning_seconds,
            oldest_critical_seconds=oldest_critical_seconds,
        ),
        options=options,
    )


def _nearest_rank(values: list[int], percentile: float) -> float:
    rank = max(1, math.ceil(percentile * len(values)))
    return float(values[rank - 1])
