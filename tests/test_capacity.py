from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from public_agent.operations.capacity import (
    ReflectionCapacityStatus,
    ReflectionCapacityThresholds,
    assess_reflection_capacity,
)
from public_agent.operations.capacity_control import CapacityGovernanceKnowledgeLifecycleScanReport
from public_agent.workers import (
    ReflectionBacklogSnapshot,
    ReflectionCapacitySnapshot,
    ReflectionWorkerFleetSnapshot,
)
from public_agent.workers.application import ReflectionCapacityApplication

OBSERVED_AT = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)


class CapacityDatabase:
    def __init__(self, events: list[str], *, fail_dispose: bool = False) -> None:
        self.events = events
        self.fail_dispose = fail_dispose

    async def ping(self) -> None:
        self.events.append("database.ping")

    async def dispose(self) -> None:
        self.events.append("database.dispose")
        if self.fail_dispose:
            raise RuntimeError("dispose failed")


class CapacitySource:
    def __init__(self, events: list[str], snapshot: ReflectionCapacitySnapshot) -> None:
        self.events = events
        self.snapshot = snapshot
        self.stale_after_seconds: int | None = None

    async def capacity_snapshot(
        self,
        *,
        stale_after_seconds: int,
    ) -> ReflectionCapacitySnapshot:
        self.events.append("source.capacity_snapshot")
        self.stale_after_seconds = stale_after_seconds
        return self.snapshot


class CapacityObservationSink:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.reports = []

    async def record_observation(self, report: object) -> None:
        self.events.append("sink.record_observation")
        self.reports.append(report)


class CapacityDriftScanner:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def scan_drift(self) -> None:
        self.events.append("drift.scan")

    async def scan_incidents(self) -> None:
        self.events.append("incident.scan")


class CapacityLifecycleScanner:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def scan_knowledge_lifecycle(self) -> CapacityGovernanceKnowledgeLifecycleScanReport:
        self.events.append("lifecycle.scan")
        return CapacityGovernanceKnowledgeLifecycleScanReport(
            handler_version="reflection-v1",
            scanned=5,
            current=2,
            due=1,
            overdue=1,
            quarantined=0,
            retired=1,
            scanned_at=OBSERVED_AT,
        )


class CapacityPolicyResolver:
    def __init__(self, thresholds: ReflectionCapacityThresholds) -> None:
        self.thresholds = thresholds
        self.fallback: ReflectionCapacityThresholds | None = None

    async def resolve_thresholds(
        self,
        fallback: ReflectionCapacityThresholds,
    ) -> ReflectionCapacityThresholds:
        self.fallback = fallback
        return self.thresholds


def _thresholds(**overrides: int) -> ReflectionCapacityThresholds:
    values = {
        "stale_after_seconds": 180,
        "minimum_workers": 1,
        "maximum_workers": 10,
        "target_jobs_per_worker": 20,
        "ready_warning": 100,
        "ready_critical": 500,
        "oldest_warning_seconds": 300,
        "oldest_critical_seconds": 1_800,
        "dead_letter_warning": 1,
        "dead_letter_critical": 10,
    }
    values.update(overrides)
    return ReflectionCapacityThresholds.model_validate(values)


def _snapshot(
    *,
    pending: int = 0,
    processing: int = 0,
    retry_wait: int = 0,
    dead_letter: int = 0,
    active: int = 1,
    stale: int = 0,
    stopped: int = 0,
    errored: int = 0,
    oldest_age_seconds: int | None = None,
) -> ReflectionCapacitySnapshot:
    oldest = (
        None
        if oldest_age_seconds is None
        else OBSERVED_AT - timedelta(seconds=oldest_age_seconds)
    )
    return ReflectionCapacitySnapshot(
        observed_at=OBSERVED_AT,
        backlog=ReflectionBacklogSnapshot(
            pending=pending,
            processing=processing,
            retry_wait=retry_wait,
            succeeded=0,
            dead_letter=dead_letter,
            oldest_available_at=oldest,
        ),
        workers=ReflectionWorkerFleetSnapshot(
            registered=active + stale + stopped,
            active=active,
            stale=stale,
            stopped=stopped,
            errored=errored,
            processed_jobs=7,
            oldest_last_seen_at=OBSERVED_AT - timedelta(seconds=10),
            newest_last_seen_at=OBSERVED_AT,
        ),
    )


def test_healthy_capacity_recommends_safe_scale_down() -> None:
    report = assess_reflection_capacity(
        _snapshot(active=3),
        handler_version="reflection-v1",
        thresholds=_thresholds(),
    )

    assert report.status is ReflectionCapacityStatus.HEALTHY
    assert report.recommended_workers == 1
    assert report.scale_delta == -2
    assert report.reasons == ()


