from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field

from public_agent.api.base import APIError, APIPrincipal
from public_agent.auth import AuthenticatedPrincipal
from public_agent.operations.capacity_control import (
    CapacityChangeRequestPage,
    CapacityChangeRequestQuery,
    CapacityDriftScanReport,
    CapacityGovernanceAlertPage,
    CapacityGovernanceAlertQuery,
    CapacityGovernanceAlertRecord,
    CapacityGovernanceAlertSeverity,
    CapacityGovernanceAlertSLA,
    CapacityGovernanceAlertStatus,
    CapacityGovernanceAuditOutcome,
    CapacityGovernanceAuditPage,
    CapacityGovernanceAuditQuery,
    CapacityGovernanceAuthorizationError,
    CapacityGovernanceCursorError,
    CapacityGovernanceDrillReport,
    CapacityGovernanceIncidentPage,
    CapacityGovernanceIncidentQuery,
    CapacityGovernanceIncidentRecord,
    CapacityGovernanceIncidentSeverity,
    CapacityGovernanceIncidentSignal,
    CapacityGovernanceIncidentStatus,
    CapacityGovernanceKnowledgeFeedbackInput,
    CapacityGovernanceKnowledgeFeedbackPage,
    CapacityGovernanceKnowledgeFeedbackQuery,
    CapacityGovernanceKnowledgeFeedbackReason,
    CapacityGovernanceKnowledgeFeedbackRecord,
    CapacityGovernanceKnowledgeFeedbackSignal,
    CapacityGovernanceKnowledgeFeedbackStatus,
    CapacityGovernanceKnowledgeQualityAssessment,
    CapacityGovernanceKnowledgeQualitySnapshotPage,
    CapacityGovernanceKnowledgeQualitySnapshotQuery,
    CapacityGovernanceKnowledgeQualitySnapshotRecord,
    CapacityGovernanceKnowledgeQualityTrendBucket,
    CapacityGovernanceKnowledgeQualityTrendQuery,
    CapacityGovernanceKnowledgeQualityTrendReport,
    CapacityGovernanceKnowledgeRecertificationDecision,
    CapacityGovernanceKnowledgeRecertificationInput,
    CapacityGovernanceKnowledgeRecertificationPage,
    CapacityGovernanceKnowledgeRecertificationQuery,
    CapacityGovernanceKnowledgeRecertificationReason,
    CapacityGovernanceKnowledgeRecertificationRecord,
    CapacityGovernanceKnowledgeRecertificationStatus,
    CapacityGovernanceKnowledgeRecoveryPage,
    CapacityGovernanceKnowledgeRecoveryQuery,
    CapacityGovernanceKnowledgeRecoveryReason,
    CapacityGovernanceKnowledgeRecoveryRecord,
    CapacityGovernanceKnowledgeRecoveryStatus,
    CapacityGovernancePostmortemImpact,
    CapacityGovernancePostmortemInput,
    CapacityGovernancePostmortemPage,
    CapacityGovernancePostmortemPrevention,
    CapacityGovernancePostmortemQuery,
    CapacityGovernancePostmortemRecord,
    CapacityGovernancePostmortemRootCause,
    CapacityGovernancePostmortemStatus,
    CapacityGovernanceRemediationEvidence,
    CapacityGovernanceRemediationExecutionResult,
    CapacityGovernanceRemediationPage,
    CapacityGovernanceRemediationPlaybook,
    CapacityGovernanceRemediationQuery,
    CapacityGovernanceRemediationRecord,
    CapacityGovernanceRemediationStatus,
    CapacityGovernanceRole,
    CapacityGovernanceSummary,
)
from public_agent.operations.capacity_governance import (
    ReflectionCapacityChangeRequestRecord,
    ReflectionCapacityChangeStatus,
    ReflectionCapacityGovernanceConflictError,
    ReflectionCapacityGovernanceNotFoundError,
    ReflectionCapacityGovernanceNotReadyError,
)


class CapacityGovernancePrincipal(APIPrincipal):
    """Trusted bearer identity for the capacity governance control plane."""


CapacityGovernancePrincipalDependency = Callable[
    ..., CapacityGovernancePrincipal | Awaitable[CapacityGovernancePrincipal]
]


