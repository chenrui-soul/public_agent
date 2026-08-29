from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from public_agent.cli import (
    build_parser,
    run_capacity_calibration_command,
    run_capacity_monitor_command,
    run_capacity_policy_command,
    run_capacity_trend_command,
    run_outbox_maintenance_command,
)
from public_agent.config import Settings
from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_governance import (
    ReflectionCapacityGovernanceNotReadyError,
    ReflectionCapacityPolicyRecord,
    ReflectionCapacityPolicyStatus,
)
from public_agent.operations.capacity_history import (
    ReflectionCapacityCalibrationOptions,
    ReflectionCapacityInsufficientSamplesError,
    ReflectionCapacityTrendBucket,
    ReflectionCapacityTrendPoint,
    ReflectionCapacityTrendReport,
)
from public_agent.operations.outbox_retention import (
    OutboxRetentionPolicy,
    OutboxRetentionPreview,
    OutboxRetentionReport,
)

OBSERVED_AT = datetime(2026, 8, 25, 6, 0, tzinfo=UTC)


def _args(command: str, *extra: str) -> argparse.Namespace:
    return build_parser().parse_args([command, *extra])


def _settings() -> Settings:
    return Settings(_env_file=None, openai_api_key=None)


class GovernanceApplication:
    def __init__(self, *, insufficient: bool = False) -> None:
        self.insufficient = insufficient
        self.closed = False

    async def trend(
        self,
        *,
        hours: int,
        bucket: ReflectionCapacityTrendBucket,
        limit: int,
    ) -> ReflectionCapacityTrendReport:
        assert hours == 24
        assert limit == 12
        return ReflectionCapacityTrendReport(
            handler_version="reflection-v1",
            bucket=bucket,
            since=OBSERVED_AT,
            generated_at=OBSERVED_AT,
            points=(
                ReflectionCapacityTrendPoint(
                    bucket_started_at=OBSERVED_AT,
                    sample_count=2,
                    average_ready=3,
                    maximum_ready=5,
                    maximum_oldest_ready_age_seconds=10,
                    maximum_dead_letter=0,
                    average_active_workers=1,
                    maximum_recommended_workers=1,
                    warning_samples=0,
                    critical_samples=0,
                ),
            ),
        )

    async def calibrate(self, options: ReflectionCapacityCalibrationOptions):
        del options
        if self.insufficient:
            raise ReflectionCapacityInsufficientSamplesError(available=2, required=30)
        raise AssertionError("not used")

    async def aclose(self) -> None:
        self.closed = True


class RetentionApplication:
    def __init__(self) -> None:
        self.closed = False

    async def run(
        self,
        policy: OutboxRetentionPolicy,
        *,
        execute: bool,
        prune: bool,
    ) -> OutboxRetentionReport:
        preview = OutboxRetentionPreview(
            observed_at=OBSERVED_AT,
            handler_version="reflection-v1",
            archive_eligible=4,
            purge_eligible=2,
            purge_blocked_by_retry_requests=1,
        )
        return OutboxRetentionReport(
            executed=execute,
            prune_requested=prune,
            archived_jobs=0,
            purged_jobs=0,
            before=preview,
            after=preview,
            policy=policy,
        )

    async def aclose(self) -> None:
        self.closed = True


