from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any, Protocol
from uuid import UUID

import uvicorn
from pydantic import BaseModel, ValidationError

from public_agent.config import Settings
from public_agent.domains.loader import DomainPackageLoader
from public_agent.operations.application import (
    build_outbox_retention_application,
    build_reflection_capacity_governance_application,
)
from public_agent.operations.capacity import (
    ReflectionCapacityReport,
    ReflectionCapacityStatus,
    ReflectionCapacityThresholds,
)
from public_agent.operations.capacity_governance import (
    ReflectionCapacityChangeRequestRecord,
    ReflectionCapacityGovernanceConflictError,
    ReflectionCapacityGovernanceNotFoundError,
    ReflectionCapacityGovernanceNotReadyError,
    ReflectionCapacityPolicyRecord,
)
from public_agent.operations.capacity_history import (
    ReflectionCapacityCalibrationOptions,
    ReflectionCapacityCalibrationReport,
    ReflectionCapacityInsufficientSamplesError,
    ReflectionCapacityTrendBucket,
    ReflectionCapacityTrendReport,
)
from public_agent.operations.outbox_retention import (
    OutboxRetentionPolicy,
    OutboxRetentionReport,
)
from public_agent.workers.application import (
    ReflectionWorkerConfigurationError,
    ReflectionWorkerOptions,
    build_reflection_capacity_application,
    build_reflection_worker_application,
)
from public_agent.workers.runner import ReflectionWorkerRunSummary


class ReflectionWorkerApplicationProtocol(Protocol):
    async def run(self, *, stop_event: asyncio.Event) -> ReflectionWorkerRunSummary: ...

    async def aclose(self) -> None: ...


class ReflectionWorkerApplicationFactory(Protocol):
    def __call__(
        self,
        settings: Settings,
        options: ReflectionWorkerOptions,
    ) -> ReflectionWorkerApplicationProtocol: ...


class ReflectionCapacityApplicationProtocol(Protocol):
    async def run(self) -> ReflectionCapacityReport: ...

    async def aclose(self) -> None: ...


class ReflectionCapacityApplicationFactory(Protocol):
    def __call__(
        self,
        settings: Settings,
        *,
        handler_version: str,
        thresholds: ReflectionCapacityThresholds,
    ) -> ReflectionCapacityApplicationProtocol: ...


