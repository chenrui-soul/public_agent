from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import pytest

from public_agent.cli import build_parser, run_capacity_check_command
from public_agent.config import Settings
from public_agent.operations.capacity import (
    ReflectionCapacityBacklog,
    ReflectionCapacityReport,
    ReflectionCapacityStatus,
    ReflectionCapacityThresholds,
    ReflectionCapacityWorkers,
)


class CapacityCommandApplication:
    def __init__(
        self,
        report: ReflectionCapacityReport,
        *,
        run_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.report = report
        self.run_error = run_error
        self.close_error = close_error
        self.closed = False

    async def run(self) -> ReflectionCapacityReport:
        if self.run_error is not None:
            raise self.run_error
        return self.report

    async def aclose(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _args(*extra: str) -> argparse.Namespace:
    return build_parser().parse_args(["capacity-check", *extra])


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None, "openai_api_key": None}
    values.update(overrides)
    return Settings(**values)


def _thresholds() -> ReflectionCapacityThresholds:
    return ReflectionCapacityThresholds.from_settings(_settings())


def _report(status: ReflectionCapacityStatus) -> ReflectionCapacityReport:
    observed_at = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)
    return ReflectionCapacityReport(
        status=status,
        handler_version="reflection-v1",
        observed_at=observed_at,
        backlog=ReflectionCapacityBacklog(
            pending=0,
            processing=0,
            retry_wait=0,
            ready=0,
            succeeded=0,
            dead_letter=0,
            oldest_available_at=None,
            oldest_ready_age_seconds=0,
        ),
        workers=ReflectionCapacityWorkers(
            registered=1,
            active=1,
            stale=0,
            stopped=0,
            errored=0,
            processed_jobs=0,
            oldest_last_seen_at=observed_at,
            newest_last_seen_at=observed_at,
        ),
        recommended_workers=1,
        scale_delta=0,
        reasons=(),
        thresholds=_thresholds(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (ReflectionCapacityStatus.HEALTHY, 0),
        (ReflectionCapacityStatus.WARNING, 4),
        (ReflectionCapacityStatus.CRITICAL, 5),
    ],
)
async def test_capacity_cli_returns_status_exit_codes_and_safe_report(
    status: ReflectionCapacityStatus,
    expected_code: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = CapacityCommandApplication(_report(status))

    code = await run_capacity_check_command(
        _args("--handler-version", "reflection-v1"),
        settings=_settings(),
        application_factory=lambda _settings, **_kwargs: application,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == expected_code
    assert captured.err == ""
    assert application.closed is True
    assert payload["status"] == status.value
    assert "database_url" not in captured.out
    assert "openai_api_key" not in captured.out


@pytest.mark.asyncio
async def test_capacity_cli_pretty_prints_and_does_not_require_openai_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = CapacityCommandApplication(_report(ReflectionCapacityStatus.HEALTHY))

    code = await run_capacity_check_command(
        _args("--pretty"),
        settings=_settings(openai_api_key=None),
        application_factory=lambda _settings, **_kwargs: application,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.startswith("{\n  ")
    assert json.loads(captured.out)["handler_version"] == "reflection-v1"


@pytest.mark.asyncio
async def test_capacity_cli_rejects_invalid_handler_before_assembly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory_called = False

    def factory(_settings: Settings, **_kwargs: object) -> CapacityCommandApplication:
        nonlocal factory_called
        factory_called = True
        return CapacityCommandApplication(_report(ReflectionCapacityStatus.HEALTHY))

    code = await run_capacity_check_command(
        _args("--handler-version", "   "),
        settings=_settings(),
        application_factory=factory,
    )

    captured = capsys.readouterr()
    assert code == 2
    assert factory_called is False
    assert json.loads(captured.err) == {
        "error_code": "reflection_capacity.configuration_invalid",
        "status": "error",
    }


@pytest.mark.asyncio
async def test_capacity_cli_assembly_failure_does_not_leak_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "postgresql://admin:assembly-secret@database/public_agent"

    def factory(_settings: Settings, **_kwargs: object) -> CapacityCommandApplication:
        raise RuntimeError(secret)

    code = await run_capacity_check_command(
        _args(),
        settings=_settings(),
        application_factory=factory,
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "reflection_capacity.assembly_runtime_error",
        "status": "error",
    }
    assert secret not in captured.err


@pytest.mark.asyncio
async def test_capacity_cli_runtime_and_cleanup_failures_do_not_leak_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "postgresql://admin:secret@database/public_agent"
    application = CapacityCommandApplication(
        _report(ReflectionCapacityStatus.HEALTHY),
        run_error=RuntimeError(secret),
        close_error=ValueError(f"cleanup:{secret}"),
    )

    code = await run_capacity_check_command(
        _args(),
        settings=_settings(),
        application_factory=lambda _settings, **_kwargs: application,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 1
    assert captured.out == ""
    assert application.closed is True
    assert payload == {
        "error_code": "reflection_capacity.runtime_runtime_error",
        "status": "error",
    }
    assert secret not in captured.err


@pytest.mark.asyncio
async def test_capacity_cli_cleanup_failure_overrides_success_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "cleanup-secret-must-not-leak"
    application = CapacityCommandApplication(
        _report(ReflectionCapacityStatus.HEALTHY),
        close_error=RuntimeError(secret),
    )

    code = await run_capacity_check_command(
        _args(),
        settings=_settings(),
        application_factory=lambda _settings, **_kwargs: application,
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "reflection_capacity.cleanup_runtime_error",
        "status": "error",
    }
    assert secret not in captured.err
