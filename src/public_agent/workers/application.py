from __future__ import annotations

import asyncio
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from public_agent.config import Settings
from public_agent.core.model import ModelProvider
from public_agent.growth import (
    EvidenceBasedCandidateEvaluator,
    KnowledgeSedimentationPipeline,
    LearningService,
    ReflectionEngine,
)
from public_agent.operations.capacity import (
    ReflectionCapacityReport,
    ReflectionCapacityThresholds,
    assess_reflection_capacity,
)
from public_agent.operations.capacity_control import (
    CapacityGovernanceIncidentThresholds,
    CapacityGovernanceKnowledgeLifecycleScanReport,
    CapacityGovernanceKnowledgeQualityRiskThresholds,
)
from public_agent.providers import OpenAIModelProvider
from public_agent.storage.capacity_control import PostgresReflectionCapacityControl
from public_agent.storage.capacity_governance import (
    PostgresReflectionCapacityGovernance,
)
from public_agent.storage.capacity_history import PostgresReflectionCapacityHistory
from public_agent.storage.database import Database
from public_agent.storage.outbox import PostgresReflectionJobStore
from public_agent.storage.repositories import (
    PostgresKnowledgeAssetPublisher,
    PostgresLearningStore,
)
from public_agent.workers.reflection import ReflectionWorker
from public_agent.workers.runner import (
    ReflectionCapacitySnapshot,
    ReflectionWorkerRunner,
    ReflectionWorkerRunSummary,
)


class ReflectionWorkerConfigurationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReflectionWorkerOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str = Field(min_length=1, max_length=200)
    handler_version: str = Field(min_length=1, max_length=64)
    max_attempts: int = Field(ge=1, le=100)
    retry_base_seconds: int = Field(ge=1, le=86_400)
    retry_max_seconds: int = Field(ge=1, le=86_400)
    lease_seconds: int = Field(ge=5, le=3_600)
    heartbeat_seconds: int = Field(ge=1, le=3_599)
    poll_interval_seconds: float = Field(ge=0.05, le=60)
    poll_jitter_seconds: float = Field(ge=0, le=60)
    drain_timeout_seconds: float = Field(ge=1, le=3_600)

    @field_validator("worker_id", "handler_version")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reflection worker text options must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_relationships(self) -> ReflectionWorkerOptions:
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("retry_base_seconds must not exceed retry_max_seconds")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        if self.poll_jitter_seconds > self.poll_interval_seconds:
            raise ValueError("poll_jitter_seconds must not exceed poll_interval_seconds")
        return self

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        **overrides: object,
    ) -> ReflectionWorkerOptions:
        values: dict[str, object] = {
            "worker_id": settings.reflection_worker_id or default_reflection_worker_id(),
            "handler_version": settings.reflection_handler_version,
            "max_attempts": settings.reflection_worker_max_attempts,
            "retry_base_seconds": settings.reflection_worker_retry_base_seconds,
            "retry_max_seconds": settings.reflection_worker_retry_max_seconds,
            "lease_seconds": settings.reflection_worker_lease_seconds,
            "heartbeat_seconds": settings.reflection_worker_heartbeat_seconds,
            "poll_interval_seconds": settings.reflection_worker_poll_interval_seconds,
            "poll_jitter_seconds": settings.reflection_worker_poll_jitter_seconds,
            "drain_timeout_seconds": settings.reflection_worker_drain_timeout_seconds,
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls.model_validate(values)


class ReflectionWorkerRunnerProtocol(Protocol):
    async def run(self, *, stop_event: asyncio.Event) -> ReflectionWorkerRunSummary: ...


class DatabaseLifecycle(Protocol):
    async def ping(self) -> None: ...

    async def dispose(self) -> None: ...


class AsyncCloser(Protocol):
    async def aclose(self) -> None: ...


class ReflectionCapacitySource(Protocol):
    async def capacity_snapshot(
        self,
        *,
        stale_after_seconds: int,
    ) -> ReflectionCapacitySnapshot: ...


class ReflectionCapacityObservationSink(Protocol):
    async def record_observation(self, report: ReflectionCapacityReport) -> None: ...


class ReflectionCapacityDriftScanner(Protocol):
    async def scan_drift(self) -> object: ...


class ReflectionCapacityIncidentScanner(Protocol):
    async def scan_incidents(self) -> object: ...


class ReflectionCapacityKnowledgeLifecycleScanner(Protocol):
    async def scan_knowledge_lifecycle(self) -> CapacityGovernanceKnowledgeLifecycleScanReport: ...


class ReflectionCapacityPolicyResolver(Protocol):
    async def resolve_thresholds(
        self,
        fallback: ReflectionCapacityThresholds,
    ) -> ReflectionCapacityThresholds: ...


@dataclass(slots=True)
class ReflectionWorkerApplication:
    database: DatabaseLifecycle
    runner: ReflectionWorkerRunnerProtocol
    owned_provider: AsyncCloser | None = None
    owns_database: bool = True
    _closed: bool = field(default=False, init=False)

    async def run(self, *, stop_event: asyncio.Event) -> ReflectionWorkerRunSummary:
        await self.database.ping()
        return await self.runner.run(stop_event=stop_event)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        if self.owned_provider is not None:
            try:
                await self.owned_provider.aclose()
            except Exception as exc:
                first_error = exc
        if self.owns_database:
            try:
                await self.database.dispose()
            except Exception as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error


@dataclass(slots=True)
class ReflectionCapacityApplication:
    database: DatabaseLifecycle
    source: ReflectionCapacitySource
    handler_version: str
    thresholds: ReflectionCapacityThresholds
    policy_resolver: ReflectionCapacityPolicyResolver | None = None
    observation_sink: ReflectionCapacityObservationSink | None = None
    drift_scanner: ReflectionCapacityDriftScanner | None = None
    incident_scanner: ReflectionCapacityIncidentScanner | None = None
    lifecycle_scanner: ReflectionCapacityKnowledgeLifecycleScanner | None = None
    last_lifecycle_report: CapacityGovernanceKnowledgeLifecycleScanReport | None = field(
        default=None, init=False
    )
    owns_database: bool = True
    _closed: bool = field(default=False, init=False)

    async def run(self) -> ReflectionCapacityReport:
        await self.database.ping()
        thresholds = self.thresholds
        if self.policy_resolver is not None:
            thresholds = await self.policy_resolver.resolve_thresholds(thresholds)
        snapshot = await self.source.capacity_snapshot(
            stale_after_seconds=thresholds.stale_after_seconds,
        )
        report = assess_reflection_capacity(
            snapshot,
            handler_version=self.handler_version,
            thresholds=thresholds,
        )
        if self.observation_sink is not None:
            await self.observation_sink.record_observation(report)
        if self.drift_scanner is not None:
            await self.drift_scanner.scan_drift()
        if self.lifecycle_scanner is not None:
            self.last_lifecycle_report = await self.lifecycle_scanner.scan_knowledge_lifecycle()
        if self.incident_scanner is not None:
            await self.incident_scanner.scan_incidents()
        return report

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.owns_database:
            await self.database.dispose()


def build_reflection_worker_application(
    settings: Settings,
    options: ReflectionWorkerOptions,
    *,
    model: ModelProvider | None = None,
    database: Database | None = None,
) -> ReflectionWorkerApplication:
    owned_provider: OpenAIModelProvider | None = None
    resolved_model = model
    if resolved_model is None:
        api_key = _required_openai_api_key(settings.openai_api_key)
        owned_provider = OpenAIModelProvider(
            api_key=api_key,
            model=settings.openai_model,
            max_output_tokens=settings.openai_max_output_tokens,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
            retry_backoff_seconds=settings.openai_retry_backoff_seconds,
        )
        resolved_model = owned_provider
    resolved_database = database or Database(settings.database_url)

    learning_store = PostgresLearningStore(resolved_database.sessions)
    pipeline = KnowledgeSedimentationPipeline(
        learning=LearningService(learning_store),
        learning_store=learning_store,
        extractor=ReflectionEngine(model=resolved_model),
        evaluator=EvidenceBasedCandidateEvaluator(),
        publisher=PostgresKnowledgeAssetPublisher(resolved_database.sessions),
    )
    jobs = PostgresReflectionJobStore(
        resolved_database.sessions,
        handler_version=options.handler_version,
        max_attempts=options.max_attempts,
        retry_base_seconds=options.retry_base_seconds,
        retry_max_seconds=options.retry_max_seconds,
    )
    worker = ReflectionWorker(
        jobs=jobs,
        sedimentation=pipeline,
        lease_seconds=options.lease_seconds,
        heartbeat_seconds=options.heartbeat_seconds,
    )
    runner = ReflectionWorkerRunner(
        worker=worker,
        lifecycle=jobs,
        worker_id=options.worker_id,
        poll_interval_seconds=options.poll_interval_seconds,
        poll_jitter_seconds=options.poll_jitter_seconds,
        drain_timeout_seconds=options.drain_timeout_seconds,
    )
    return ReflectionWorkerApplication(
        database=resolved_database,
        runner=runner,
        owned_provider=owned_provider,
        owns_database=database is None,
    )


def build_reflection_capacity_application(
    settings: Settings,
    *,
    handler_version: str,
    thresholds: ReflectionCapacityThresholds,
    database: Database | None = None,
) -> ReflectionCapacityApplication:
    resolved_database = database or Database(settings.database_url)
    source = PostgresReflectionJobStore(
        resolved_database.sessions,
        handler_version=handler_version,
    )
    governance = PostgresReflectionCapacityGovernance(
        resolved_database.sessions,
        handler_version=source.handler_version,
    )
    capacity_control = PostgresReflectionCapacityControl(
        resolved_database.sessions,
        governance=governance,
        governance_tenant=settings.reflection_capacity_governance_tenant_id,
        fallback_thresholds=thresholds,
        drift_window_seconds=settings.reflection_capacity_drift_window_seconds,
        drift_minimum_observations=(
            settings.reflection_capacity_drift_minimum_observations
        ),
        drift_critical_observations=(
            settings.reflection_capacity_drift_critical_observations
        ),
        drift_maximum_observations=(
            settings.reflection_capacity_drift_maximum_observations
        ),
        alert_response_warning_seconds=(
            settings.reflection_capacity_alert_response_warning_seconds
        ),
        alert_response_critical_seconds=(
            settings.reflection_capacity_alert_response_critical_seconds
        ),
        incident_thresholds=CapacityGovernanceIncidentThresholds(
            audit_window_seconds=(
                settings.reflection_capacity_incident_audit_window_seconds
            ),
            audit_warning_count=(
                settings.reflection_capacity_incident_audit_warning_count
            ),
            audit_critical_count=(
                settings.reflection_capacity_incident_audit_critical_count
            ),
            audit_maximum_events=(
                settings.reflection_capacity_incident_audit_maximum_events
            ),
            reopen_warning_count=(
                settings.reflection_capacity_incident_reopen_warning_count
            ),
            reopen_critical_count=(
                settings.reflection_capacity_incident_reopen_critical_count
            ),
            maximum_alerts=(
                settings.reflection_capacity_incident_maximum_alerts
            ),
            maximum_incidents=(
                settings.reflection_capacity_incident_maximum_incidents
            ),
        ),
        knowledge_quality_risk_thresholds=(
            CapacityGovernanceKnowledgeQualityRiskThresholds(
                window_seconds=(
                    settings.reflection_capacity_knowledge_quality_risk_window_seconds
                ),
                unsafe_warning_count=(
                    settings.reflection_capacity_knowledge_unsafe_warning_count
                ),
                unsafe_critical_count=(
                    settings.reflection_capacity_knowledge_unsafe_critical_count
                ),
                degraded_warning_count=(
                    settings.reflection_capacity_knowledge_degraded_warning_count
                ),
                degraded_critical_count=(
                    settings.reflection_capacity_knowledge_degraded_critical_count
                ),
                maximum_snapshots=(
                    settings.reflection_capacity_knowledge_quality_maximum_snapshots
                ),
            )
        ),
        knowledge_quality_maximum_trend_buckets=(
            settings.reflection_capacity_knowledge_quality_maximum_trend_buckets
        ),
    )
    return ReflectionCapacityApplication(
        database=resolved_database,
        source=source,
        handler_version=source.handler_version,
        thresholds=thresholds,
        policy_resolver=governance,
        observation_sink=PostgresReflectionCapacityHistory(resolved_database.sessions),
        drift_scanner=capacity_control,
        lifecycle_scanner=capacity_control,
        incident_scanner=capacity_control,
        owns_database=database is None,
    )


def default_reflection_worker_id() -> str:
    hostname = re.sub(r"[^A-Za-z0-9_.-]+", "-", socket.gethostname()).strip(".-")
    normalized_hostname = hostname or "host"
    return f"reflection-{normalized_hostname[:160]}-{os.getpid()}"[:200]


def _required_openai_api_key(value: SecretStr | None) -> SecretStr:
    if value is None or not value.get_secret_value().strip():
        raise ReflectionWorkerConfigurationError(
            "reflection_worker.openai_api_key_missing"
        )
    return value
