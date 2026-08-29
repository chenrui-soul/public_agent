from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_history import (
    ReflectionCapacityThresholdRecommendation,
)


class ReflectionCapacityPolicyStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"


class ReflectionCapacityChangeStatus(StrEnum):
    PENDING_WINDOW = "pending_window"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COOLING_DOWN = "cooling_down"
    EFFECTIVE = "effective"
    INEFFECTIVE = "ineffective"
    ROLLED_BACK = "rolled_back"


class ReflectionCapacityPolicyRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    handler_version: str
    policy_version: int = Field(ge=1)
    status: ReflectionCapacityPolicyStatus
    thresholds: ReflectionCapacityThresholds
    source_type: str
    source_calibration_id: UUID | None = None
    previous_policy_id: UUID | None = None
    created_by: str
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    created_at: datetime


class ReflectionCapacityObservationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    window_started_at: datetime
    window_ended_at: datetime
    sample_count: int = Field(ge=1)
    warning_samples: int = Field(ge=0)
    critical_samples: int = Field(ge=0)
    average_ready: float = Field(ge=0)
    maximum_ready: int = Field(ge=0)
    average_oldest_ready_age_seconds: float = Field(ge=0)
    maximum_oldest_ready_age_seconds: float = Field(ge=0)
    maximum_dead_letter: int = Field(ge=0)

    @property
    def unhealthy_rate(self) -> float:
        return (self.warning_samples + self.critical_samples) / self.sample_count

    @property
    def critical_rate(self) -> float:
        return self.critical_samples / self.sample_count


class ReflectionCapacityPolicyEffect(BaseModel):
    model_config = ConfigDict(frozen=True)

    effective: bool
    reasons: tuple[str, ...]
    baseline: ReflectionCapacityObservationEvidence
    observed: ReflectionCapacityObservationEvidence


class ReflectionCapacityChangeRequestRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    handler_version: str
    calibration_id: UUID
    base_policy_id: UUID
    published_policy_id: UUID | None = None
    status: ReflectionCapacityChangeStatus
    version: int = Field(ge=1)
    proposed_thresholds: ReflectionCapacityThresholds
    window_started_at: datetime
    window_required_seconds: int = Field(ge=60, le=2_592_000)
    window_minimum_observations: int = Field(ge=2, le=100_000)
    window_validated_at: datetime | None = None
    window_evidence: ReflectionCapacityObservationEvidence | None = None
    requested_by: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    published_by: str | None = None
    published_at: datetime | None = None
    cooldown_until: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    effect: ReflectionCapacityPolicyEffect | None = None
    rolled_back_by: str | None = None
    rolled_back_at: datetime | None = None
    rollback_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ReflectionCapacityGovernanceError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ReflectionCapacityGovernanceNotFoundError(ReflectionCapacityGovernanceError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="reflection_capacity_policy.not_found",
        )


class ReflectionCapacityGovernanceConflictError(ReflectionCapacityGovernanceError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="reflection_capacity_policy.state_conflict",
        )


class ReflectionCapacityGovernanceNotReadyError(ReflectionCapacityGovernanceError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "reflection_capacity_policy.window_not_ready",
    ) -> None:
        super().__init__(message, code=code)


def recommended_capacity_thresholds(
    *,
    current: ReflectionCapacityThresholds,
    recommendation: ReflectionCapacityThresholdRecommendation,
) -> ReflectionCapacityThresholds:
    values = current.model_dump()
    values.update(recommendation.model_dump())
    return ReflectionCapacityThresholds.model_validate(values)


def assess_capacity_policy_effect(
    *,
    baseline: ReflectionCapacityObservationEvidence,
    observed: ReflectionCapacityObservationEvidence,
) -> ReflectionCapacityPolicyEffect:
    reasons: list[str] = []
    if observed.critical_rate > baseline.critical_rate:
        reasons.append("reflection_capacity_policy.critical_rate_regressed")
    if observed.unhealthy_rate > baseline.unhealthy_rate + 0.10:
        reasons.append("reflection_capacity_policy.unhealthy_rate_regressed")
    ready_limit = max(baseline.average_ready * 1.20, baseline.average_ready + 1.0)
    if observed.average_ready > ready_limit:
        reasons.append("reflection_capacity_policy.ready_regressed")
    oldest_limit = max(
        baseline.average_oldest_ready_age_seconds * 1.20,
        baseline.average_oldest_ready_age_seconds + 30.0,
    )
    if observed.average_oldest_ready_age_seconds > oldest_limit:
        reasons.append("reflection_capacity_policy.oldest_ready_regressed")
    if observed.maximum_dead_letter > baseline.maximum_dead_letter:
        reasons.append("reflection_capacity_policy.dead_letter_regressed")
    return ReflectionCapacityPolicyEffect(
        effective=not reasons,
        reasons=tuple(reasons),
        baseline=baseline,
        observed=observed,
    )
