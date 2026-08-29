from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_history import (
    ReflectionCapacityCalibrationOptions,
    ReflectionCapacityInsufficientSamplesError,
    ReflectionProcessingSample,
    calibrate_reflection_capacity,
)
from public_agent.operations.outbox_retention import OutboxRetentionPolicy

OBSERVED_AT = datetime(2026, 8, 25, 6, 0, tzinfo=UTC)


def _thresholds() -> ReflectionCapacityThresholds:
    return ReflectionCapacityThresholds(
        stale_after_seconds=180,
        minimum_workers=2,
        maximum_workers=20,
        target_jobs_per_worker=20,
        ready_warning=100,
        ready_critical=500,
        oldest_warning_seconds=300,
        oldest_critical_seconds=1_800,
        dead_letter_warning=1,
        dead_letter_critical=10,
    )


def test_calibration_uses_real_p95_duration_and_target_drain_window() -> None:
    samples = tuple(
        ReflectionProcessingSample(
            completed_at=OBSERVED_AT - timedelta(minutes=index),
            status="dead_letter" if index == 4 else "succeeded",
            total_processing_duration_ms=duration,
        )
        for index, duration in enumerate((100, 200, 300, 400, 1_000))
    )

    report = calibrate_reflection_capacity(
        samples,
        handler_version="reflection-v1",
        calibrated_at=OBSERVED_AT,
        options=ReflectionCapacityCalibrationOptions(
            minimum_samples=5,
            maximum_samples=5,
            target_drain_seconds=300,
            target_utilization=0.70,
        ),
        current_thresholds=_thresholds(),
    )

    assert report.p50_processing_ms == 300
    assert report.p95_processing_ms == 1_000
    assert report.p99_processing_ms == 1_000
    assert report.succeeded_count == 4
    assert report.dead_letter_count == 1
    assert report.recommendation.target_jobs_per_worker == 210
    assert report.recommendation.ready_warning == 420
    assert report.recommendation.ready_critical == 1_260
    assert report.recommendation.oldest_warning_seconds == 300
    assert report.recommendation.oldest_critical_seconds == 900


def test_calibration_fails_closed_when_history_is_not_representative() -> None:
    sample = ReflectionProcessingSample(
        completed_at=OBSERVED_AT,
        status="succeeded",
        total_processing_duration_ms=100,
    )

    with pytest.raises(ReflectionCapacityInsufficientSamplesError) as exc_info:
        calibrate_reflection_capacity(
            (sample,),
            handler_version="reflection-v1",
            calibrated_at=OBSERVED_AT,
            options=ReflectionCapacityCalibrationOptions(minimum_samples=3),
            current_thresholds=_thresholds(),
        )

    assert exc_info.value.available == 1
    assert exc_info.value.required == 3


def test_retention_policy_requires_archive_before_destructive_purge() -> None:
    policy = OutboxRetentionPolicy(
        archive_after_days=7,
        purge_after_days=90,
        batch_size=250,
        maximum_batches=4,
    )
    assert policy.batch_size * policy.maximum_batches == 1_000

    with pytest.raises(ValidationError, match="purge_after_days"):
        OutboxRetentionPolicy(archive_after_days=30, purge_after_days=30)
