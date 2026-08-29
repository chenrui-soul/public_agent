from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from public_agent.config import Settings
from public_agent.core.types import utc_now
from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_governance import (
    ReflectionCapacityChangeRequestRecord,
    ReflectionCapacityPolicyRecord,
)
from public_agent.operations.capacity_history import (
    ReflectionCapacityCalibrationOptions,
    ReflectionCapacityCalibrationReport,
    ReflectionCapacityTrendBucket,
    ReflectionCapacityTrendReport,
    calibrate_reflection_capacity,
)
from public_agent.operations.outbox_retention import (
    OutboxRetentionPolicy,
    OutboxRetentionReport,
)
from public_agent.storage.capacity_governance import (
    PostgresReflectionCapacityGovernance,
)
from public_agent.storage.capacity_history import PostgresReflectionCapacityHistory
from public_agent.storage.database import Database
from public_agent.storage.outbox_retention import PostgresOutboxRetention


@dataclass(slots=True)
class ReflectionCapacityGovernanceApplication:
    database: Database
    history: PostgresReflectionCapacityHistory
    handler_version: str
    thresholds: ReflectionCapacityThresholds
    governance: PostgresReflectionCapacityGovernance
    owns_database: bool = True
    _closed: bool = field(default=False, init=False)

    async def trend(
        self,
        *,
        hours: int,
        bucket: ReflectionCapacityTrendBucket,
        limit: int,
    ) -> ReflectionCapacityTrendReport:
        if not 1 <= hours <= 8_760:
            raise ValueError("hours must be between 1 and 8760")
        await self.database.ping()
        return await self.history.trend(
            handler_version=self.handler_version,
            since=utc_now() - timedelta(hours=hours),
            bucket=bucket,
            limit=limit,
        )

    async def calibrate(
        self,
        options: ReflectionCapacityCalibrationOptions,
    ) -> ReflectionCapacityCalibrationReport:
        await self.database.ping()
        calibrated_at = utc_now()
        samples = await self.history.processing_samples(
            handler_version=self.handler_version,
            since=calibrated_at - timedelta(hours=options.lookback_hours),
            limit=options.maximum_samples,
        )
        current_thresholds = await self.governance.resolve_thresholds(self.thresholds)
        report = calibrate_reflection_capacity(
            samples,
            handler_version=self.handler_version,
            calibrated_at=calibrated_at,
            options=options,
            current_thresholds=current_thresholds,
        )
        return await self.history.record_calibration(report)

    async def active_policy(self) -> ReflectionCapacityPolicyRecord | None:
        await self.database.ping()
        return await self.governance.active_policy()

    async def create_change_request(
        self,
        *,
        calibration_id: UUID,
        requested_by: str,
        window_required_seconds: int,
        window_minimum_observations: int,
    ) -> ReflectionCapacityChangeRequestRecord:
        await self.database.ping()
        return await self.governance.create_change_request(
            calibration_id=calibration_id,
            fallback_thresholds=self.thresholds,
            requested_by=requested_by,
            window_required_seconds=window_required_seconds,
            window_minimum_observations=window_minimum_observations,
        )

    async def get_change_request(
        self,
        request_id: UUID,
    ) -> ReflectionCapacityChangeRequestRecord:
        await self.database.ping()
        return await self.governance.get_change_request(request_id)

    async def validate_window(
        self,
        *,
        request_id: UUID,
        expected_version: int,
    ) -> ReflectionCapacityChangeRequestRecord:
        await self.database.ping()
        return await self.governance.validate_window(
            request_id=request_id,
            expected_version=expected_version,
        )

    async def approve_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        approved_by: str,
    ) -> ReflectionCapacityChangeRequestRecord:
        await self.database.ping()
        return await self.governance.approve(
            request_id=request_id,
            expected_version=expected_version,
            approved_by=approved_by,
        )

    async def reject_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        rejected_by: str,
    ) -> ReflectionCapacityChangeRequestRecord:
        await self.database.ping()
        return await self.governance.reject(
            request_id=request_id,
            expected_version=expected_version,
            rejected_by=rejected_by,
        )

    async def publish_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        published_by: str,
        cooldown_seconds: int,
    ) -> ReflectionCapacityChangeRequestRecord:
        await self.database.ping()
        return await self.governance.publish(
            request_id=request_id,
            expected_version=expected_version,
            published_by=published_by,
            cooldown_seconds=cooldown_seconds,
        )

    async def review_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        reviewed_by: str,
    ) -> ReflectionCapacityChangeRequestRecord:
        await self.database.ping()
        return await self.governance.review(
            request_id=request_id,
            expected_version=expected_version,
            reviewed_by=reviewed_by,
        )

    async def rollback_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        rolled_back_by: str,
        reason: str,
    ) -> ReflectionCapacityChangeRequestRecord:
        await self.database.ping()
        return await self.governance.rollback(
            request_id=request_id,
            expected_version=expected_version,
            rolled_back_by=rolled_back_by,
            reason=reason,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.owns_database:
            await self.database.dispose()


@dataclass(slots=True)
class OutboxRetentionApplication:
    database: Database
    retention: PostgresOutboxRetention
    owns_database: bool = True
    _closed: bool = field(default=False, init=False)

    async def run(
        self,
        policy: OutboxRetentionPolicy,
        *,
        execute: bool,
        prune: bool,
    ) -> OutboxRetentionReport:
        await self.database.ping()
        if not execute:
            preview = await self.retention.preview(policy)
            return OutboxRetentionReport(
                executed=False,
                prune_requested=prune,
                archived_jobs=0,
                purged_jobs=0,
                before=preview,
                after=preview,
                policy=policy,
            )
        return await self.retention.maintain(policy, prune=prune)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.owns_database:
            await self.database.dispose()


def build_reflection_capacity_governance_application(
    settings: Settings,
    *,
    handler_version: str,
    database: Database | None = None,
) -> ReflectionCapacityGovernanceApplication:
    resolved_database = database or Database(settings.database_url)
    return ReflectionCapacityGovernanceApplication(
        database=resolved_database,
        history=PostgresReflectionCapacityHistory(resolved_database.sessions),
        handler_version=_normalize_handler_version(handler_version),
        thresholds=ReflectionCapacityThresholds.from_settings(settings),
        governance=PostgresReflectionCapacityGovernance(
            resolved_database.sessions,
            handler_version=_normalize_handler_version(handler_version),
        ),
        owns_database=database is None,
    )


def build_outbox_retention_application(
    settings: Settings,
    *,
    handler_version: str,
    database: Database | None = None,
) -> OutboxRetentionApplication:
    resolved_database = database or Database(settings.database_url)
    return OutboxRetentionApplication(
        database=resolved_database,
        retention=PostgresOutboxRetention(
            resolved_database.sessions,
            handler_version=_normalize_handler_version(handler_version),
        ),
        owns_database=database is None,
    )


def _normalize_handler_version(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        raise ValueError("handler_version must contain 1 to 64 characters")
    return normalized
