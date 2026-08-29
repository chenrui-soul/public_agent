from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.core.types import utc_now
from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_governance import (
    ReflectionCapacityChangeRequestRecord,
    ReflectionCapacityChangeStatus,
    ReflectionCapacityGovernanceConflictError,
    ReflectionCapacityGovernanceNotFoundError,
    ReflectionCapacityGovernanceNotReadyError,
    ReflectionCapacityObservationEvidence,
    ReflectionCapacityPolicyEffect,
    ReflectionCapacityPolicyRecord,
    ReflectionCapacityPolicyStatus,
    assess_capacity_policy_effect,
    recommended_capacity_thresholds,
)
from public_agent.operations.capacity_history import (
    ReflectionCapacityThresholdRecommendation,
)
from public_agent.storage.models import (
    ReflectionCapacityCalibrationModel,
    ReflectionCapacityChangeRequestModel,
    ReflectionCapacityObservationModel,
    ReflectionCapacityPolicyModel,
)
from public_agent.storage.outbox import REFLECTION_JOB_TYPE

CapacityOperatorResolver = Callable[[AsyncSession], Awaitable[str]]


class PostgresReflectionCapacityGovernance:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        handler_version: str,
    ) -> None:
        normalized = handler_version.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("handler_version must contain 1 to 64 characters")
        self._sessions = sessions
        self.handler_version = normalized

    async def active_policy(self) -> ReflectionCapacityPolicyRecord | None:
        async with self._sessions() as session:
            row = await session.scalar(self._active_policy_statement())
        return _policy_record(row) if row is not None else None

    async def resolve_thresholds(
        self,
        fallback: ReflectionCapacityThresholds,
    ) -> ReflectionCapacityThresholds:
        policy = await self.active_policy()
        return policy.thresholds if policy is not None else fallback

    async def get_change_request(
        self,
        request_id: UUID,
    ) -> ReflectionCapacityChangeRequestRecord:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ReflectionCapacityChangeRequestModel).where(
                    ReflectionCapacityChangeRequestModel.id == request_id,
                    ReflectionCapacityChangeRequestModel.job_type
                    == REFLECTION_JOB_TYPE,
                    ReflectionCapacityChangeRequestModel.handler_version
                    == self.handler_version,
                )
            )
        if row is None:
            raise ReflectionCapacityGovernanceNotFoundError(
                "Unknown reflection capacity change request"
            )
        return _request_record(row)

    async def create_change_request(
        self,
        *,
        calibration_id: UUID,
        fallback_thresholds: ReflectionCapacityThresholds,
        requested_by: str | None = None,
        window_required_seconds: int,
        window_minimum_observations: int,
        operator_resolver: CapacityOperatorResolver | None = None,
    ) -> ReflectionCapacityChangeRequestRecord:
        if not 60 <= window_required_seconds <= 2_592_000:
            raise ValueError("window_required_seconds must be between 60 and 2592000")
        if not 2 <= window_minimum_observations <= 100_000:
            raise ValueError("window_minimum_observations must be between 2 and 100000")
        request_id: UUID | None = None
        now = utc_now()
        async with self._sessions() as session, session.begin():
            operator = await _resolve_operator(
                session,
                explicit=requested_by,
                resolver=operator_resolver,
            )
            await self._lock_handler(session)
            calibration = await session.scalar(
                select(ReflectionCapacityCalibrationModel).where(
                    ReflectionCapacityCalibrationModel.id == calibration_id,
                    ReflectionCapacityCalibrationModel.job_type == REFLECTION_JOB_TYPE,
                    ReflectionCapacityCalibrationModel.handler_version
                    == self.handler_version,
                )
            )
            if calibration is None:
                raise ReflectionCapacityGovernanceNotFoundError(
                    "Unknown reflection capacity calibration"
                )
            existing = await session.scalar(
                select(ReflectionCapacityChangeRequestModel).where(
                    ReflectionCapacityChangeRequestModel.calibration_id
                    == calibration_id
                )
            )
            if existing is not None:
                if (
                    existing.handler_version != self.handler_version
                    or existing.requested_by != operator
                    or existing.window_required_seconds != window_required_seconds
                    or existing.window_minimum_observations
                    != window_minimum_observations
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Calibration is already bound to a different change request"
                    )
                request_id = existing.id
            else:
                active = await session.scalar(
                    self._active_policy_statement(for_update=True)
                )
                if active is None:
                    historical_count = await session.scalar(
                        select(func.count(ReflectionCapacityPolicyModel.id)).where(
                            ReflectionCapacityPolicyModel.job_type
                            == REFLECTION_JOB_TYPE,
                            ReflectionCapacityPolicyModel.handler_version
                            == self.handler_version,
                        )
                    )
                    if int(historical_count or 0) > 0:
                        raise ReflectionCapacityGovernanceConflictError(
                            "Capacity policy history exists without an active policy"
                        )
                    active = ReflectionCapacityPolicyModel(
                        id=uuid4(),
                        job_type=REFLECTION_JOB_TYPE,
                        handler_version=self.handler_version,
                        policy_version=1,
                        status=ReflectionCapacityPolicyStatus.ACTIVE.value,
                        thresholds=fallback_thresholds.model_dump(mode="json"),
                        source_type="settings_baseline",
                        source_calibration_id=None,
                        previous_policy_id=None,
                        created_by="settings-fallback",
                        activated_at=now,
                        deactivated_at=None,
                    )
                    session.add(active)
                    await session.flush()
                recommendation = (
                    ReflectionCapacityThresholdRecommendation.model_validate(
                        calibration.recommendation
                    )
                )
                current = ReflectionCapacityThresholds.model_validate(
                    active.thresholds
                )
                proposed = recommended_capacity_thresholds(
                    current=current,
                    recommendation=recommendation,
                )
                if proposed == current:
                    raise ReflectionCapacityGovernanceConflictError(
                        "Calibration recommendation does not change the active policy"
                    )
                request_id = uuid4()
                session.add(
                    ReflectionCapacityChangeRequestModel(
                        id=request_id,
                        job_type=REFLECTION_JOB_TYPE,
                        handler_version=self.handler_version,
                        calibration_id=calibration_id,
                        base_policy_id=active.id,
                        published_policy_id=None,
                        status=ReflectionCapacityChangeStatus.PENDING_WINDOW.value,
                        version=1,
                        proposed_thresholds=proposed.model_dump(mode="json"),
                        window_started_at=now,
                        window_required_seconds=window_required_seconds,
                        window_minimum_observations=window_minimum_observations,
                        requested_by=operator,
                    )
                )
        if request_id is None:
            raise RuntimeError("capacity change request did not produce an id")
        return await self.get_change_request(request_id)

    async def validate_window(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        operator_resolver: CapacityOperatorResolver | None = None,
    ) -> ReflectionCapacityChangeRequestRecord:
        now = utc_now()
        async with self._sessions() as session, session.begin():
            if operator_resolver is not None:
                await operator_resolver(session)
            row = await self._lock_request(session, request_id)
            _require_state(
                row,
                expected_version=expected_version,
                status=ReflectionCapacityChangeStatus.PENDING_WINDOW,
            )
            elapsed = (now - row.window_started_at).total_seconds()
            if elapsed < row.window_required_seconds:
                raise ReflectionCapacityGovernanceNotReadyError(
                    "Capacity validation window has not elapsed"
                )
            evidence = await self._observation_evidence(
                session,
                since=row.window_started_at,
                until=now,
            )
            evidence = _require_evidence(
                evidence,
                minimum_observations=row.window_minimum_observations,
                minimum_span_seconds=row.window_required_seconds,
                code_prefix="window",
            )
            row.status = ReflectionCapacityChangeStatus.AWAITING_APPROVAL.value
            row.window_validated_at = now
            row.window_evidence = evidence.model_dump(mode="json")
            row.version += 1
            row.updated_at = now
        return await self.get_change_request(request_id)

    async def approve(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        approved_by: str | None = None,
        operator_resolver: CapacityOperatorResolver | None = None,
    ) -> ReflectionCapacityChangeRequestRecord:
        now = utc_now()
        async with self._sessions() as session, session.begin():
            operator = await _resolve_operator(
                session,
                explicit=approved_by,
                resolver=operator_resolver,
            )
            row = await self._lock_request(session, request_id)
            _require_state(
                row,
                expected_version=expected_version,
                status=ReflectionCapacityChangeStatus.AWAITING_APPROVAL,
            )
            if row.window_evidence is None:
                raise ReflectionCapacityGovernanceConflictError(
                    "Capacity change requires validated window evidence"
                )
            if operator == row.requested_by:
                raise ReflectionCapacityGovernanceConflictError(
                    "Capacity change requester cannot approve their own request"
                )
            row.status = ReflectionCapacityChangeStatus.APPROVED.value
            row.approved_by = operator
            row.approved_at = now
            row.version += 1
            row.updated_at = now
        return await self.get_change_request(request_id)

    async def reject(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        rejected_by: str | None = None,
        operator_resolver: CapacityOperatorResolver | None = None,
    ) -> ReflectionCapacityChangeRequestRecord:
        now = utc_now()
        async with self._sessions() as session, session.begin():
            operator = await _resolve_operator(
                session,
                explicit=rejected_by,
                resolver=operator_resolver,
            )
            row = await self._lock_request(session, request_id)
            _require_state(
                row,
                expected_version=expected_version,
                status=ReflectionCapacityChangeStatus.AWAITING_APPROVAL,
            )
            row.status = ReflectionCapacityChangeStatus.REJECTED.value
            row.rejected_by = operator
            row.rejected_at = now
            row.version += 1
            row.updated_at = now
        return await self.get_change_request(request_id)

    async def publish(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        published_by: str | None = None,
        cooldown_seconds: int,
        operator_resolver: CapacityOperatorResolver | None = None,
    ) -> ReflectionCapacityChangeRequestRecord:
        if not 60 <= cooldown_seconds <= 2_592_000:
            raise ValueError("cooldown_seconds must be between 60 and 2592000")
        now = utc_now()
        async with self._sessions() as session, session.begin():
            operator = await _resolve_operator(
                session,
                explicit=published_by,
                resolver=operator_resolver,
            )
            await self._lock_handler(session)
            row = await self._lock_request(session, request_id)
            _require_state(
                row,
                expected_version=expected_version,
                status=ReflectionCapacityChangeStatus.APPROVED,
            )
            cooling = await session.scalar(
                select(ReflectionCapacityChangeRequestModel.id).where(
                    ReflectionCapacityChangeRequestModel.job_type
                    == REFLECTION_JOB_TYPE,
                    ReflectionCapacityChangeRequestModel.handler_version
                    == self.handler_version,
                    ReflectionCapacityChangeRequestModel.status
                    == ReflectionCapacityChangeStatus.COOLING_DOWN.value,
                )
            )
            if cooling is not None:
                raise ReflectionCapacityGovernanceConflictError(
                    "Another capacity policy is still cooling down"
                )
            active = await session.scalar(
                self._active_policy_statement(for_update=True)
            )
            if active is None or active.id != row.base_policy_id:
                raise ReflectionCapacityGovernanceConflictError(
                    "Active capacity policy changed before publication"
                )
            if row.approved_by is None or row.window_evidence is None:
                raise ReflectionCapacityGovernanceConflictError(
                    "Capacity change is missing approval or window evidence"
                )
            latest_version = await session.scalar(
                select(func.max(ReflectionCapacityPolicyModel.policy_version)).where(
                    ReflectionCapacityPolicyModel.job_type == REFLECTION_JOB_TYPE,
                    ReflectionCapacityPolicyModel.handler_version
                    == self.handler_version,
                )
            )
            policy_id = uuid4()
            active.status = ReflectionCapacityPolicyStatus.SUPERSEDED.value
            active.deactivated_at = now
            await session.flush()
            session.add(
                ReflectionCapacityPolicyModel(
                    id=policy_id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=self.handler_version,
                    policy_version=int(latest_version or 0) + 1,
                    status=ReflectionCapacityPolicyStatus.ACTIVE.value,
                    thresholds=row.proposed_thresholds,
                    source_type="calibration",
                    source_calibration_id=row.calibration_id,
                    previous_policy_id=active.id,
                    created_by=operator,
                    activated_at=now,
                    deactivated_at=None,
                )
            )
            await session.flush()
            row.published_policy_id = policy_id
            row.status = ReflectionCapacityChangeStatus.COOLING_DOWN.value
            row.published_by = operator
            row.published_at = now
            row.cooldown_until = now + timedelta(seconds=cooldown_seconds)
            row.version += 1
            row.updated_at = now
        return await self.get_change_request(request_id)

    async def review(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        reviewed_by: str | None = None,
        operator_resolver: CapacityOperatorResolver | None = None,
    ) -> ReflectionCapacityChangeRequestRecord:
        now = utc_now()
        async with self._sessions() as session, session.begin():
            operator = await _resolve_operator(
                session,
                explicit=reviewed_by,
                resolver=operator_resolver,
            )
            await self._lock_handler(session)
            row = await self._lock_request(session, request_id)
            _require_state(
                row,
                expected_version=expected_version,
                status=ReflectionCapacityChangeStatus.COOLING_DOWN,
            )
            if (
                row.cooldown_until is None
                or row.published_at is None
                or row.window_evidence is None
            ):
                raise ReflectionCapacityGovernanceConflictError(
                    "Published capacity change is missing governance evidence"
                )
            if now < row.cooldown_until:
                raise ReflectionCapacityGovernanceNotReadyError(
                    "Capacity policy is still cooling down",
                    code="reflection_capacity_policy.cooldown_active",
                )
            active = await session.scalar(
                self._active_policy_statement(for_update=True)
            )
            if active is None or active.id != row.published_policy_id:
                raise ReflectionCapacityGovernanceConflictError(
                    "Published capacity policy is no longer active"
                )
            observed = await self._observation_evidence(
                session,
                since=row.published_at,
                until=now,
            )
            cooldown_seconds = int(
                (row.cooldown_until - row.published_at).total_seconds()
            )
            observed = _require_evidence(
                observed,
                minimum_observations=row.window_minimum_observations,
                minimum_span_seconds=cooldown_seconds,
                code_prefix="review",
            )
            baseline = ReflectionCapacityObservationEvidence.model_validate(
                row.window_evidence
            )
            effect = assess_capacity_policy_effect(
                baseline=baseline,
                observed=observed,
            )
            row.status = (
                ReflectionCapacityChangeStatus.EFFECTIVE.value
                if effect.effective
                else ReflectionCapacityChangeStatus.INEFFECTIVE.value
            )
            row.reviewed_by = operator
            row.reviewed_at = now
            row.effect_evidence = effect.model_dump(mode="json")
            row.version += 1
            row.updated_at = now
        return await self.get_change_request(request_id)

    async def rollback(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        rolled_back_by: str | None = None,
        reason: str,
        operator_resolver: CapacityOperatorResolver | None = None,
    ) -> ReflectionCapacityChangeRequestRecord:
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 1_000:
            raise ValueError("reason must contain 1 to 1000 characters")
        now = utc_now()
        async with self._sessions() as session, session.begin():
            operator = await _resolve_operator(
                session,
                explicit=rolled_back_by,
                resolver=operator_resolver,
            )
            await self._lock_handler(session)
            row = await self._lock_request(session, request_id)
            if row.version != expected_version or row.status not in {
                ReflectionCapacityChangeStatus.COOLING_DOWN.value,
                ReflectionCapacityChangeStatus.INEFFECTIVE.value,
            }:
                raise ReflectionCapacityGovernanceConflictError(
                    "Capacity change is not rollback eligible"
                )
            if row.published_policy_id is None:
                raise ReflectionCapacityGovernanceConflictError(
                    "Capacity change has no published policy"
                )
            active = await session.scalar(
                self._active_policy_statement(for_update=True)
            )
            previous = await session.scalar(
                select(ReflectionCapacityPolicyModel)
                .where(ReflectionCapacityPolicyModel.id == row.base_policy_id)
                .with_for_update()
            )
            if (
                active is None
                or active.id != row.published_policy_id
                or previous is None
                or active.previous_policy_id != previous.id
                or previous.status != ReflectionCapacityPolicyStatus.SUPERSEDED.value
            ):
                raise ReflectionCapacityGovernanceConflictError(
                    "Published policy is no longer the active rollback target"
                )
            active.status = ReflectionCapacityPolicyStatus.ROLLED_BACK.value
            active.deactivated_at = now
            await session.flush()
            previous.status = ReflectionCapacityPolicyStatus.ACTIVE.value
            previous.activated_at = now
            previous.deactivated_at = None
            await session.flush()
            row.status = ReflectionCapacityChangeStatus.ROLLED_BACK.value
            row.rolled_back_by = operator
            row.rolled_back_at = now
            row.rollback_reason = normalized_reason
            row.version += 1
            row.updated_at = now
        return await self.get_change_request(request_id)

    def _active_policy_statement(
        self,
        *,
        for_update: bool = False,
    ) -> Select[tuple[ReflectionCapacityPolicyModel]]:
        statement = select(ReflectionCapacityPolicyModel).where(
            ReflectionCapacityPolicyModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityPolicyModel.handler_version == self.handler_version,
            ReflectionCapacityPolicyModel.status
            == ReflectionCapacityPolicyStatus.ACTIVE.value,
        )
        return statement.with_for_update() if for_update else statement

    async def _lock_request(
        self,
        session: AsyncSession,
        request_id: UUID,
    ) -> ReflectionCapacityChangeRequestModel:
        row = await session.scalar(
            select(ReflectionCapacityChangeRequestModel)
            .where(
                ReflectionCapacityChangeRequestModel.id == request_id,
                ReflectionCapacityChangeRequestModel.job_type == REFLECTION_JOB_TYPE,
                ReflectionCapacityChangeRequestModel.handler_version
                == self.handler_version,
            )
            .with_for_update()
        )
        if row is None:
            raise ReflectionCapacityGovernanceNotFoundError(
                "Unknown reflection capacity change request"
            )
        return row

    async def _lock_handler(self, session: AsyncSession) -> None:
        await session.execute(
            select(func.pg_advisory_xact_lock(_handler_lock_id(self.handler_version)))
        )

    async def _observation_evidence(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        until: datetime,
    ) -> ReflectionCapacityObservationEvidence | None:
        row = (
            await session.execute(
                select(
                    func.min(ReflectionCapacityObservationModel.observed_at),
                    func.max(ReflectionCapacityObservationModel.observed_at),
                    func.count(ReflectionCapacityObservationModel.id),
                    func.count(ReflectionCapacityObservationModel.id).filter(
                        ReflectionCapacityObservationModel.status == "warning"
                    ),
                    func.count(ReflectionCapacityObservationModel.id).filter(
                        ReflectionCapacityObservationModel.status == "critical"
                    ),
                    func.avg(ReflectionCapacityObservationModel.ready),
                    func.max(ReflectionCapacityObservationModel.ready),
                    func.avg(
                        ReflectionCapacityObservationModel.oldest_ready_age_seconds
                    ),
                    func.max(
                        ReflectionCapacityObservationModel.oldest_ready_age_seconds
                    ),
                    func.max(ReflectionCapacityObservationModel.dead_letter),
                ).where(
                    ReflectionCapacityObservationModel.job_type
                    == REFLECTION_JOB_TYPE,
                    ReflectionCapacityObservationModel.handler_version
                    == self.handler_version,
                    ReflectionCapacityObservationModel.observed_at >= since,
                    ReflectionCapacityObservationModel.observed_at <= until,
                )
            )
        ).one()
        if int(row[2]) == 0 or row[0] is None or row[1] is None:
            return None
        return ReflectionCapacityObservationEvidence(
            window_started_at=row[0],
            window_ended_at=row[1],
            sample_count=int(row[2]),
            warning_samples=int(row[3]),
            critical_samples=int(row[4]),
            average_ready=float(row[5]),
            maximum_ready=int(row[6]),
            average_oldest_ready_age_seconds=float(row[7]),
            maximum_oldest_ready_age_seconds=float(row[8]),
            maximum_dead_letter=int(row[9]),
        )