class PolicyApplication:
    def __init__(self, *, not_ready: bool = False) -> None:
        self.not_ready = not_ready
        self.closed = False

    async def active_policy(self) -> ReflectionCapacityPolicyRecord:
        return ReflectionCapacityPolicyRecord(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            handler_version="reflection-v1",
            policy_version=2,
            status=ReflectionCapacityPolicyStatus.ACTIVE,
            thresholds=ReflectionCapacityThresholds(
                stale_after_seconds=180,
                minimum_workers=1,
                maximum_workers=32,
                target_jobs_per_worker=25,
                ready_warning=100,
                ready_critical=500,
                oldest_warning_seconds=300,
                oldest_critical_seconds=1_800,
                dead_letter_warning=1,
                dead_letter_critical=10,
            ),
            source_type="calibration",
            created_by="ops@example.com",
            activated_at=OBSERVED_AT,
            created_at=OBSERVED_AT,
        )

    async def validate_window(self, **_kwargs: object):
        if self.not_ready:
            raise ReflectionCapacityGovernanceNotReadyError("not ready")
        raise AssertionError("not used")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_capacity_trend_cli_returns_persisted_buckets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = GovernanceApplication()
    code = await run_capacity_trend_command(
        _args("capacity-trend", "--hours", "24", "--limit", "12"),
        settings=_settings(),
        application_factory=lambda _settings, **_kwargs: application,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert application.closed is True
    assert payload["points"][0]["maximum_ready"] == 5


@pytest.mark.asyncio
async def test_capacity_calibration_cli_has_distinct_insufficient_data_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = GovernanceApplication(insufficient=True)
    code = await run_capacity_calibration_command(
        _args("capacity-calibrate"),
        settings=_settings(),
        application_factory=lambda _settings, **_kwargs: application,
    )

    payload = json.loads(capsys.readouterr().err)
    assert code == 6
    assert application.closed is True
    assert payload == {
        "available_samples": 2,
        "error_code": "reflection_capacity_calibration.insufficient_samples",
        "required_samples": 30,
        "status": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_outbox_maintenance_is_dry_run_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = RetentionApplication()
    code = await run_outbox_maintenance_command(
        _args("outbox-maintain"),
        settings=_settings(),
        application_factory=lambda _settings, **_kwargs: application,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert application.closed is True
    assert payload["executed"] is False
    assert payload["before"]["purge_blocked_by_retry_requests"] == 1


@pytest.mark.asyncio
async def test_outbox_prune_requires_explicit_execute_before_assembly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory_called = False

    def factory(_settings: Settings, **_kwargs: object) -> RetentionApplication:
        nonlocal factory_called
        factory_called = True
        return RetentionApplication()

    code = await run_outbox_maintenance_command(
        _args("outbox-maintain", "--prune"),
        settings=_settings(),
        application_factory=factory,
    )

    assert code == 2
    assert factory_called is False
    assert json.loads(capsys.readouterr().err)["error_code"] == (
        "outbox_retention.configuration_invalid"
    )


@pytest.mark.asyncio
async def test_capacity_monitor_rejects_unsafe_sampling_interval(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await run_capacity_monitor_command(
        _args("capacity-monitor", "--interval-seconds", "1"),
        settings=_settings(),
    )

    assert code == 2
    assert json.loads(capsys.readouterr().err)["status"] == "error"


@pytest.mark.asyncio
async def test_capacity_policy_show_returns_active_database_policy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = PolicyApplication()
    code = await run_capacity_policy_command(
        _args("capacity-policy", "show"),
        settings=_settings(),
        application_factory=lambda _settings, **_kwargs: application,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert application.closed is True
    assert payload["policy_version"] == 2
    assert payload["thresholds"]["target_jobs_per_worker"] == 25


@pytest.mark.asyncio
async def test_capacity_policy_window_not_ready_has_stable_blocked_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = PolicyApplication(not_ready=True)
    code = await run_capacity_policy_command(
        _args(
            "capacity-policy",
            "validate",
            "--request-id",
            "00000000-0000-0000-0000-000000000002",
            "--expected-version",
            "1",
        ),
        settings=_settings(),
        application_factory=lambda _settings, **_kwargs: application,
    )

    payload = json.loads(capsys.readouterr().err)
    assert code == 7
    assert application.closed is True
    assert payload == {
        "error_code": "reflection_capacity_policy.window_not_ready",
        "status": "blocked",
    }


@pytest.mark.asyncio
async def test_capacity_policy_create_requires_explicit_operator_and_calibration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory_called = False

    def factory(_settings: Settings, **_kwargs: object) -> PolicyApplication:
        nonlocal factory_called
        factory_called = True
        return PolicyApplication()

    code = await run_capacity_policy_command(
        _args("capacity-policy", "create"),
        settings=_settings(),
        application_factory=factory,
    )

    assert code == 2
    assert factory_called is False
    assert json.loads(capsys.readouterr().err)["error_code"] == (
        "reflection_capacity_policy.configuration_invalid"
    )


@pytest.mark.asyncio
async def test_capacity_policy_rejects_invalid_version_before_assembly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory_called = False

    def factory(_settings: Settings, **_kwargs: object) -> PolicyApplication:
        nonlocal factory_called
        factory_called = True
        return PolicyApplication()

    code = await run_capacity_policy_command(
        _args(
            "capacity-policy",
            "validate",
            "--request-id",
            "00000000-0000-0000-0000-000000000002",
            "--expected-version",
            "0",
        ),
        settings=_settings(),
        application_factory=factory,
    )

    assert code == 2
    assert factory_called is False
    assert json.loads(capsys.readouterr().err)["status"] == "error"