class CapacityGovernanceService(Protocol):
    async def list_roles(
        self,
        *,
        actor: AuthenticatedPrincipal,
    ) -> tuple[CapacityGovernanceRole, ...]: ...

    async def summary(
        self,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceSummary: ...

    async def list_change_requests(
        self,
        query: CapacityChangeRequestQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityChangeRequestPage: ...

    async def get_change_request(
        self,
        request_id: UUID,
        *,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def create_change_request(
        self,
        *,
        calibration_id: UUID,
        window_required_seconds: int,
        window_minimum_observations: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def validate_window(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def approve(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def reject(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def publish(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        cooldown_seconds: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def review(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def rollback(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        reason: str,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord: ...

    async def scan_drift(
        self,
        *,
        actor: AuthenticatedPrincipal | None = None,
    ) -> CapacityDriftScanReport: ...

    async def list_alerts(
        self,
        query: CapacityGovernanceAlertQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceAlertPage: ...

    async def acknowledge_alert(
        self,
        *,
        alert_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceAlertRecord: ...

    async def list_audit_events(
        self,
        query: CapacityGovernanceAuditQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceAuditPage: ...

    async def governance_drill(
        self,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceDrillReport: ...

    async def list_incidents(
        self,
        query: CapacityGovernanceIncidentQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceIncidentPage: ...

    async def get_incident(
        self,
        incident_id: UUID,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceIncidentRecord: ...

    async def acknowledge_incident(
        self,
        *,
        incident_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceIncidentRecord: ...

    async def list_remediations(
        self,
        query: CapacityGovernanceRemediationQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationPage: ...

    async def get_remediation(
        self,
        remediation_id: UUID,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord: ...

    async def create_remediation(
        self,
        *,
        incident_id: UUID,
        expected_incident_version: int,
        playbook: CapacityGovernanceRemediationPlaybook,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord: ...

    async def approve_remediation(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord: ...

    async def reject_remediation(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord: ...

    async def record_remediation_execution(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        result: CapacityGovernanceRemediationExecutionResult,
        evidence: CapacityGovernanceRemediationEvidence,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord: ...

    async def verify_remediation(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord: ...

    async def list_postmortems(
        self,
        query: CapacityGovernancePostmortemQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemPage: ...

    async def get_postmortem(
        self,
        postmortem_id: UUID,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemRecord: ...

    async def create_postmortem(
        self,
        *,
        remediation_id: UUID,
        expected_remediation_version: int,
        content: CapacityGovernancePostmortemInput,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemRecord: ...

    async def approve_postmortem(
        self,
        *,
        postmortem_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemRecord: ...

    async def reject_postmortem(
        self,
        *,
        postmortem_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemRecord: ...

    async def list_knowledge_feedback(
        self,
        query: CapacityGovernanceKnowledgeFeedbackQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackPage: ...

    async def report_knowledge_feedback(
        self,
        *,
        postmortem_id: UUID,
        expected_postmortem_version: int,
        expected_knowledge_version: str,
        expected_content_fingerprint: str,
        content: CapacityGovernanceKnowledgeFeedbackInput,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackRecord: ...

    async def confirm_knowledge_feedback(
        self,
        *,
        feedback_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackRecord: ...

    async def dismiss_knowledge_feedback(
        self,
        *,
        feedback_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackRecord: ...

    async def list_knowledge_quality_snapshots(
        self,
        query: CapacityGovernanceKnowledgeQualitySnapshotQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeQualitySnapshotPage: ...

    async def capture_knowledge_quality_snapshot(
        self,
        *,
        postmortem_id: UUID,
        expected_postmortem_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeQualitySnapshotRecord: ...

    async def knowledge_quality_trend(
        self,
        query: CapacityGovernanceKnowledgeQualityTrendQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeQualityTrendReport: ...

    async def list_knowledge_recoveries(
        self,
        query: CapacityGovernanceKnowledgeRecoveryQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryPage: ...

    async def request_knowledge_recovery(
        self,
        *,
        postmortem_id: UUID,
        expected_postmortem_version: int,
        snapshot_id: UUID,
        reason: CapacityGovernanceKnowledgeRecoveryReason,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryRecord: ...

    async def approve_knowledge_recovery(
        self,
        *,
        recovery_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryRecord: ...

    async def reject_knowledge_recovery(
        self,
        *,
        recovery_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryRecord: ...

    async def list_knowledge_recertifications(
        self,
        query: CapacityGovernanceKnowledgeRecertificationQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecertificationPage: ...

    async def request_knowledge_recertification(
        self,
        *,
        content: CapacityGovernanceKnowledgeRecertificationInput,
        actor: AuthenticatedPrincipal,
        idempotency_key: str | None = None,
    ) -> CapacityGovernanceKnowledgeRecertificationRecord: ...

    async def review_knowledge_recertification(
        self, *, recertification_id: UUID, expected_version: int, actor: AuthenticatedPrincipal
    ) -> CapacityGovernanceKnowledgeRecertificationRecord: ...

    async def approve_knowledge_recertification(
        self, *, recertification_id: UUID, expected_version: int, actor: AuthenticatedPrincipal
    ) -> CapacityGovernanceKnowledgeRecertificationRecord: ...

    async def reject_knowledge_recertification(
        self, *, recertification_id: UUID, expected_version: int, actor: AuthenticatedPrincipal
    ) -> CapacityGovernanceKnowledgeRecertificationRecord: ...

    async def retire_knowledge(
        self, *, recertification_id: UUID, expected_version: int, actor: AuthenticatedPrincipal
    ) -> CapacityGovernanceKnowledgeRecertificationRecord: ...


class CapacityRequestCreateBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration_id: UUID
    window_required_seconds: int | None = Field(default=None, ge=60, le=2_592_000)
    window_minimum_observations: int | None = Field(
        default=None,
        ge=2,
        le=100_000,
    )


class ExpectedVersionBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=1)


class PublishCapacityRequestBody(ExpectedVersionBody):
    cooldown_seconds: int | None = Field(default=None, ge=60, le=2_592_000)


class RollbackCapacityRequestBody(ExpectedVersionBody):
    reason: str = Field(min_length=1, max_length=1_000)


class CapacityRemediationCreateBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_incident_version: int = Field(ge=1)
    playbook: CapacityGovernanceRemediationPlaybook


class CapacityRemediationExecutionBody(ExpectedVersionBody):
    result: CapacityGovernanceRemediationExecutionResult
    evidence: CapacityGovernanceRemediationEvidence


class CapacityPostmortemCreateBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_remediation_version: int = Field(ge=1)
    root_cause: CapacityGovernancePostmortemRootCause
    impact: CapacityGovernancePostmortemImpact
    prevention: CapacityGovernancePostmortemPrevention
    summary: str = Field(min_length=10, max_length=1_000)


class CapacityKnowledgeFeedbackCreateBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_postmortem_version: int = Field(ge=1)
    expected_knowledge_version: str = Field(min_length=1, max_length=100)
    expected_content_fingerprint: str = Field(min_length=64, max_length=64)
    signal: CapacityGovernanceKnowledgeFeedbackSignal
    reason: CapacityGovernanceKnowledgeFeedbackReason


class CapacityKnowledgeQualityCaptureBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_postmortem_version: int = Field(ge=1)


class CapacityKnowledgeRecoveryCreateBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_postmortem_version: int = Field(ge=1)
    snapshot_id: UUID
    reason: CapacityGovernanceKnowledgeRecoveryReason


class CapacityKnowledgeRecertificationCreateBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_postmortem_version: int = Field(ge=1)
    knowledge_version: str = Field(min_length=1, max_length=100)
    content_fingerprint: str = Field(min_length=64, max_length=64)
    quality_snapshot_id: UUID
    quality_evidence_fingerprint: str = Field(min_length=64, max_length=64)
    decision: CapacityGovernanceKnowledgeRecertificationDecision
    reason: CapacityGovernanceKnowledgeRecertificationReason


class CapacityAlertResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    handler_version: str
    alert_type: str
    severity: CapacityGovernanceAlertSeverity
    status: CapacityGovernanceAlertStatus
    version: int
    expected_policy_id: UUID | None
    expected_policy_version: int | None
    expected_fingerprint: str
    observed_fingerprint: str
    first_seen_at: datetime
    last_seen_at: datetime
    last_observation_at: datetime
    sample_count: int
    details: dict[str, str | int | float | bool | None]
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    reopened_count: int
    sla: CapacityGovernanceAlertSLA
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: CapacityGovernanceAlertRecord) -> CapacityAlertResponse:
        return cls(
            **record.model_dump(exclude={"acknowledged_principal_id", "acknowledged_token_id"})
        )


class CapacityAlertPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityAlertResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: CapacityGovernanceAlertPage) -> CapacityAlertPageResponse:
        return cls(
            items=tuple(CapacityAlertResponse.from_record(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


def install_capacity_governance_routes(
    app: FastAPI,
    *,
    service: CapacityGovernanceService,
    principal_dependency: CapacityGovernancePrincipalDependency,
    default_window_seconds: int,
    default_window_minimum_observations: int,
    default_cooldown_seconds: int,
) -> None:
    router = APIRouter(
        prefix="/v1/operations/capacity-governance",
        tags=["capacity-governance"],
    )
    principal_depends = Depends(principal_dependency)

    @router.get("/roles", response_model=tuple[CapacityGovernanceRole, ...])
    async def list_roles(
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> tuple[CapacityGovernanceRole, ...]:
        try:
            return await service.list_roles(actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get("/summary", response_model=CapacityGovernanceSummary)
    async def summary(
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceSummary:
        try:
            return await service.summary(actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get("/requests", response_model=CapacityChangeRequestPage)
    async def list_requests(
        request_status: Annotated[
            ReflectionCapacityChangeStatus | None,
            Query(alias="status"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityChangeRequestPage:
        try:
            return await service.list_change_requests(
                CapacityChangeRequestQuery(
                    status=request_status,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/requests",
        response_model=ReflectionCapacityChangeRequestRecord,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_request(
        body: CapacityRequestCreateBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> ReflectionCapacityChangeRequestRecord:
        try:
            return await service.create_change_request(
                calibration_id=body.calibration_id,
                window_required_seconds=(body.window_required_seconds or default_window_seconds),
                window_minimum_observations=(
                    body.window_minimum_observations or default_window_minimum_observations
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get(
        "/requests/{request_id}",
        response_model=ReflectionCapacityChangeRequestRecord,
    )
    async def get_request(
        request_id: UUID,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> ReflectionCapacityChangeRequestRecord:
        try:
            return await service.get_change_request(request_id, actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None

    def action_route(
        action: str,
    ) -> Callable[..., Awaitable[ReflectionCapacityChangeRequestRecord]]:
        async def execute(
            request_id: UUID,
            body: ExpectedVersionBody,
            current: CapacityGovernancePrincipal = principal_depends,
        ) -> ReflectionCapacityChangeRequestRecord:
            try:
                handler = cast(
                    Callable[..., Awaitable[ReflectionCapacityChangeRequestRecord]],
                    getattr(service, action),
                )
                return await handler(
                    request_id=request_id,
                    expected_version=body.expected_version,
                    actor=current,
                )
            except Exception as exc:
                raise _mapped_error(exc) from None

        return execute

    router.post(
        "/requests/{request_id}/validate",
        response_model=ReflectionCapacityChangeRequestRecord,
    )(action_route("validate_window"))
    router.post(
        "/requests/{request_id}/approve",
        response_model=ReflectionCapacityChangeRequestRecord,
    )(action_route("approve"))
    router.post(
        "/requests/{request_id}/reject",
        response_model=ReflectionCapacityChangeRequestRecord,
    )(action_route("reject"))
    router.post(
        "/requests/{request_id}/review",
        response_model=ReflectionCapacityChangeRequestRecord,
    )(action_route("review"))

    @router.post(
        "/requests/{request_id}/publish",
        response_model=ReflectionCapacityChangeRequestRecord,
    )
    async def publish_request(
        request_id: UUID,
        body: PublishCapacityRequestBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> ReflectionCapacityChangeRequestRecord:
        try:
            return await service.publish(
                request_id=request_id,
                expected_version=body.expected_version,
                cooldown_seconds=body.cooldown_seconds or default_cooldown_seconds,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/requests/{request_id}/rollback",
        response_model=ReflectionCapacityChangeRequestRecord,
    )
    async def rollback_request(
        request_id: UUID,
        body: RollbackCapacityRequestBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> ReflectionCapacityChangeRequestRecord:
        try:
            return await service.rollback(
                request_id=request_id,
                expected_version=body.expected_version,
                reason=body.reason,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post("/drift/scan", response_model=CapacityDriftScanReport)
    async def scan_drift(
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityDriftScanReport:
        try:
            return await service.scan_drift(actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get("/alerts", response_model=CapacityAlertPageResponse)
    async def list_alerts(
        alert_status: Annotated[
            CapacityGovernanceAlertStatus | None,
            Query(alias="status"),
        ] = None,
        severity: CapacityGovernanceAlertSeverity | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityAlertPageResponse:
        try:
            page = await service.list_alerts(
                CapacityGovernanceAlertQuery(
                    status=alert_status,
                    severity=severity,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return CapacityAlertPageResponse.from_page(page)

    @router.post(
        "/alerts/{alert_id}/acknowledge",
        response_model=CapacityAlertResponse,
    )
    async def acknowledge_alert(
        alert_id: UUID,
        body: ExpectedVersionBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityAlertResponse:
        try:
            record = await service.acknowledge_alert(
                alert_id=alert_id,
                expected_version=body.expected_version,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return CapacityAlertResponse.from_record(record)

    @router.get("/audit-events", response_model=CapacityGovernanceAuditPage)
    async def list_audit_events(
        actor_subject: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        action: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        outcome: CapacityGovernanceAuditOutcome | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceAuditPage:
        try:
            return await service.list_audit_events(
                CapacityGovernanceAuditQuery(
                    actor_subject=actor_subject,
                    action=action,
                    outcome=outcome,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get("/incidents", response_model=CapacityGovernanceIncidentPage)
    async def list_incidents(
        incident_status: Annotated[
            CapacityGovernanceIncidentStatus | None,
            Query(alias="status"),
        ] = None,
        severity: CapacityGovernanceIncidentSeverity | None = None,
        signal: CapacityGovernanceIncidentSignal | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceIncidentPage:
        try:
            return await service.list_incidents(
                CapacityGovernanceIncidentQuery(
                    signal=signal,
                    severity=severity,
                    status=incident_status,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get(
        "/incidents/{incident_id}",
        response_model=CapacityGovernanceIncidentRecord,
    )
    async def get_incident(
        incident_id: UUID,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceIncidentRecord:
        try:
            return await service.get_incident(incident_id, actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/incidents/{incident_id}/acknowledge",
        response_model=CapacityGovernanceIncidentRecord,
    )
    async def acknowledge_incident(
        incident_id: UUID,
        body: ExpectedVersionBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceIncidentRecord:
        try:
            return await service.acknowledge_incident(
                incident_id=incident_id,
                expected_version=body.expected_version,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get("/remediations", response_model=CapacityGovernanceRemediationPage)
    async def list_remediations(
        remediation_status: Annotated[
            CapacityGovernanceRemediationStatus | None,
            Query(alias="status"),
        ] = None,
        incident_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceRemediationPage:
        try:
            return await service.list_remediations(
                CapacityGovernanceRemediationQuery(
                    status=remediation_status,
                    incident_id=incident_id,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get(
        "/remediations/{remediation_id}",
        response_model=CapacityGovernanceRemediationRecord,
    )
    async def get_remediation(
        remediation_id: UUID,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceRemediationRecord:
        try:
            return await service.get_remediation(remediation_id, actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/incidents/{incident_id}/remediations",
        response_model=CapacityGovernanceRemediationRecord,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_remediation(
        incident_id: UUID,
        body: CapacityRemediationCreateBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceRemediationRecord:
        try:
            return await service.create_remediation(
                incident_id=incident_id,
                expected_incident_version=body.expected_incident_version,
                playbook=body.playbook,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    def remediation_action_route(
        action: str,
    ) -> Callable[..., Awaitable[CapacityGovernanceRemediationRecord]]:
        async def execute(
            remediation_id: UUID,
            body: ExpectedVersionBody,
            current: CapacityGovernancePrincipal = principal_depends,
        ) -> CapacityGovernanceRemediationRecord:
            try:
                handler = cast(
                    Callable[..., Awaitable[CapacityGovernanceRemediationRecord]],
                    getattr(service, action),
                )
                return await handler(
                    remediation_id=remediation_id,
                    expected_version=body.expected_version,
                    actor=current,
                )
            except Exception as exc:
                raise _mapped_error(exc) from None

        return execute

    router.post(
        "/remediations/{remediation_id}/approve",
        response_model=CapacityGovernanceRemediationRecord,
    )(remediation_action_route("approve_remediation"))
    router.post(
        "/remediations/{remediation_id}/reject",
        response_model=CapacityGovernanceRemediationRecord,
    )(remediation_action_route("reject_remediation"))
    router.post(
        "/remediations/{remediation_id}/verify",
        response_model=CapacityGovernanceRemediationRecord,
    )(remediation_action_route("verify_remediation"))

    @router.post(
        "/remediations/{remediation_id}/execution",
        response_model=CapacityGovernanceRemediationRecord,
    )
    async def record_remediation_execution(
        remediation_id: UUID,
        body: CapacityRemediationExecutionBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceRemediationRecord:
        try:
            return await service.record_remediation_execution(
                remediation_id=remediation_id,
                expected_version=body.expected_version,
                result=body.result,
                evidence=body.evidence,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get("/postmortems", response_model=CapacityGovernancePostmortemPage)
    async def list_postmortems(
        postmortem_status: Annotated[
            CapacityGovernancePostmortemStatus | None,
            Query(alias="status"),
        ] = None,
        incident_id: UUID | None = None,
        remediation_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernancePostmortemPage:
        try:
            return await service.list_postmortems(
                CapacityGovernancePostmortemQuery(
                    status=postmortem_status,
                    incident_id=incident_id,
                    remediation_id=remediation_id,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get(
        "/postmortems/{postmortem_id}",
        response_model=CapacityGovernancePostmortemRecord,
    )
    async def get_postmortem(
        postmortem_id: UUID,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernancePostmortemRecord:
        try:
            return await service.get_postmortem(postmortem_id, actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/remediations/{remediation_id}/postmortems",
        response_model=CapacityGovernancePostmortemRecord,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_postmortem(
        remediation_id: UUID,
        body: CapacityPostmortemCreateBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernancePostmortemRecord:
        try:
            return await service.create_postmortem(
                remediation_id=remediation_id,
                expected_remediation_version=body.expected_remediation_version,
                content=CapacityGovernancePostmortemInput(
                    root_cause=body.root_cause,
                    impact=body.impact,
                    prevention=body.prevention,
                    summary=body.summary,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    def postmortem_action_route(
        action: str,
    ) -> Callable[..., Awaitable[CapacityGovernancePostmortemRecord]]:
        async def execute(
            postmortem_id: UUID,
            body: ExpectedVersionBody,
            current: CapacityGovernancePrincipal = principal_depends,
        ) -> CapacityGovernancePostmortemRecord:
            try:
                handler = cast(
                    Callable[..., Awaitable[CapacityGovernancePostmortemRecord]],
                    getattr(service, action),
                )
                return await handler(
                    postmortem_id=postmortem_id,
                    expected_version=body.expected_version,
                    actor=current,
                )
            except Exception as exc:
                raise _mapped_error(exc) from None

        return execute

    router.post(
        "/postmortems/{postmortem_id}/approve",
        response_model=CapacityGovernancePostmortemRecord,
    )(postmortem_action_route("approve_postmortem"))
    router.post(
        "/postmortems/{postmortem_id}/reject",
        response_model=CapacityGovernancePostmortemRecord,
    )(postmortem_action_route("reject_postmortem"))

    @router.get(
        "/knowledge-feedback",
        response_model=CapacityGovernanceKnowledgeFeedbackPage,
    )
    async def list_knowledge_feedback(
        feedback_status: Annotated[
            CapacityGovernanceKnowledgeFeedbackStatus | None,
            Query(alias="status"),
        ] = None,
        signal: CapacityGovernanceKnowledgeFeedbackSignal | None = None,
        postmortem_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeFeedbackPage:
        try:
            return await service.list_knowledge_feedback(
                CapacityGovernanceKnowledgeFeedbackQuery(
                    status=feedback_status,
                    signal=signal,
                    postmortem_id=postmortem_id,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/postmortems/{postmortem_id}/feedback",
        response_model=CapacityGovernanceKnowledgeFeedbackRecord,
        status_code=status.HTTP_201_CREATED,
    )
    async def report_knowledge_feedback(
        postmortem_id: UUID,
        body: CapacityKnowledgeFeedbackCreateBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeFeedbackRecord:
        try:
            return await service.report_knowledge_feedback(
                postmortem_id=postmortem_id,
                expected_postmortem_version=body.expected_postmortem_version,
                expected_knowledge_version=body.expected_knowledge_version,
                expected_content_fingerprint=body.expected_content_fingerprint,
                content=CapacityGovernanceKnowledgeFeedbackInput(
                    signal=body.signal,
                    reason=body.reason,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    def knowledge_feedback_action_route(
        action: str,
    ) -> Callable[..., Awaitable[CapacityGovernanceKnowledgeFeedbackRecord]]:
        async def execute(
            feedback_id: UUID,
            body: ExpectedVersionBody,
            current: CapacityGovernancePrincipal = principal_depends,
        ) -> CapacityGovernanceKnowledgeFeedbackRecord:
            try:
                handler = cast(
                    Callable[..., Awaitable[CapacityGovernanceKnowledgeFeedbackRecord]],
                    getattr(service, action),
                )
                return await handler(
                    feedback_id=feedback_id,
                    expected_version=body.expected_version,
                    actor=current,
                )
            except Exception as exc:
                raise _mapped_error(exc) from None

        return execute

    router.post(
        "/knowledge-feedback/{feedback_id}/confirm",
        response_model=CapacityGovernanceKnowledgeFeedbackRecord,
    )(knowledge_feedback_action_route("confirm_knowledge_feedback"))
    router.post(
        "/knowledge-feedback/{feedback_id}/dismiss",
        response_model=CapacityGovernanceKnowledgeFeedbackRecord,
    )(knowledge_feedback_action_route("dismiss_knowledge_feedback"))

    @router.get(
        "/knowledge-quality-snapshots",
        response_model=CapacityGovernanceKnowledgeQualitySnapshotPage,
    )
    async def list_knowledge_quality_snapshots(
        assessment: CapacityGovernanceKnowledgeQualityAssessment | None = None,
        postmortem_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeQualitySnapshotPage:
        try:
            return await service.list_knowledge_quality_snapshots(
                CapacityGovernanceKnowledgeQualitySnapshotQuery(
                    assessment=assessment,
                    postmortem_id=postmortem_id,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/postmortems/{postmortem_id}/quality-snapshots",
        response_model=CapacityGovernanceKnowledgeQualitySnapshotRecord,
        status_code=status.HTTP_201_CREATED,
    )
    async def capture_knowledge_quality_snapshot(
        postmortem_id: UUID,
        body: CapacityKnowledgeQualityCaptureBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeQualitySnapshotRecord:
        try:
            return await service.capture_knowledge_quality_snapshot(
                postmortem_id=postmortem_id,
                expected_postmortem_version=body.expected_postmortem_version,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get(
        "/knowledge-quality-trend",
        response_model=CapacityGovernanceKnowledgeQualityTrendReport,
    )
    async def knowledge_quality_trend(
        captured_from: datetime,
        captured_to: datetime,
        bucket: CapacityGovernanceKnowledgeQualityTrendBucket = (
            CapacityGovernanceKnowledgeQualityTrendBucket.HOUR
        ),
        assessment: CapacityGovernanceKnowledgeQualityAssessment | None = None,
        limit: Annotated[int, Query(ge=1, le=366)] = 168,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeQualityTrendReport:
        try:
            return await service.knowledge_quality_trend(
                CapacityGovernanceKnowledgeQualityTrendQuery(
                    bucket=bucket,
                    captured_from=captured_from,
                    captured_to=captured_to,
                    assessment=assessment,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.get(
        "/knowledge-recoveries",
        response_model=CapacityGovernanceKnowledgeRecoveryPage,
    )
    async def list_knowledge_recoveries(
        recovery_status: Annotated[
            CapacityGovernanceKnowledgeRecoveryStatus | None,
            Query(alias="status"),
        ] = None,
        postmortem_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeRecoveryPage:
        try:
            return await service.list_knowledge_recoveries(
                CapacityGovernanceKnowledgeRecoveryQuery(
                    status=recovery_status,
                    postmortem_id=postmortem_id,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/postmortems/{postmortem_id}/recoveries",
        response_model=CapacityGovernanceKnowledgeRecoveryRecord,
        status_code=status.HTTP_201_CREATED,
    )
    async def request_knowledge_recovery(
        postmortem_id: UUID,
        body: CapacityKnowledgeRecoveryCreateBody,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeRecoveryRecord:
        try:
            return await service.request_knowledge_recovery(
                postmortem_id=postmortem_id,
                expected_postmortem_version=body.expected_postmortem_version,
                snapshot_id=body.snapshot_id,
                reason=body.reason,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    def knowledge_recovery_action_route(
        action: str,
    ) -> Callable[..., Awaitable[CapacityGovernanceKnowledgeRecoveryRecord]]:
        async def execute(
            recovery_id: UUID,
            body: ExpectedVersionBody,
            current: CapacityGovernancePrincipal = principal_depends,
        ) -> CapacityGovernanceKnowledgeRecoveryRecord:
            try:
                handler = cast(
                    Callable[..., Awaitable[CapacityGovernanceKnowledgeRecoveryRecord]],
                    getattr(service, action),
                )
                return await handler(
                    recovery_id=recovery_id,
                    expected_version=body.expected_version,
                    actor=current,
                )
            except Exception as exc:
                raise _mapped_error(exc) from None

        return execute

    router.post(
        "/knowledge-recoveries/{recovery_id}/approve",
        response_model=CapacityGovernanceKnowledgeRecoveryRecord,
    )(knowledge_recovery_action_route("approve_knowledge_recovery"))
    router.post(
        "/knowledge-recoveries/{recovery_id}/reject",
        response_model=CapacityGovernanceKnowledgeRecoveryRecord,
    )(knowledge_recovery_action_route("reject_knowledge_recovery"))

    @router.get(
        "/knowledge-recertifications",
        response_model=CapacityGovernanceKnowledgeRecertificationPage,
    )
    async def list_knowledge_recertifications(
        recertification_status: Annotated[
            CapacityGovernanceKnowledgeRecertificationStatus | None,
            Query(alias="status"),
        ] = None,
        postmortem_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeRecertificationPage:
        try:
            return await service.list_knowledge_recertifications(
                CapacityGovernanceKnowledgeRecertificationQuery(
                    status=recertification_status,
                    postmortem_id=postmortem_id,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    @router.post(
        "/postmortems/{postmortem_id}/recertifications",
        response_model=CapacityGovernanceKnowledgeRecertificationRecord,
        status_code=status.HTTP_201_CREATED,
    )
    async def request_knowledge_recertification(
        postmortem_id: UUID,
        body: CapacityKnowledgeRecertificationCreateBody,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceKnowledgeRecertificationRecord:
        try:
            content = CapacityGovernanceKnowledgeRecertificationInput(
                postmortem_id=postmortem_id,
                expected_postmortem_version=body.expected_postmortem_version,
                knowledge_version=body.knowledge_version,
                content_fingerprint=body.content_fingerprint,
                quality_snapshot_id=body.quality_snapshot_id,
                quality_evidence_fingerprint=body.quality_evidence_fingerprint,
                decision=body.decision,
                reason=body.reason,
            )
            return await service.request_knowledge_recertification(
                content=content,
                actor=current,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None

    def knowledge_recertification_action_route(
        action: str,
    ) -> Callable[..., Awaitable[CapacityGovernanceKnowledgeRecertificationRecord]]:
        async def execute(
            recertification_id: UUID,
            body: ExpectedVersionBody,
            current: CapacityGovernancePrincipal = principal_depends,
        ) -> CapacityGovernanceKnowledgeRecertificationRecord:
            try:
                handler = cast(
                    Callable[..., Awaitable[CapacityGovernanceKnowledgeRecertificationRecord]],
                    getattr(service, action),
                )
                return await handler(
                    recertification_id=recertification_id,
                    expected_version=body.expected_version,
                    actor=current,
                )
            except Exception as exc:
                raise _mapped_error(exc) from None

        return execute

    router.post(
        "/knowledge-recertifications/{recertification_id}/review",
        response_model=CapacityGovernanceKnowledgeRecertificationRecord,
    )(knowledge_recertification_action_route("review_knowledge_recertification"))
    router.post(
        "/knowledge-recertifications/{recertification_id}/approve",
        response_model=CapacityGovernanceKnowledgeRecertificationRecord,
    )(knowledge_recertification_action_route("approve_knowledge_recertification"))
    router.post(
        "/knowledge-recertifications/{recertification_id}/reject",
        response_model=CapacityGovernanceKnowledgeRecertificationRecord,
    )(knowledge_recertification_action_route("reject_knowledge_recertification"))
    router.post(
        "/knowledge-recertifications/{recertification_id}/retire",
        response_model=CapacityGovernanceKnowledgeRecertificationRecord,
    )(knowledge_recertification_action_route("retire_knowledge"))

    @router.get("/drill-report", response_model=CapacityGovernanceDrillReport)
    async def governance_drill(
        current: CapacityGovernancePrincipal = principal_depends,
    ) -> CapacityGovernanceDrillReport:
        try:
            return await service.governance_drill(actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None

    app.include_router(router)


def _mapped_error(exc: Exception) -> APIError:
    if isinstance(exc, APIError):
        return exc
    if isinstance(exc, CapacityGovernanceAuthorizationError):
        return APIError(
            status_code=403,
            code="capacity_governance_forbidden",
            message="The authenticated principal cannot perform this capacity action.",
        )
    if isinstance(exc, (KeyError, ReflectionCapacityGovernanceNotFoundError)):
        return APIError(
            status_code=404,
            code="capacity_governance_resource_not_found",
            message="The requested capacity governance resource was not found.",
        )
    if isinstance(exc, CapacityGovernanceCursorError):
        return APIError(
            status_code=400,
            code="invalid_cursor",
            message="The capacity governance cursor is invalid.",
        )
    if isinstance(
        exc,
        (ReflectionCapacityGovernanceConflictError, ReflectionCapacityGovernanceNotReadyError),
    ):
        return APIError(
            status_code=409,
            code=getattr(exc, "code", "capacity_governance_state_conflict"),
            message="The capacity governance resource cannot perform this transition.",
        )
    if isinstance(exc, ValueError):
        return APIError(
            status_code=400,
            code="invalid_capacity_governance_request",
            message="The capacity governance request is invalid.",
        )
    return APIError(
        status_code=500,
        code="capacity_governance_internal_error",
        message="The capacity governance operation could not be completed.",
    )