class ReflectionCapacityGovernanceApplicationProtocol(Protocol):
    async def trend(
        self,
        *,
        hours: int,
        bucket: ReflectionCapacityTrendBucket,
        limit: int,
    ) -> ReflectionCapacityTrendReport: ...

    async def calibrate(
        self,
        options: ReflectionCapacityCalibrationOptions,
    ) -> ReflectionCapacityCalibrationReport: ...

    async def active_policy(self) -> ReflectionCapacityPolicyRecord | None: ...

    async def create_change_request(
        self,
        *,
        calibration_id: UUID,
        requested_by: str,
        window_required_seconds: int,
        window_minimum_observations: int,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def get_change_request(
        self,
        request_id: UUID,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def validate_window(
        self,
        *,
        request_id: UUID,
        expected_version: int,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def approve_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        approved_by: str,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def reject_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        rejected_by: str,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def publish_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        published_by: str,
        cooldown_seconds: int,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def review_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        reviewed_by: str,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def rollback_change(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        rolled_back_by: str,
        reason: str,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def aclose(self) -> None: ...


class ReflectionCapacityGovernanceApplicationFactory(Protocol):
    def __call__(
        self,
        settings: Settings,
        *,
        handler_version: str,
    ) -> ReflectionCapacityGovernanceApplicationProtocol: ...


class OutboxRetentionApplicationProtocol(Protocol):
    async def run(
        self,
        policy: OutboxRetentionPolicy,
        *,
        execute: bool,
        prune: bool,
    ) -> OutboxRetentionReport: ...

    async def aclose(self) -> None: ...


class OutboxRetentionApplicationFactory(Protocol):
    def __call__(
        self,
        settings: Settings,
        *,
        handler_version: str,
    ) -> OutboxRetentionApplicationProtocol: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="public-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-domain", help="Validate a domain package")
    validate.add_argument("path", type=Path)

    serve = subparsers.add_parser("serve", help="Run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)

    worker = subparsers.add_parser(
        "reflection-worker",
        help="Run the production PostgreSQL reflection worker",
    )
    worker.add_argument("--worker-id")
    worker.add_argument("--handler-version")
    worker.add_argument("--max-attempts", type=int)
    worker.add_argument("--retry-base-seconds", type=int)
    worker.add_argument("--retry-max-seconds", type=int)
    worker.add_argument("--lease-seconds", type=int)
    worker.add_argument("--heartbeat-seconds", type=int)
    worker.add_argument("--poll-interval-seconds", type=float)
    worker.add_argument("--poll-jitter-seconds", type=float)
    worker.add_argument("--drain-timeout-seconds", type=float)

    capacity = subparsers.add_parser(
        "capacity-check",
        help="Assess PostgreSQL reflection worker capacity",
    )
    capacity.add_argument("--handler-version")
    capacity.add_argument("--pretty", action="store_true")

    monitor = subparsers.add_parser(
        "capacity-monitor",
        help="Continuously sample PostgreSQL reflection worker capacity",
    )
    monitor.add_argument("--handler-version")
    monitor.add_argument("--interval-seconds", type=float)
    monitor.add_argument("--pretty", action="store_true")

    trend = subparsers.add_parser(
        "capacity-trend",
        help="Query persisted reflection capacity trends",
    )
    trend.add_argument("--handler-version")
    trend.add_argument("--hours", type=int, default=168)
    trend.add_argument(
        "--bucket",
        choices=tuple(bucket.value for bucket in ReflectionCapacityTrendBucket),
        default=ReflectionCapacityTrendBucket.HOUR.value,
    )
    trend.add_argument("--limit", type=int, default=168)
    trend.add_argument("--pretty", action="store_true")

    calibration = subparsers.add_parser(
        "capacity-calibrate",
        help="Calibrate capacity thresholds from real completed-job history",
    )
    calibration.add_argument("--handler-version")
    calibration.add_argument("--lookback-hours", type=int, default=168)
    calibration.add_argument("--minimum-samples", type=int, default=30)
    calibration.add_argument("--maximum-samples", type=int, default=10_000)
    calibration.add_argument("--target-drain-seconds", type=int, default=300)
    calibration.add_argument("--target-utilization", type=float, default=0.70)
    calibration.add_argument("--pretty", action="store_true")

    policy = subparsers.add_parser(
        "capacity-policy",
        help="Govern versioned reflection capacity threshold policies",
    )
    policy.add_argument(
        "action",
        choices=(
            "show",
            "create",
            "validate",
            "approve",
            "reject",
            "publish",
            "review",
            "rollback",
        ),
    )
    policy.add_argument("--handler-version")
    policy.add_argument("--request-id")
    policy.add_argument("--calibration-id")
    policy.add_argument("--expected-version", type=int)
    policy.add_argument("--operator")
    policy.add_argument("--window-seconds", type=int)
    policy.add_argument("--minimum-observations", type=int)
    policy.add_argument("--cooldown-seconds", type=int)
    policy.add_argument("--reason")
    policy.add_argument("--pretty", action="store_true")

    retention = subparsers.add_parser(
        "outbox-maintain",
        help="Preview or execute bounded Outbox archive and retention maintenance",
    )
    retention.add_argument("--handler-version")
    retention.add_argument("--archive-after-days", type=int, default=7)
    retention.add_argument("--purge-after-days", type=int, default=90)
    retention.add_argument("--batch-size", type=int, default=500)
    retention.add_argument("--maximum-batches", type=int, default=10)
    retention.add_argument("--execute", action="store_true")
    retention.add_argument("--prune", action="store_true")
    retention.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-domain":
        prepared = DomainPackageLoader().build(args.path)
        print(
            json.dumps(
                {
                    "package": prepared.package.model_dump(mode="json"),
                    "content_hash": prepared.content_hash,
                    "total_size_bytes": prepared.total_size_bytes,
                    "assets": [
                        {
                            "asset_type": asset.asset_type.value,
                            "key": asset.key,
                            "relative_path": asset.relative_path,
                            "media_type": asset.media_type,
                            "content_hash": asset.content_hash,
                            "size_bytes": asset.size_bytes,
                        }
                        for asset in prepared.assets
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "serve":
        from public_agent.api.app import create_management_app

        uvicorn.run(create_management_app(Settings()), host=args.host, port=args.port)
        return 0
    if args.command == "reflection-worker":
        try:
            return asyncio.run(run_reflection_worker_command(args))
        except KeyboardInterrupt:
            return 130
    if args.command == "capacity-check":
        try:
            return asyncio.run(run_capacity_check_command(args))
        except KeyboardInterrupt:
            return 130
    if args.command == "capacity-monitor":
        try:
            return asyncio.run(run_capacity_monitor_command(args))
        except KeyboardInterrupt:
            return 130
    if args.command == "capacity-trend":
        return asyncio.run(run_capacity_trend_command(args))
    if args.command == "capacity-calibrate":
        return asyncio.run(run_capacity_calibration_command(args))
    if args.command == "capacity-policy":
        return asyncio.run(run_capacity_policy_command(args))
    if args.command == "outbox-maintain":
        return asyncio.run(run_outbox_maintenance_command(args))
    raise RuntimeError(f"Unsupported command: {args.command}")


async def run_reflection_worker_command(
    args: argparse.Namespace,
    *,
    settings: Settings | None = None,
    application_factory: ReflectionWorkerApplicationFactory = (
        build_reflection_worker_application
    ),
) -> int:
    try:
        resolved_settings = settings or Settings()
        options = ReflectionWorkerOptions.from_settings(
            resolved_settings,
            worker_id=args.worker_id,
            handler_version=args.handler_version,
            max_attempts=args.max_attempts,
            retry_base_seconds=args.retry_base_seconds,
            retry_max_seconds=args.retry_max_seconds,
            lease_seconds=args.lease_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            poll_jitter_seconds=args.poll_jitter_seconds,
            drain_timeout_seconds=args.drain_timeout_seconds,
        )
        application = application_factory(resolved_settings, options)
    except ReflectionWorkerConfigurationError as exc:
        _emit_worker_event(
            "reflection_worker.configuration_failed",
            error_code=exc.code,
            error=True,
        )
        return 2
    except (ValidationError, ValueError):
        _emit_worker_event(
            "reflection_worker.configuration_failed",
            error_code="reflection_worker.configuration_invalid",
            error=True,
        )
        return 2
    except Exception as exc:
        _emit_worker_event(
            "reflection_worker.assembly_failed",
            error_code=_safe_cli_error_code(exc, prefix="assembly"),
            error=True,
        )
        return 2

    _emit_worker_event(
        "reflection_worker.starting",
        worker_id=options.worker_id,
        handler_version=options.handler_version,
    )
    stop_event = asyncio.Event()
    summary: ReflectionWorkerRunSummary | None = None
    failure_code: str | None = None
    try:
        with install_shutdown_signal_handlers(stop_event):
            summary = await application.run(stop_event=stop_event)
    except Exception as exc:
        failure_code = _safe_cli_error_code(exc)
    finally:
        try:
            await application.aclose()
        except Exception as exc:
            failure_code = failure_code or _safe_cli_error_code(exc, prefix="cleanup")

    if failure_code is not None:
        _emit_worker_event(
            "reflection_worker.failed",
            worker_id=options.worker_id,
            handler_version=options.handler_version,
            error_code=failure_code,
            error=True,
        )
        return 1
    if summary is None:
        _emit_worker_event(
            "reflection_worker.failed",
            worker_id=options.worker_id,
            handler_version=options.handler_version,
            error_code="reflection_worker.missing_summary",
            error=True,
        )
        return 1

    _emit_worker_event(
        "reflection_worker.stopped",
        worker_id=summary.worker_id,
        handler_version=options.handler_version,
        processed_jobs=summary.processed_jobs,
        last_job_id=str(summary.last_job_id) if summary.last_job_id is not None else None,
        last_error_code=summary.last_error_code,
    )
    return 3 if summary.last_error_code == "reflection_worker.drain_timeout" else 0


async def run_capacity_check_command(
    args: argparse.Namespace,
    *,
    settings: Settings | None = None,
    application_factory: ReflectionCapacityApplicationFactory = (
        build_reflection_capacity_application
    ),
) -> int:
    try:
        resolved_settings = settings or Settings()
        handler_version = (
            args.handler_version or resolved_settings.reflection_handler_version
        ).strip()
        if not handler_version or len(handler_version) > 64:
            raise ValueError("handler_version must contain 1 to 64 characters")
        thresholds = ReflectionCapacityThresholds.from_settings(resolved_settings)
        application = application_factory(
            resolved_settings,
            handler_version=handler_version,
            thresholds=thresholds,
        )
    except (ValidationError, ValueError):
        _emit_json(
            {
                "error_code": "reflection_capacity.configuration_invalid",
                "status": "error",
            },
            error=True,
        )
        return 2
    except Exception as exc:
        _emit_json(
            {
                "error_code": _safe_cli_error_code(
                    exc,
                    component="reflection_capacity",
                    prefix="assembly",
                ),
                "status": "error",
            },
            error=True,
        )
        return 2

    report: ReflectionCapacityReport | None = None
    failure_code: str | None = None
    try:
        report = await application.run()
    except Exception as exc:
        failure_code = _safe_cli_error_code(
            exc,
            component="reflection_capacity",
        )
    finally:
        try:
            await application.aclose()
        except Exception as exc:
            failure_code = failure_code or _safe_cli_error_code(
                exc,
                component="reflection_capacity",
                prefix="cleanup",
            )

    if failure_code is not None or report is None:
        _emit_json(
            {
                "error_code": failure_code or "reflection_capacity.missing_report",
                "status": "error",
            },
            error=True,
        )
        return 1

    _emit_json(
        report.model_dump(mode="json"),
        pretty=bool(args.pretty),
    )
    return {
        ReflectionCapacityStatus.HEALTHY: 0,
        ReflectionCapacityStatus.WARNING: 4,
        ReflectionCapacityStatus.CRITICAL: 5,
    }[report.status]


async def run_capacity_monitor_command(
    args: argparse.Namespace,
    *,
    settings: Settings | None = None,
    application_factory: ReflectionCapacityApplicationFactory = (
        build_reflection_capacity_application
    ),
) -> int:
    try:
        resolved_settings = settings or Settings()
        handler_version = _handler_version(args, resolved_settings)
        thresholds = ReflectionCapacityThresholds.from_settings(resolved_settings)
        interval_seconds = (
            args.interval_seconds
            if args.interval_seconds is not None
            else resolved_settings.reflection_capacity_sample_interval_seconds
        )
        if not 5 <= interval_seconds <= 3_600:
            raise ValueError("interval_seconds must be between 5 and 3600")
        application = application_factory(
            resolved_settings,
            handler_version=handler_version,
            thresholds=thresholds,
        )
    except (ValidationError, ValueError):
        _emit_json(
            {
                "error_code": "reflection_capacity_monitor.configuration_invalid",
                "status": "error",
            },
            error=True,
        )
        return 2
    except Exception as exc:
        _emit_json(
            {
                "error_code": _safe_cli_error_code(
                    exc,
                    component="reflection_capacity_monitor",
                    prefix="assembly",
                ),
                "status": "error",
            },
            error=True,
        )
        return 2

    stop_event = asyncio.Event()
    failure_code: str | None = None
    try:
        with install_shutdown_signal_handlers(stop_event):
            while not stop_event.is_set():
                report = await application.run()
                _emit_json(
                    {
                        "event": "reflection_capacity.sampled",
                        **report.model_dump(mode="json"),
                        **(
                            {"knowledge_lifecycle": lifecycle.model_dump(mode="json")}
                            if (lifecycle := getattr(application, "last_lifecycle_report", None))
                            is not None
                            else {}
                        ),
                    },
                    pretty=bool(args.pretty),
                )
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=interval_seconds,
                    )
                except TimeoutError:
                    pass
    except Exception as exc:
        failure_code = _safe_cli_error_code(
            exc,
            component="reflection_capacity_monitor",
        )
    finally:
        try:
            await application.aclose()
        except Exception as exc:
            failure_code = failure_code or _safe_cli_error_code(
                exc,
                component="reflection_capacity_monitor",
                prefix="cleanup",
            )
    if failure_code is not None:
        _emit_json(
            {"error_code": failure_code, "status": "error"},
            error=True,
        )
        return 1
    return 0


async def run_capacity_trend_command(
    args: argparse.Namespace,
    *,
    settings: Settings | None = None,
    application_factory: ReflectionCapacityGovernanceApplicationFactory = (
        build_reflection_capacity_governance_application
    ),
) -> int:
    try:
        resolved_settings = settings or Settings()
        handler_version = _handler_version(args, resolved_settings)
        bucket = ReflectionCapacityTrendBucket(args.bucket)
        if not 1 <= args.hours <= 8_760 or not 1 <= args.limit <= 1_000:
            raise ValueError("capacity trend bounds are invalid")
        application = application_factory(
            resolved_settings,
            handler_version=handler_version,
        )
    except (ValidationError, ValueError):
        _emit_json(
            {
                "error_code": "reflection_capacity_trend.configuration_invalid",
                "status": "error",
            },
            error=True,
        )
        return 2
    except Exception as exc:
        _emit_json(
            {
                "error_code": _safe_cli_error_code(
                    exc,
                    component="reflection_capacity_trend",
                    prefix="assembly",
                ),
                "status": "error",
            },
            error=True,
        )
        return 2

    try:
        report = await application.trend(
            hours=args.hours,
            bucket=bucket,
            limit=args.limit,
        )
    except Exception as exc:
        failure_code = _safe_cli_error_code(
            exc,
            component="reflection_capacity_trend",
        )
        _emit_json({"error_code": failure_code, "status": "error"}, error=True)
        return 1
    finally:
        try:
            await application.aclose()
        except Exception as exc:
            failure_code = _safe_cli_error_code(
                exc,
                component="reflection_capacity_trend",
                prefix="cleanup",
            )
            _emit_json({"error_code": failure_code, "status": "error"}, error=True)
            return 1
    _emit_json(report.model_dump(mode="json"), pretty=bool(args.pretty))
    return 0


async def run_capacity_calibration_command(
    args: argparse.Namespace,
    *,
    settings: Settings | None = None,
    application_factory: ReflectionCapacityGovernanceApplicationFactory = (
        build_reflection_capacity_governance_application
    ),
) -> int:
    try:
        resolved_settings = settings or Settings()
        handler_version = _handler_version(args, resolved_settings)
        options = ReflectionCapacityCalibrationOptions(
            lookback_hours=args.lookback_hours,
            minimum_samples=args.minimum_samples,
            maximum_samples=args.maximum_samples,
            target_drain_seconds=args.target_drain_seconds,
            target_utilization=args.target_utilization,
        )
        application = application_factory(
            resolved_settings,
            handler_version=handler_version,
        )
    except (ValidationError, ValueError):
        _emit_json(
            {
                "error_code": "reflection_capacity_calibration.configuration_invalid",
                "status": "error",
            },
            error=True,
        )
        return 2
    except Exception as exc:
        _emit_json(
            {
                "error_code": _safe_cli_error_code(
                    exc,
                    component="reflection_capacity_calibration",
                    prefix="assembly",
                ),
                "status": "error",
            },
            error=True,
        )
        return 2

    try:
        report = await application.calibrate(options)
    except ReflectionCapacityInsufficientSamplesError as exc:
        _emit_json(
            {
                "available_samples": exc.available,
                "error_code": exc.code,
                "required_samples": exc.required,
                "status": "insufficient_data",
            },
            error=True,
        )
        return 6
    except Exception as exc:
        failure_code = _safe_cli_error_code(
            exc,
            component="reflection_capacity_calibration",
        )
        _emit_json({"error_code": failure_code, "status": "error"}, error=True)
        return 1
    finally:
        try:
            await application.aclose()
        except Exception as exc:
            failure_code = _safe_cli_error_code(
                exc,
                component="reflection_capacity_calibration",
                prefix="cleanup",
            )
            _emit_json({"error_code": failure_code, "status": "error"}, error=True)
            return 1
    _emit_json(report.model_dump(mode="json"), pretty=bool(args.pretty))
    return 0


async def run_capacity_policy_command(
    args: argparse.Namespace,
    *,
    settings: Settings | None = None,
    application_factory: ReflectionCapacityGovernanceApplicationFactory = (
        build_reflection_capacity_governance_application
    ),
) -> int:
    try:
        resolved_settings = settings or Settings()
        handler_version = _handler_version(args, resolved_settings)
        action = str(args.action)
        request_id = _optional_uuid(args.request_id, name="request_id")
        calibration_id = _optional_uuid(args.calibration_id, name="calibration_id")
        expected_version = args.expected_version
        operator = args.operator
        window_seconds = (
            args.window_seconds
            if args.window_seconds is not None
            else resolved_settings.reflection_capacity_policy_window_seconds
        )
        minimum_observations = (
            args.minimum_observations
            if args.minimum_observations is not None
            else resolved_settings.reflection_capacity_policy_minimum_observations
        )
        cooldown_seconds = (
            args.cooldown_seconds
            if args.cooldown_seconds is not None
            else resolved_settings.reflection_capacity_policy_cooldown_seconds
        )
        if expected_version is not None and expected_version < 1:
            raise ValueError("expected_version must be positive")
        if not 60 <= window_seconds <= 2_592_000:
            raise ValueError("window_seconds is out of range")
        if not 2 <= minimum_observations <= 100_000:
            raise ValueError("minimum_observations is out of range")
        if not 60 <= cooldown_seconds <= 2_592_000:
            raise ValueError("cooldown_seconds is out of range")
        if action == "create":
            if calibration_id is None or operator is None:
                raise ValueError("create requires calibration_id and operator")
        elif action != "show":
            if request_id is None or expected_version is None:
                raise ValueError("governance action requires request_id and expected_version")
            if action != "validate" and operator is None:
                raise ValueError("governance action requires operator")
        if action == "rollback" and args.reason is None:
            raise ValueError("rollback requires reason")
        application = application_factory(
            resolved_settings,
            handler_version=handler_version,
        )
    except (ValidationError, ValueError):
        _emit_json(
            {
                "error_code": "reflection_capacity_policy.configuration_invalid",
                "status": "error",
            },
            error=True,
        )
        return 2
    except Exception as exc:
        _emit_json(
            {
                "error_code": _safe_cli_error_code(
                    exc,
                    component="reflection_capacity_policy",
                    prefix="assembly",
                ),
                "status": "error",
            },
            error=True,
        )
        return 2

    try:
        if action == "show":
            result: BaseModel | None
            result = (
                await application.get_change_request(request_id)
                if request_id is not None
                else await application.active_policy()
            )
        elif action == "create":
            assert calibration_id is not None
            assert operator is not None
            result = await application.create_change_request(
                calibration_id=calibration_id,
                requested_by=operator,
                window_required_seconds=window_seconds,
                window_minimum_observations=minimum_observations,
            )
        elif action == "validate":
            assert request_id is not None
            assert expected_version is not None
            result = await application.validate_window(
                request_id=request_id,
                expected_version=expected_version,
            )
        elif action == "approve":
            assert request_id is not None
            assert expected_version is not None
            assert operator is not None
            result = await application.approve_change(
                request_id=request_id,
                expected_version=expected_version,
                approved_by=operator,
            )
        elif action == "reject":
            assert request_id is not None
            assert expected_version is not None
            assert operator is not None
            result = await application.reject_change(
                request_id=request_id,
                expected_version=expected_version,
                rejected_by=operator,
            )
        elif action == "publish":
            assert request_id is not None
            assert expected_version is not None
            assert operator is not None
            result = await application.publish_change(
                request_id=request_id,
                expected_version=expected_version,
                published_by=operator,
                cooldown_seconds=cooldown_seconds,
            )
        elif action == "review":
            assert request_id is not None
            assert expected_version is not None
            assert operator is not None
            result = await application.review_change(
                request_id=request_id,
                expected_version=expected_version,
                reviewed_by=operator,
            )
        else:
            assert action == "rollback"
            assert request_id is not None
            assert expected_version is not None
            assert operator is not None
            assert args.reason is not None
            result = await application.rollback_change(
                request_id=request_id,
                expected_version=expected_version,
                rolled_back_by=operator,
                reason=args.reason,
            )
    except ReflectionCapacityGovernanceNotFoundError as exc:
        _emit_json({"error_code": exc.code, "status": "error"}, error=True)
        return 8
    except (
        ReflectionCapacityGovernanceConflictError,
        ReflectionCapacityGovernanceNotReadyError,
    ) as exc:
        _emit_json({"error_code": exc.code, "status": "blocked"}, error=True)
        return 7
    except Exception as exc:
        _emit_json(
            {
                "error_code": _safe_cli_error_code(
                    exc,
                    component="reflection_capacity_policy",
                ),
                "status": "error",
            },
            error=True,
        )
        return 1
    finally:
        try:
            await application.aclose()
        except Exception as exc:
            _emit_json(
                {
                    "error_code": _safe_cli_error_code(
                        exc,
                        component="reflection_capacity_policy",
                        prefix="cleanup",
                    ),
                    "status": "error",
                },
                error=True,
            )
            return 1
    _emit_json(
        result.model_dump(mode="json") if result is not None else None,
        pretty=bool(args.pretty),
    )
    return 0


async def run_outbox_maintenance_command(
    args: argparse.Namespace,
    *,
    settings: Settings | None = None,
    application_factory: OutboxRetentionApplicationFactory = (
        build_outbox_retention_application
    ),
) -> int:
    try:
        resolved_settings = settings or Settings()
        handler_version = _handler_version(args, resolved_settings)
        if args.prune and not args.execute:
            raise ValueError("--prune requires --execute")
        policy = OutboxRetentionPolicy(
            archive_after_days=args.archive_after_days,
            purge_after_days=args.purge_after_days,
            batch_size=args.batch_size,
            maximum_batches=args.maximum_batches,
        )
        application = application_factory(
            resolved_settings,
            handler_version=handler_version,
        )
    except (ValidationError, ValueError):
        _emit_json(
            {
                "error_code": "outbox_retention.configuration_invalid",
                "status": "error",
            },
            error=True,
        )
        return 2
    except Exception as exc:
        _emit_json(
            {
                "error_code": _safe_cli_error_code(
                    exc,
                    component="outbox_retention",
                    prefix="assembly",
                ),
                "status": "error",
            },
            error=True,
        )
        return 2

    try:
        report = await application.run(
            policy,
            execute=bool(args.execute),
            prune=bool(args.prune),
        )
    except Exception as exc:
        failure_code = _safe_cli_error_code(exc, component="outbox_retention")
        _emit_json({"error_code": failure_code, "status": "error"}, error=True)
        return 1
    finally:
        try:
            await application.aclose()
        except Exception as exc:
            failure_code = _safe_cli_error_code(
                exc,
                component="outbox_retention",
                prefix="cleanup",
            )
            _emit_json({"error_code": failure_code, "status": "error"}, error=True)
            return 1
    _emit_json(report.model_dump(mode="json"), pretty=bool(args.pretty))
    return 0


def _handler_version(args: argparse.Namespace, settings: Settings) -> str:
    value = args.handler_version or settings.reflection_handler_version
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        raise ValueError("handler_version must contain 1 to 64 characters")
    return normalized


def _optional_uuid(value: str | None, *, name: str) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID") from exc


@contextmanager
def install_shutdown_signal_handlers(stop_event: asyncio.Event) -> Iterator[None]:
    loop = asyncio.get_running_loop()
    installed: dict[signal.Signals, Any] = {}

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    handled = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        handled.append(signal.SIGBREAK)
    try:
        for signum in handled:
            installed[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        yield
    finally:
        for signum, previous in installed.items():
            signal.signal(signum, previous)


def _safe_cli_error_code(
    exc: Exception,
    *,
    component: str = "reflection_worker",
    prefix: str = "runtime",
) -> str:
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()
    normalized = re.sub(r"[^a-z0-9_.-]", "_", name)[:60] or "error"
    return f"{component}.{prefix}_{normalized}"


def _emit_worker_event(event: str, *, error: bool = False, **fields: object) -> None:
    _emit_json({"event": event, **fields}, error=error)


def _emit_json(
    payload: object,
    *,
    error: bool = False,
    pretty: bool = False,
) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            sort_keys=True,
        ),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