def _require_state(
    row: ReflectionCapacityChangeRequestModel,
    *,
    expected_version: int,
    status: ReflectionCapacityChangeStatus,
) -> None:
    if expected_version < 1:
        raise ValueError("expected_version must be positive")
    if row.version != expected_version or row.status != status.value:
        raise ReflectionCapacityGovernanceConflictError(
            "Capacity change request changed before this action"
        )


def _require_evidence(
    evidence: ReflectionCapacityObservationEvidence | None,
    *,
    minimum_observations: int,
    minimum_span_seconds: int,
    code_prefix: str,
) -> ReflectionCapacityObservationEvidence:
    if evidence is None or evidence.sample_count < minimum_observations:
        raise ReflectionCapacityGovernanceNotReadyError(
            "Capacity governance evidence has insufficient samples",
            code=f"reflection_capacity_policy.{code_prefix}_samples_insufficient",
        )
    span = (evidence.window_ended_at - evidence.window_started_at).total_seconds()
    if span < minimum_span_seconds:
        raise ReflectionCapacityGovernanceNotReadyError(
            "Capacity governance evidence does not cover the required span",
            code=f"reflection_capacity_policy.{code_prefix}_coverage_insufficient",
        )
    return evidence


def _operator(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("operator must contain 1 to 200 characters")
    return normalized


async def _resolve_operator(
    session: AsyncSession,
    *,
    explicit: str | None,
    resolver: CapacityOperatorResolver | None,
) -> str:
    if resolver is not None:
        if explicit is not None:
            raise ValueError("operator must come from exactly one trusted source")
        return _operator(await resolver(session))
    if explicit is None:
        raise ValueError("operator is required")
    return _operator(explicit)


def _handler_lock_id(handler_version: str) -> int:
    digest = hashlib.sha256(
        f"reflection-capacity-policy|{handler_version}".encode()
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _policy_record(row: ReflectionCapacityPolicyModel) -> ReflectionCapacityPolicyRecord:
    return ReflectionCapacityPolicyRecord(
        id=row.id,
        handler_version=row.handler_version,
        policy_version=row.policy_version,
        status=ReflectionCapacityPolicyStatus(row.status),
        thresholds=ReflectionCapacityThresholds.model_validate(row.thresholds),
        source_type=row.source_type,
        source_calibration_id=row.source_calibration_id,
        previous_policy_id=row.previous_policy_id,
        created_by=row.created_by,
        activated_at=row.activated_at,
        deactivated_at=row.deactivated_at,
        created_at=row.created_at,
    )


def _request_record(
    row: ReflectionCapacityChangeRequestModel,
) -> ReflectionCapacityChangeRequestRecord:
    return ReflectionCapacityChangeRequestRecord(
        id=row.id,
        handler_version=row.handler_version,
        calibration_id=row.calibration_id,
        base_policy_id=row.base_policy_id,
        published_policy_id=row.published_policy_id,
        status=ReflectionCapacityChangeStatus(row.status),
        version=row.version,
        proposed_thresholds=ReflectionCapacityThresholds.model_validate(
            row.proposed_thresholds
        ),
        window_started_at=row.window_started_at,
        window_required_seconds=row.window_required_seconds,
        window_minimum_observations=row.window_minimum_observations,
        window_validated_at=row.window_validated_at,
        window_evidence=(
            ReflectionCapacityObservationEvidence.model_validate(row.window_evidence)
            if row.window_evidence is not None
            else None
        ),
        requested_by=row.requested_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        rejected_by=row.rejected_by,
        rejected_at=row.rejected_at,
        published_by=row.published_by,
        published_at=row.published_at,
        cooldown_until=row.cooldown_until,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        effect=(
            ReflectionCapacityPolicyEffect.model_validate(row.effect_evidence)
            if row.effect_evidence is not None
            else None
        ),
        rolled_back_by=row.rolled_back_by,
        rolled_back_at=row.rolled_back_at,
        rollback_reason=row.rollback_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