def test_warning_thresholds_and_scale_out_are_inclusive() -> None:
    report = assess_reflection_capacity(
        _snapshot(pending=100, active=1, oldest_age_seconds=300),
        handler_version="reflection-v1",
        thresholds=_thresholds(),
    )

    assert report.status is ReflectionCapacityStatus.WARNING
    assert report.recommended_workers == 5
    assert report.scale_delta == 4
    assert report.reasons == (
        "reflection_capacity.ready_backlog_warning",
        "reflection_capacity.oldest_ready_warning",
        "reflection_capacity.scale_out_recommended",
    )


def test_critical_capacity_combines_backlog_age_dead_letter_and_worker_loss() -> None:
    report = assess_reflection_capacity(
        _snapshot(
            pending=500,
            processing=1,
            dead_letter=10,
            active=0,
            oldest_age_seconds=1_800,
        ),
        handler_version="reflection-v1",
        thresholds=_thresholds(),
    )

    assert report.status is ReflectionCapacityStatus.CRITICAL
    assert report.recommended_workers == 10
    assert report.scale_delta == 10
    assert report.reasons == (
        "reflection_capacity.ready_backlog_critical",
        "reflection_capacity.oldest_ready_critical",
        "reflection_capacity.dead_letter_critical",
        "reflection_capacity.no_active_worker_with_backlog",
    )


def test_stale_and_errored_workers_are_warning_signals() -> None:
    report = assess_reflection_capacity(
        _snapshot(active=1, stale=1, errored=1),
        handler_version="reflection-v1",
        thresholds=_thresholds(),
    )

    assert report.status is ReflectionCapacityStatus.WARNING
    assert report.reasons == (
        "reflection_capacity.stale_workers_present",
        "reflection_capacity.worker_errors_present",
    )


def test_capacity_threshold_relationships_fail_closed() -> None:
    with pytest.raises(ValidationError, match="minimum_workers"):
        _thresholds(minimum_workers=3, maximum_workers=2)
    with pytest.raises(ValidationError, match="ready_warning"):
        _thresholds(ready_warning=501, ready_critical=500)


@pytest.mark.asyncio
async def test_capacity_application_pings_reads_and_closes_once() -> None:
    events: list[str] = []
    snapshot = _snapshot()
    source = CapacitySource(events, snapshot)
    sink = CapacityObservationSink(events)
    scanner = CapacityDriftScanner(events)
    application = ReflectionCapacityApplication(
        database=CapacityDatabase(events),
        source=source,
        handler_version="reflection-v1",
        thresholds=_thresholds(stale_after_seconds=90),
        observation_sink=sink,
        drift_scanner=scanner,
        incident_scanner=scanner,
    )

    report = await application.run()
    await application.aclose()
    await application.aclose()

    assert report.status is ReflectionCapacityStatus.HEALTHY
    assert sink.reports == [report]
    assert source.stale_after_seconds == 90
    assert events == [
        "database.ping",
        "source.capacity_snapshot",
        "sink.record_observation",
        "drift.scan",
        "incident.scan",
        "database.dispose",
    ]


@pytest.mark.asyncio
async def test_capacity_application_resolves_active_policy_on_every_run() -> None:
    events: list[str] = []
    source = CapacitySource(events, _snapshot(pending=2))
    fallback = _thresholds(stale_after_seconds=90, ready_warning=100)
    resolver = CapacityPolicyResolver(
        _thresholds(stale_after_seconds=30, ready_warning=1)
    )
    application = ReflectionCapacityApplication(
        database=CapacityDatabase(events),
        source=source,
        handler_version="reflection-v1",
        thresholds=fallback,
        policy_resolver=resolver,
    )

    report = await application.run()

    assert resolver.fallback == fallback
    assert source.stale_after_seconds == 30
    assert report.thresholds.ready_warning == 1
    assert report.status is ReflectionCapacityStatus.WARNING


@pytest.mark.asyncio
async def test_capacity_application_wires_read_only_knowledge_lifecycle_into_monitor() -> None:
    events: list[str] = []
    lifecycle = CapacityLifecycleScanner(events)
    application = ReflectionCapacityApplication(
        database=CapacityDatabase(events),
        source=CapacitySource(events, _snapshot()),
        handler_version="reflection-v1",
        thresholds=_thresholds(),
        lifecycle_scanner=lifecycle,
    )

    await application.run()

    assert application.last_lifecycle_report is not None
    assert application.last_lifecycle_report.overdue == 1
    assert events == [
        "database.ping",
        "source.capacity_snapshot",
        "lifecycle.scan",
    ]
