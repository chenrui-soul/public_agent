from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_governance import (
    ReflectionCapacityObservationEvidence,
    assess_capacity_policy_effect,
    recommended_capacity_thresholds,
)
from public_agent.operations.capacity_history import (
    ReflectionCapacityThresholdRecommendation,
)

OBSERVED_AT = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)


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


def _evidence(
    *,
    average_ready: float,
    average_oldest: float,
    warnings: int,
    critical: int,
) -> ReflectionCapacityObservationEvidence:
    return ReflectionCapacityObservationEvidence(
        window_started_at=OBSERVED_AT,
        window_ended_at=OBSERVED_AT,
        sample_count=10,
        warning_samples=warnings,
        critical_samples=critical,
        average_ready=average_ready,
        maximum_ready=int(average_ready),
        average_oldest_ready_age_seconds=average_oldest,
        maximum_oldest_ready_age_seconds=average_oldest,
        maximum_dead_letter=0,
    )


def test_recommended_policy_preserves_non_calibrated_safety_thresholds() -> None:
    result = recommended_capacity_thresholds(
        current=_thresholds(),
        recommendation=ReflectionCapacityThresholdRecommendation(
            target_jobs_per_worker=210,
            ready_warning=420,
            ready_critical=1_260,
            oldest_warning_seconds=300,
            oldest_critical_seconds=900,
        ),
    )

    assert result.minimum_workers == 2
    assert result.maximum_workers == 20
    assert result.stale_after_seconds == 180
    assert result.dead_letter_warning == 1
    assert result.dead_letter_critical == 10
    assert result.target_jobs_per_worker == 210
    assert result.ready_critical == 1_260


def test_recommended_policy_revalidates_cross_field_relationships() -> None:
    with pytest.raises(ValidationError, match="ready_warning"):
        recommended_capacity_thresholds(
            current=_thresholds(),
            recommendation=ReflectionCapacityThresholdRecommendation(
                target_jobs_per_worker=20,
                ready_warning=600,
                ready_critical=500,
                oldest_warning_seconds=300,
                oldest_critical_seconds=900,
            ),
        )


def test_policy_effect_review_accepts_non_regressing_observations() -> None:
    result = assess_capacity_policy_effect(
        baseline=_evidence(
            average_ready=20,
            average_oldest=120,
            warnings=4,
            critical=1,
        ),
        observed=_evidence(
            average_ready=10,
            average_oldest=60,
            warnings=2,
            critical=0,
        ),
    )

    assert result.effective is True
    assert result.reasons == ()


def test_policy_effect_review_rejects_hidden_raw_backlog_regression() -> None:
    result = assess_capacity_policy_effect(
        baseline=_evidence(
            average_ready=0,
            average_oldest=0,
            warnings=0,
            critical=0,
        ),
        observed=_evidence(
            average_ready=10,
            average_oldest=90,
            warnings=0,
            critical=0,
        ),
    )

    assert result.effective is False
    assert "reflection_capacity_policy.ready_regressed" in result.reasons
    assert "reflection_capacity_policy.oldest_ready_regressed" in result.reasons
