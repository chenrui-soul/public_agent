from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from public_agent.auth import AuthenticatedPrincipal
from public_agent.core.types import utc_now
from public_agent.knowledge.base import (
    KNOWLEDGE_EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    TextSegmenter,
)
from public_agent.knowledge.embeddings import DeterministicHashEmbeddingProvider
from public_agent.knowledge.segmentation import JiebaChineseSegmenter, lexical_text
from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_control import (
    CAPACITY_ALERTS_MANAGE,
    CAPACITY_ALERTS_READ,
    CAPACITY_AUDIT_READ,
    CAPACITY_GOVERNANCE_APPROVE,
    CAPACITY_GOVERNANCE_PUBLISH,
    CAPACITY_GOVERNANCE_READ,
    CAPACITY_GOVERNANCE_REQUEST,
    CAPACITY_GOVERNANCE_REVIEW,
    CAPACITY_GOVERNANCE_ROLES,
    CAPACITY_GOVERNANCE_ROLLBACK,
    CAPACITY_INCIDENTS_MANAGE,
    CAPACITY_INCIDENTS_READ,
    CAPACITY_KNOWLEDGE_FEEDBACK_READ,
    CAPACITY_KNOWLEDGE_FEEDBACK_REPORT,
    CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
    CAPACITY_KNOWLEDGE_QUALITY_ASSESS,
    CAPACITY_KNOWLEDGE_QUALITY_READ,
    CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
    CAPACITY_KNOWLEDGE_RECERTIFICATION_REQUEST,
    CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW,
    CAPACITY_KNOWLEDGE_RECOVERY_READ,
    CAPACITY_KNOWLEDGE_RECOVERY_REQUEST,
    CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
    CAPACITY_KNOWLEDGE_RETIREMENT,
    CAPACITY_POSTMORTEMS_READ,
    CAPACITY_POSTMORTEMS_REQUEST,
    CAPACITY_POSTMORTEMS_REVIEW,
    CAPACITY_REMEDIATIONS_APPROVE,
    CAPACITY_REMEDIATIONS_EXECUTE,
    CAPACITY_REMEDIATIONS_READ,
    CAPACITY_REMEDIATIONS_REQUEST,
    CAPACITY_REMEDIATIONS_VERIFY,
    GOVERNANCE_KNOWLEDGE_NAMESPACE,
    GOVERNANCE_KNOWLEDGE_QUARANTINE_RETENTION,
    CapacityChangeRequestPage,
    CapacityChangeRequestQuery,
    CapacityDriftScanReport,
    CapacityGovernanceAlertPage,
    CapacityGovernanceAlertQuery,
    CapacityGovernanceAlertRecord,
    CapacityGovernanceAlertSeverity,
    CapacityGovernanceAlertStatus,
    CapacityGovernanceAlertType,
    CapacityGovernanceAuditOutcome,
    CapacityGovernanceAuditPage,
    CapacityGovernanceAuditQuery,
    CapacityGovernanceAuditRecord,
    CapacityGovernanceAuthorizationError,
    CapacityGovernanceCursorError,
    CapacityGovernanceDrillCheck,
    CapacityGovernanceDrillReport,
    CapacityGovernanceIncidentCandidate,
    CapacityGovernanceIncidentPage,
    CapacityGovernanceIncidentQuery,
    CapacityGovernanceIncidentRecord,
    CapacityGovernanceIncidentSeverity,
    CapacityGovernanceIncidentSignal,
    CapacityGovernanceIncidentStatus,
    CapacityGovernanceIncidentThresholds,
    CapacityGovernanceKnowledgeFeedbackInput,
    CapacityGovernanceKnowledgeFeedbackPage,
    CapacityGovernanceKnowledgeFeedbackQuery,
    CapacityGovernanceKnowledgeFeedbackReason,
    CapacityGovernanceKnowledgeFeedbackRecord,
    CapacityGovernanceKnowledgeFeedbackSignal,
    CapacityGovernanceKnowledgeFeedbackStatus,
    CapacityGovernanceKnowledgeQualityAssessment,
    CapacityGovernanceKnowledgeQualityRiskThresholds,
    CapacityGovernanceKnowledgeQualitySnapshotPage,
    CapacityGovernanceKnowledgeQualitySnapshotQuery,
    CapacityGovernanceKnowledgeQualitySnapshotRecord,
    CapacityGovernanceKnowledgeQualityTrendBucket,
    CapacityGovernanceKnowledgeQualityTrendPoint,
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
    CapacityIncidentScanReport,
    assess_capacity_alert_sla,
    build_alert_reopen_incident_candidate,
    build_alert_sla_incident_candidate,
    build_audit_failure_incident_candidate,
    build_drill_incident_candidates,
    build_persistent_unsafe_knowledge_incident_candidate,
    build_post_recovery_requarantine_incident_candidate,
    build_repeated_degraded_knowledge_incident_candidate,
    capacity_drift_dedupe_key,
    capacity_threshold_fingerprint,
    expected_remediation_evidence,
    expected_remediation_playbook,
    governance_knowledge_quality_assessment,
    postmortem_content_fingerprint,
    render_postmortem_knowledge_content,
    validate_postmortem_classification,
)
from public_agent.operations.capacity_governance import (
    ReflectionCapacityChangeRequestRecord,
    ReflectionCapacityGovernanceConflictError,
)
from public_agent.storage.authorization import (
    AuthorizedGlobalActor,
    authorize_global_operation,
)
from public_agent.storage.capacity_governance import (
    CapacityOperatorResolver,
    PostgresReflectionCapacityGovernance,
    _policy_record,
    _request_record,
)
from public_agent.storage.models import (
    APIPrincipalModel,
    ReflectionCapacityChangeRequestModel,
    ReflectionCapacityGovernanceAlertModel,
    ReflectionCapacityGovernanceAuditEventModel,
    ReflectionCapacityGovernanceIncidentModel,
    ReflectionCapacityGovernanceKnowledgeFeedbackModel,
    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel,
    ReflectionCapacityGovernanceKnowledgeRecertificationModel,
    ReflectionCapacityGovernanceKnowledgeRecoveryModel,
    ReflectionCapacityGovernancePostmortemModel,
    ReflectionCapacityGovernanceRemediationModel,
    ReflectionCapacityObservationModel,
    ReflectionCapacityPolicyModel,
    TenantModel,
)
from public_agent.storage.outbox import REFLECTION_JOB_TYPE


class PostgresReflectionCapacityControl:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        governance: PostgresReflectionCapacityGovernance,
        governance_tenant: str,
        fallback_thresholds: ReflectionCapacityThresholds,
        drift_window_seconds: int,
        drift_minimum_observations: int,
        drift_critical_observations: int,
        drift_maximum_observations: int = 10_000,
        alert_response_warning_seconds: int = 900,
        alert_response_critical_seconds: int = 3_600,
        incident_thresholds: CapacityGovernanceIncidentThresholds | None = None,
        knowledge_quality_risk_thresholds: (
            CapacityGovernanceKnowledgeQualityRiskThresholds | None
        ) = None,
        knowledge_quality_maximum_trend_buckets: int = 366,
        governance_embeddings: EmbeddingProvider | None = None,
        governance_segmenter: TextSegmenter | None = None,
    ) -> None:
        normalized_tenant = governance_tenant.strip()
        if not normalized_tenant or len(normalized_tenant) > 100:
            raise ValueError("governance_tenant must contain 1 to 100 characters")
        if not 60 <= drift_window_seconds <= 2_592_000:
            raise ValueError("drift_window_seconds must be between 60 and 2592000")
        if not 2 <= drift_minimum_observations <= drift_critical_observations:
            raise ValueError("drift observation thresholds must be ordered")
        if not drift_critical_observations <= drift_maximum_observations <= 100_000:
            raise ValueError("drift maximum observations must cover the critical threshold")
        if not (
            60 <= alert_response_warning_seconds <= alert_response_critical_seconds <= 2_592_000
        ):
            raise ValueError("alert response SLA thresholds must be ordered")
        self._sessions = sessions
        self.governance = governance
        self.handler_version = governance.handler_version
        self.governance_tenant = normalized_tenant
        self.fallback_thresholds = fallback_thresholds
        self.drift_window_seconds = drift_window_seconds
        self.drift_minimum_observations = drift_minimum_observations
        self.drift_critical_observations = drift_critical_observations
        self.drift_maximum_observations = drift_maximum_observations
        self.alert_response_warning_seconds = alert_response_warning_seconds
        self.alert_response_critical_seconds = alert_response_critical_seconds
        self.incident_thresholds = incident_thresholds or CapacityGovernanceIncidentThresholds()
        self.knowledge_quality_risk_thresholds = (
            knowledge_quality_risk_thresholds or CapacityGovernanceKnowledgeQualityRiskThresholds()
        )
        if not 1 <= knowledge_quality_maximum_trend_buckets <= 3_660:
            raise ValueError("knowledge quality maximum trend buckets must be between 1 and 3660")
        self.knowledge_quality_maximum_trend_buckets = knowledge_quality_maximum_trend_buckets
        self._governance_embeddings = governance_embeddings or DeterministicHashEmbeddingProvider()
        if self._governance_embeddings.profile.dimensions != KNOWLEDGE_EMBEDDING_DIMENSIONS:
            raise ValueError("governance knowledge embeddings must match PostgreSQL dimensions")
        self._governance_segmenter = governance_segmenter or JiebaChineseSegmenter()

    async def list_roles(
        self,
        *,
        actor: AuthenticatedPrincipal,
    ) -> tuple[CapacityGovernanceRole, ...]:
        async with self._sessions() as session, session.begin():
            await self._authorize(session, actor=actor, permission=CAPACITY_GOVERNANCE_READ)
        return CAPACITY_GOVERNANCE_ROLES

    async def summary(self, *, actor: AuthenticatedPrincipal) -> CapacityGovernanceSummary:
        async with self._sessions() as session, session.begin():
            await self._authorize(session, actor=actor, permission=CAPACITY_GOVERNANCE_READ)
            policy = await session.scalar(self._active_policy_statement())
            request_rows = (
                await session.execute(
                    select(
                        ReflectionCapacityChangeRequestModel.status,
                        func.count(ReflectionCapacityChangeRequestModel.id),
                    )
                    .where(*self._request_scope())
                    .group_by(ReflectionCapacityChangeRequestModel.status)
                )
            ).all()
            alert_rows = (
                await session.execute(
                    select(
                        ReflectionCapacityGovernanceAlertModel.status,
                        func.count(ReflectionCapacityGovernanceAlertModel.id),
                    )
                    .where(*self._alert_scope())
                    .group_by(ReflectionCapacityGovernanceAlertModel.status)
                )
            ).all()
            latest_alert_at = await session.scalar(
                select(func.max(ReflectionCapacityGovernanceAlertModel.updated_at)).where(
                    *self._alert_scope()
                )
            )
        return CapacityGovernanceSummary(
            handler_version=self.handler_version,
            active_policy=_policy_record(policy) if policy is not None else None,
            request_counts={status: int(count) for status, count in request_rows},
            alert_counts={status: int(count) for status, count in alert_rows},
            latest_alert_at=latest_alert_at,
        )

    async def list_change_requests(
        self,
        query: CapacityChangeRequestQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityChangeRequestPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_GOVERNANCE_READ,
            )
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="request",
                    filters={"status": query.status.value if query.status else None},
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters = self._request_scope()
            if query.status is not None:
                filters.append(ReflectionCapacityChangeRequestModel.status == query.status.value)
            if after is not None:
                updated_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityChangeRequestModel.updated_at < updated_at,
                        and_(
                            ReflectionCapacityChangeRequestModel.updated_at == updated_at,
                            ReflectionCapacityChangeRequestModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityChangeRequestModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityChangeRequestModel.updated_at.desc(),
                        ReflectionCapacityChangeRequestModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        return CapacityChangeRequestPage(
            items=tuple(_request_record(row) for row in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="request",
                    updated_at=page_rows[-1].updated_at,
                    item_id=page_rows[-1].id,
                    filters={"status": query.status.value if query.status else None},
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def get_change_request(
        self,
        request_id: UUID,
        *,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord:
        async with self._sessions() as session, session.begin():
            await self._authorize(session, actor=actor, permission=CAPACITY_GOVERNANCE_READ)
            row = await session.scalar(
                select(ReflectionCapacityChangeRequestModel).where(
                    ReflectionCapacityChangeRequestModel.id == request_id,
                    *self._request_scope(),
                )
            )
        if row is None:
            raise KeyError("Unknown capacity change request")
        return _request_record(row)

    async def create_change_request(
        self,
        *,
        calibration_id: UUID,
        window_required_seconds: int,
        window_minimum_observations: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord:
        return await self._governance_action(
            action="capacity.request.create",
            permission=CAPACITY_GOVERNANCE_REQUEST,
            actor=actor,
            target_request_id=None,
            operation=lambda resolver: self.governance.create_change_request(
                calibration_id=calibration_id,
                fallback_thresholds=self.fallback_thresholds,
                window_required_seconds=window_required_seconds,
                window_minimum_observations=window_minimum_observations,
                operator_resolver=resolver,
            ),
        )

    async def validate_window(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord:
        return await self._governance_action(
            action="capacity.request.validate",
            permission=CAPACITY_GOVERNANCE_REQUEST,
            actor=actor,
            target_request_id=request_id,
            operation=lambda resolver: self.governance.validate_window(
                request_id=request_id,
                expected_version=expected_version,
                operator_resolver=resolver,
            ),
        )

    async def approve(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord:
        return await self._governance_action(
            action="capacity.request.approve",
            permission=CAPACITY_GOVERNANCE_APPROVE,
            actor=actor,
            target_request_id=request_id,
            operation=lambda resolver: self.governance.approve(
                request_id=request_id,
                expected_version=expected_version,
                operator_resolver=resolver,
            ),
        )

    async def reject(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord:
        return await self._governance_action(
            action="capacity.request.reject",
            permission=CAPACITY_GOVERNANCE_APPROVE,
            actor=actor,
            target_request_id=request_id,
            operation=lambda resolver: self.governance.reject(
                request_id=request_id,
                expected_version=expected_version,
                operator_resolver=resolver,
            ),
        )

    async def publish(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        cooldown_seconds: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord:
        return await self._governance_action(
            action="capacity.request.publish",
            permission=CAPACITY_GOVERNANCE_PUBLISH,
            actor=actor,
            target_request_id=request_id,
            operation=lambda resolver: self.governance.publish(
                request_id=request_id,
                expected_version=expected_version,
                cooldown_seconds=cooldown_seconds,
                operator_resolver=resolver,
            ),
        )

    async def review(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord:
        return await self._governance_action(
            action="capacity.request.review",
            permission=CAPACITY_GOVERNANCE_REVIEW,
            actor=actor,
            target_request_id=request_id,
            operation=lambda resolver: self.governance.review(
                request_id=request_id,
                expected_version=expected_version,
                operator_resolver=resolver,
            ),
        )

    async def rollback(
        self,
        *,
        request_id: UUID,
        expected_version: int,
        reason: str,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionCapacityChangeRequestRecord:
        return await self._governance_action(
            action="capacity.request.rollback",
            permission=CAPACITY_GOVERNANCE_ROLLBACK,
            actor=actor,
            target_request_id=request_id,
            operation=lambda resolver: self.governance.rollback(
                request_id=request_id,
                expected_version=expected_version,
                reason=reason,
                operator_resolver=resolver,
            ),
        )

    async def list_alerts(
        self,
        query: CapacityGovernanceAlertQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceAlertPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_ALERTS_READ,
            )
            cursor_filters = {
                "severity": query.severity.value if query.severity else None,
                "status": query.status.value if query.status else None,
            }
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="alert",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters = self._alert_scope()
            if query.status is not None:
                filters.append(ReflectionCapacityGovernanceAlertModel.status == query.status.value)
            if query.severity is not None:
                filters.append(
                    ReflectionCapacityGovernanceAlertModel.severity == query.severity.value
                )
            if after is not None:
                updated_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernanceAlertModel.updated_at < updated_at,
                        and_(
                            ReflectionCapacityGovernanceAlertModel.updated_at == updated_at,
                            ReflectionCapacityGovernanceAlertModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceAlertModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityGovernanceAlertModel.updated_at.desc(),
                        ReflectionCapacityGovernanceAlertModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        return CapacityGovernanceAlertPage(
            items=tuple(
                _alert_record(
                    row,
                    response_warning_seconds=self.alert_response_warning_seconds,
                    response_critical_seconds=self.alert_response_critical_seconds,
                )
                for row in page_rows
            ),
            next_cursor=(
                _encode_cursor(
                    kind="alert",
                    updated_at=page_rows[-1].updated_at,
                    item_id=page_rows[-1].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def list_audit_events(
        self,
        query: CapacityGovernanceAuditQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceAuditPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_AUDIT_READ,
            )
            cursor_filters = _audit_cursor_filters(query)
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="audit",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters: list[ColumnElement[bool]] = [
                ReflectionCapacityGovernanceAuditEventModel.tenant_id == scope.tenant_id,
                ReflectionCapacityGovernanceAuditEventModel.handler_version == self.handler_version,
            ]
            if query.actor_subject is not None:
                filters.append(APIPrincipalModel.subject == query.actor_subject)
            if query.action is not None:
                filters.append(ReflectionCapacityGovernanceAuditEventModel.action == query.action)
            if query.outcome is not None:
                filters.append(
                    ReflectionCapacityGovernanceAuditEventModel.outcome == query.outcome.value
                )
            if query.occurred_from is not None:
                filters.append(
                    ReflectionCapacityGovernanceAuditEventModel.created_at >= query.occurred_from
                )
            if query.occurred_to is not None:
                filters.append(
                    ReflectionCapacityGovernanceAuditEventModel.created_at <= query.occurred_to
                )
            if after is not None:
                created_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernanceAuditEventModel.created_at < created_at,
                        and_(
                            ReflectionCapacityGovernanceAuditEventModel.created_at == created_at,
                            ReflectionCapacityGovernanceAuditEventModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                (
                    await session.execute(
                        select(
                            ReflectionCapacityGovernanceAuditEventModel,
                            APIPrincipalModel.subject,
                        )
                        .outerjoin(
                            APIPrincipalModel,
                            and_(
                                APIPrincipalModel.id
                                == ReflectionCapacityGovernanceAuditEventModel.actor_principal_id,
                                APIPrincipalModel.tenant_id
                                == ReflectionCapacityGovernanceAuditEventModel.tenant_id,
                            ),
                        )
                        .where(*filters)
                        .order_by(
                            ReflectionCapacityGovernanceAuditEventModel.created_at.desc(),
                            ReflectionCapacityGovernanceAuditEventModel.id.desc(),
                        )
                        .limit(query.limit + 1)
                    )
                ).all()
            )
        page_rows = rows[: query.limit]
        return CapacityGovernanceAuditPage(
            items=tuple(_audit_record(row, subject) for row, subject in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="audit",
                    updated_at=page_rows[-1][0].created_at,
                    item_id=page_rows[-1][0].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def list_incidents(
        self,
        query: CapacityGovernanceIncidentQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceIncidentPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_INCIDENTS_READ,
            )
            cursor_filters = _incident_cursor_filters(query)
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="incident",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters = self._incident_scope(scope.tenant_id)
            if query.signal is not None:
                filters.append(
                    ReflectionCapacityGovernanceIncidentModel.signal == query.signal.value
                )
            if query.severity is not None:
                filters.append(
                    ReflectionCapacityGovernanceIncidentModel.severity == query.severity.value
                )
            if query.status is not None:
                filters.append(
                    ReflectionCapacityGovernanceIncidentModel.status == query.status.value
                )
            if after is not None:
                updated_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernanceIncidentModel.updated_at < updated_at,
                        and_(
                            ReflectionCapacityGovernanceIncidentModel.updated_at == updated_at,
                            ReflectionCapacityGovernanceIncidentModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceIncidentModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityGovernanceIncidentModel.updated_at.desc(),
                        ReflectionCapacityGovernanceIncidentModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        return CapacityGovernanceIncidentPage(
            items=tuple(_incident_record(row) for row in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="incident",
                    updated_at=page_rows[-1].updated_at,
                    item_id=page_rows[-1].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def get_incident(
        self,
        incident_id: UUID,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceIncidentRecord:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_INCIDENTS_READ,
            )
            row = await session.scalar(
                select(ReflectionCapacityGovernanceIncidentModel).where(
                    ReflectionCapacityGovernanceIncidentModel.id == incident_id,
                    *self._incident_scope(scope.tenant_id),
                )
            )
        if row is None:
            raise KeyError("Unknown capacity governance incident")
        return _incident_record(row)

    async def governance_drill(
        self,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceDrillReport:
        checked_at = utc_now()
        async with self._sessions() as session, session.begin():
            await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_AUDIT_READ,
            )
            return await self._incident_drill_report(session, checked_at=checked_at)

    async def acknowledge_incident(
        self,
        *,
        incident_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceIncidentRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_INCIDENTS_MANAGE,
                    for_update=True,
                )
                row = await session.scalar(
                    select(ReflectionCapacityGovernanceIncidentModel)
                    .where(
                        ReflectionCapacityGovernanceIncidentModel.id == incident_id,
                        *self._incident_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance incident")
                if (
                    row.version != expected_version
                    or row.status == CapacityGovernanceIncidentStatus.RESOLVED.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Capacity governance incident changed before acknowledgement"
                    )
                if row.status == CapacityGovernanceIncidentStatus.OPEN.value:
                    now = utc_now()
                    row.status = CapacityGovernanceIncidentStatus.ACKNOWLEDGED.value
                    row.acknowledged_by = authorized.subject
                    row.acknowledged_principal_id = authorized.principal_id
                    row.acknowledged_token_id = authorized.token_id
                    row.acknowledged_at = now
                    row.version += 1
                    row.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.incident.acknowledge",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=row.id,
                )
            return _incident_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.incident.acknowledge",
                exc=exc,
                incident_id=incident_id,
            )
            raise

    async def list_remediations(
        self,
        query: CapacityGovernanceRemediationQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_REMEDIATIONS_READ,
            )
            cursor_filters = _remediation_cursor_filters(query)
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="remediation",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters = self._remediation_scope(scope.tenant_id)
            if query.status is not None:
                filters.append(
                    ReflectionCapacityGovernanceRemediationModel.status == query.status.value
                )
            if query.incident_id is not None:
                filters.append(
                    ReflectionCapacityGovernanceRemediationModel.incident_id == query.incident_id
                )
            if after is not None:
                updated_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernanceRemediationModel.updated_at < updated_at,
                        and_(
                            ReflectionCapacityGovernanceRemediationModel.updated_at == updated_at,
                            ReflectionCapacityGovernanceRemediationModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceRemediationModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityGovernanceRemediationModel.updated_at.desc(),
                        ReflectionCapacityGovernanceRemediationModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        return CapacityGovernanceRemediationPage(
            items=tuple(_remediation_record(row) for row in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="remediation",
                    updated_at=page_rows[-1].updated_at,
                    item_id=page_rows[-1].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def get_remediation(
        self,
        remediation_id: UUID,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_REMEDIATIONS_READ,
            )
            row = await session.scalar(
                select(ReflectionCapacityGovernanceRemediationModel).where(
                    ReflectionCapacityGovernanceRemediationModel.id == remediation_id,
                    *self._remediation_scope(scope.tenant_id),
                )
            )
        if row is None:
            raise KeyError("Unknown capacity governance remediation")
        return _remediation_record(row)

    async def create_remediation(
        self,
        *,
        incident_id: UUID,
        expected_incident_version: int,
        playbook: CapacityGovernanceRemediationPlaybook,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord:
        if expected_incident_version < 1:
            raise ValueError("expected_incident_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_REMEDIATIONS_REQUEST,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                incident = await session.scalar(
                    select(ReflectionCapacityGovernanceIncidentModel)
                    .where(
                        ReflectionCapacityGovernanceIncidentModel.id == incident_id,
                        *self._incident_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if incident is None:
                    raise KeyError("Unknown capacity governance incident")
                if (
                    incident.version != expected_incident_version
                    or incident.status != CapacityGovernanceIncidentStatus.ACKNOWLEDGED.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Incident must be acknowledged at the expected version"
                    )
                expected_playbook = expected_remediation_playbook(
                    CapacityGovernanceIncidentSignal(incident.signal)
                )
                if playbook is not expected_playbook:
                    raise ValueError("playbook does not match the incident signal")
                existing = await session.scalar(
                    select(ReflectionCapacityGovernanceRemediationModel.id).where(
                        ReflectionCapacityGovernanceRemediationModel.incident_id == incident.id,
                        ReflectionCapacityGovernanceRemediationModel.incident_cycle
                        == incident.reopened_count,
                    )
                )
                if existing is not None:
                    raise ReflectionCapacityGovernanceConflictError(
                        "A remediation already exists for this incident cycle"
                    )
                now = utc_now()
                row = ReflectionCapacityGovernanceRemediationModel(
                    id=uuid4(),
                    tenant_id=authorized.tenant_id,
                    incident_id=incident.id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=self.handler_version,
                    incident_cycle=incident.reopened_count,
                    playbook=playbook.value,
                    status=CapacityGovernanceRemediationStatus.AWAITING_APPROVAL.value,
                    version=1,
                    requested_by=authorized.subject,
                    requested_principal_id=authorized.principal_id,
                    requested_token_id=authorized.token_id,
                    requested_at=now,
                )
                session.add(row)
                await session.flush()
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.remediation.request",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=incident.id,
                    metadata={"playbook": playbook.value},
                )
            return _remediation_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.remediation.request",
                exc=exc,
                incident_id=incident_id,
            )
            raise

    async def approve_remediation(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord:
        return await self._decide_remediation(
            remediation_id=remediation_id,
            expected_version=expected_version,
            approve=True,
            actor=actor,
        )

    async def reject_remediation(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord:
        return await self._decide_remediation(
            remediation_id=remediation_id,
            expected_version=expected_version,
            approve=False,
            actor=actor,
        )

    async def _decide_remediation(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        approve: bool,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        action = "capacity.remediation.approve" if approve else "capacity.remediation.reject"
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_REMEDIATIONS_APPROVE,
                    for_update=True,
                )
                row = await session.scalar(
                    select(ReflectionCapacityGovernanceRemediationModel)
                    .where(
                        ReflectionCapacityGovernanceRemediationModel.id == remediation_id,
                        *self._remediation_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance remediation")
                if (
                    row.version != expected_version
                    or row.status != CapacityGovernanceRemediationStatus.AWAITING_APPROVAL.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Remediation changed before approval decision"
                    )
                if row.requested_principal_id == authorized.principal_id:
                    raise CapacityGovernanceAuthorizationError(
                        "Remediation requester cannot approve or reject the request"
                    )
                now = utc_now()
                if approve:
                    row.status = CapacityGovernanceRemediationStatus.APPROVED.value
                    row.approved_by = authorized.subject
                    row.approved_principal_id = authorized.principal_id
                    row.approved_token_id = authorized.token_id
                    row.approved_at = now
                else:
                    row.status = CapacityGovernanceRemediationStatus.REJECTED.value
                    row.rejected_by = authorized.subject
                    row.rejected_principal_id = authorized.principal_id
                    row.rejected_token_id = authorized.token_id
                    row.rejected_at = now
                row.version += 1
                row.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action=action,
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=row.incident_id,
                    metadata={"remediation_status": row.status},
                )
            return _remediation_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action=action,
                exc=exc,
            )
            raise

    async def record_remediation_execution(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        result: CapacityGovernanceRemediationExecutionResult,
        evidence: CapacityGovernanceRemediationEvidence,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_REMEDIATIONS_EXECUTE,
                    for_update=True,
                )
                row = await session.scalar(
                    select(ReflectionCapacityGovernanceRemediationModel)
                    .where(
                        ReflectionCapacityGovernanceRemediationModel.id == remediation_id,
                        *self._remediation_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance remediation")
                if (
                    row.version != expected_version
                    or row.status != CapacityGovernanceRemediationStatus.APPROVED.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Remediation changed before execution recording"
                    )
                if evidence is not expected_remediation_evidence(
                    CapacityGovernanceRemediationPlaybook(row.playbook)
                ):
                    raise ValueError("execution evidence does not match the playbook")
                incident = await session.scalar(
                    select(ReflectionCapacityGovernanceIncidentModel)
                    .where(
                        ReflectionCapacityGovernanceIncidentModel.id == row.incident_id,
                        *self._incident_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if incident is None or incident.reopened_count != row.incident_cycle:
                    raise ReflectionCapacityGovernanceConflictError(
                        "Incident cycle changed before execution recording"
                    )
                now = utc_now()
                row.status = (
                    CapacityGovernanceRemediationStatus.VERIFICATION_PENDING.value
                    if result is CapacityGovernanceRemediationExecutionResult.COMPLETED
                    else CapacityGovernanceRemediationStatus.FAILED.value
                )
                row.executed_by = authorized.subject
                row.executed_principal_id = authorized.principal_id
                row.executed_token_id = authorized.token_id
                row.executed_at = now
                row.execution_result = result.value
                row.execution_evidence = evidence.value
                row.incident_version_at_execution = incident.version
                row.version += 1
                row.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.remediation.execute.record",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=row.incident_id,
                    metadata={
                        "execution_result": result.value,
                        "remediation_status": row.status,
                    },
                )
            return _remediation_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.remediation.execute.record",
                exc=exc,
            )
            raise

    async def verify_remediation(
        self,
        *,
        remediation_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceRemediationRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_REMEDIATIONS_VERIFY,
                    for_update=True,
                )
                row = await session.scalar(
                    select(ReflectionCapacityGovernanceRemediationModel)
                    .where(
                        ReflectionCapacityGovernanceRemediationModel.id == remediation_id,
                        *self._remediation_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance remediation")
                if (
                    row.version != expected_version
                    or row.status != CapacityGovernanceRemediationStatus.VERIFICATION_PENDING.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Remediation changed before verification"
                    )
                if row.executed_principal_id == authorized.principal_id:
                    raise CapacityGovernanceAuthorizationError(
                        "Remediation executor cannot verify the same remediation"
                    )
                incident = await session.scalar(
                    select(ReflectionCapacityGovernanceIncidentModel)
                    .where(
                        ReflectionCapacityGovernanceIncidentModel.id == row.incident_id,
                        *self._incident_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if (
                    incident is None
                    or incident.reopened_count != row.incident_cycle
                    or incident.status != CapacityGovernanceIncidentStatus.RESOLVED.value
                    or incident.resolved_at is None
                    or row.executed_at is None
                    or incident.resolved_at <= row.executed_at
                    or row.incident_version_at_execution is None
                    or incident.version <= row.incident_version_at_execution
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Verification requires a newer resolved incident fact"
                    )
                now = utc_now()
                row.status = CapacityGovernanceRemediationStatus.VERIFIED.value
                row.verified_by = authorized.subject
                row.verified_principal_id = authorized.principal_id
                row.verified_token_id = authorized.token_id
                row.verified_at = now
                row.version += 1
                row.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.remediation.verify",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=row.incident_id,
                    metadata={"remediation_status": row.status},
                )
            return _remediation_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.remediation.verify",
                exc=exc,
            )
            raise

    async def list_postmortems(
        self,
        query: CapacityGovernancePostmortemQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_POSTMORTEMS_READ,
            )
            cursor_filters = _postmortem_cursor_filters(query)
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="postmortem",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters = self._postmortem_scope(scope.tenant_id)
            if query.status is not None:
                filters.append(
                    ReflectionCapacityGovernancePostmortemModel.status == query.status.value
                )
            if query.incident_id is not None:
                filters.append(
                    ReflectionCapacityGovernancePostmortemModel.incident_id == query.incident_id
                )
            if query.remediation_id is not None:
                filters.append(
                    ReflectionCapacityGovernancePostmortemModel.remediation_id
                    == query.remediation_id
                )
            if after is not None:
                updated_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernancePostmortemModel.updated_at < updated_at,
                        and_(
                            ReflectionCapacityGovernancePostmortemModel.updated_at == updated_at,
                            ReflectionCapacityGovernancePostmortemModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityGovernancePostmortemModel.updated_at.desc(),
                        ReflectionCapacityGovernancePostmortemModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        return CapacityGovernancePostmortemPage(
            items=tuple(_postmortem_record(row) for row in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="postmortem",
                    updated_at=page_rows[-1].updated_at,
                    item_id=page_rows[-1].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def get_postmortem(
        self,
        postmortem_id: UUID,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemRecord:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_POSTMORTEMS_READ,
            )
            row = await session.scalar(
                select(ReflectionCapacityGovernancePostmortemModel).where(
                    ReflectionCapacityGovernancePostmortemModel.id == postmortem_id,
                    *self._postmortem_scope(scope.tenant_id),
                )
            )
        if row is None:
            raise KeyError("Unknown capacity governance postmortem")
        return _postmortem_record(row)

    async def create_postmortem(
        self,
        *,
        remediation_id: UUID,
        expected_remediation_version: int,
        content: CapacityGovernancePostmortemInput,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemRecord:
        if expected_remediation_version < 1:
            raise ValueError("expected_remediation_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_POSTMORTEMS_REQUEST,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                remediation = await session.scalar(
                    select(ReflectionCapacityGovernanceRemediationModel)
                    .where(
                        ReflectionCapacityGovernanceRemediationModel.id == remediation_id,
                        *self._remediation_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if remediation is None:
                    raise KeyError("Unknown capacity governance remediation")
                if (
                    remediation.version != expected_remediation_version
                    or remediation.status != CapacityGovernanceRemediationStatus.VERIFIED.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Postmortems require a verified remediation at the expected version"
                    )
                incident = await session.scalar(
                    select(ReflectionCapacityGovernanceIncidentModel)
                    .where(
                        ReflectionCapacityGovernanceIncidentModel.id == remediation.incident_id,
                        *self._incident_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if (
                    incident is None
                    or incident.reopened_count != remediation.incident_cycle
                    or incident.status != CapacityGovernanceIncidentStatus.RESOLVED.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Postmortem source incident cycle is no longer resolved"
                    )
                validate_postmortem_classification(
                    CapacityGovernanceRemediationPlaybook(remediation.playbook),
                    content,
                )
                existing = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel.id).where(
                        ReflectionCapacityGovernancePostmortemModel.tenant_id
                        == authorized.tenant_id,
                        ReflectionCapacityGovernancePostmortemModel.remediation_id
                        == remediation.id,
                    )
                )
                if existing is not None:
                    raise ReflectionCapacityGovernanceConflictError(
                        "A postmortem already exists for this remediation"
                    )
                fingerprint = postmortem_content_fingerprint(
                    incident_id=incident.id,
                    incident_cycle=incident.reopened_count,
                    incident_version=incident.version,
                    remediation_id=remediation.id,
                    remediation_version=remediation.version,
                    content=content,
                )
                now = utc_now()
                row = ReflectionCapacityGovernancePostmortemModel(
                    id=uuid4(),
                    tenant_id=authorized.tenant_id,
                    incident_id=incident.id,
                    remediation_id=remediation.id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=self.handler_version,
                    incident_cycle=incident.reopened_count,
                    incident_version=incident.version,
                    remediation_version=remediation.version,
                    status=CapacityGovernancePostmortemStatus.AWAITING_REVIEW.value,
                    version=1,
                    root_cause=content.root_cause.value,
                    impact=content.impact.value,
                    prevention=content.prevention.value,
                    summary=content.summary,
                    content_fingerprint=fingerprint,
                    requested_by=authorized.subject,
                    requested_principal_id=authorized.principal_id,
                    requested_token_id=authorized.token_id,
                    requested_at=now,
                )
                session.add(row)
                await session.flush()
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.postmortem.request",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=incident.id,
                    postmortem_id=row.id,
                    metadata={"postmortem_status": row.status},
                )
            return _postmortem_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.postmortem.request",
                exc=exc,
                postmortem_id=None,
            )
            raise

    async def approve_postmortem(
        self,
        *,
        postmortem_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_POSTMORTEMS_REVIEW,
                )
                row = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel).where(
                        ReflectionCapacityGovernancePostmortemModel.id == postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                )
                if row is None:
                    raise KeyError("Unknown capacity governance postmortem")
                await self._validate_postmortem_review(
                    session,
                    row=row,
                    authorized=authorized,
                    expected_version=expected_version,
                    for_update=False,
                )
                knowledge_content = render_postmortem_knowledge_content(_postmortem_record(row))
            embedding = await self._governance_embeddings.embed(knowledge_content)
            if len(embedding) != KNOWLEDGE_EMBEDDING_DIMENSIONS or not all(
                math.isfinite(value) for value in embedding
            ):
                raise ValueError("governance knowledge embedding is invalid")
            indexed_text = lexical_text(self._governance_segmenter, knowledge_content)
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_POSTMORTEMS_REVIEW,
                    for_update=True,
                )
                row = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance postmortem")
                await self._validate_postmortem_review(
                    session,
                    row=row,
                    authorized=authorized,
                    expected_version=expected_version,
                    for_update=True,
                )
                now = utc_now()
                row.status = CapacityGovernancePostmortemStatus.PUBLISHED.value
                row.reviewed_by = authorized.subject
                row.reviewed_principal_id = authorized.principal_id
                row.reviewed_token_id = authorized.token_id
                row.reviewed_at = now
                row.knowledge_namespace = GOVERNANCE_KNOWLEDGE_NAMESPACE
                row.knowledge_source_key = f"governance-postmortem:{row.id}"
                row.knowledge_version = (
                    f"{row.incident_version}-{row.remediation_version}-"
                    f"{row.content_fingerprint[:12]}"
                )
                row.published_content = knowledge_content
                row.lexical_text = indexed_text
                row.lexical_profile = self._governance_segmenter.profile
                row.embedding_profile = self._governance_embeddings.profile.name
                row.embedding_dimensions = self._governance_embeddings.profile.dimensions
                row.embedding = list(embedding)
                row.published_at = now
                row.version += 1
                row.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.postmortem.publish",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=row.incident_id,
                    postmortem_id=row.id,
                    metadata={"postmortem_status": row.status},
                )
            return _postmortem_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.postmortem.publish",
                exc=exc,
                postmortem_id=postmortem_id,
            )
            raise

    async def reject_postmortem(
        self,
        *,
        postmortem_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernancePostmortemRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_POSTMORTEMS_REVIEW,
                    for_update=True,
                )
                row = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance postmortem")
                await self._validate_postmortem_review(
                    session,
                    row=row,
                    authorized=authorized,
                    expected_version=expected_version,
                    for_update=True,
                )
                now = utc_now()
                row.status = CapacityGovernancePostmortemStatus.REJECTED.value
                row.reviewed_by = authorized.subject
                row.reviewed_principal_id = authorized.principal_id
                row.reviewed_token_id = authorized.token_id
                row.reviewed_at = now
                row.version += 1
                row.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.postmortem.reject",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=row.incident_id,
                    postmortem_id=row.id,
                    metadata={"postmortem_status": row.status},
                )
            return _postmortem_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.postmortem.reject",
                exc=exc,
                postmortem_id=postmortem_id,
            )
            raise

    async def list_knowledge_feedback(
        self,
        query: CapacityGovernanceKnowledgeFeedbackQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_KNOWLEDGE_FEEDBACK_READ,
            )
            cursor_filters = _knowledge_feedback_cursor_filters(query)
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="knowledge_feedback",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters = self._knowledge_feedback_scope(scope.tenant_id)
            if query.status is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.status == query.status.value
                )
            if query.signal is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.signal == query.signal.value
                )
            if query.postmortem_id is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.postmortem_id
                    == query.postmortem_id
                )
            if after is not None:
                updated_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernanceKnowledgeFeedbackModel.updated_at < updated_at,
                        and_(
                            ReflectionCapacityGovernanceKnowledgeFeedbackModel.updated_at
                            == updated_at,
                            ReflectionCapacityGovernanceKnowledgeFeedbackModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceKnowledgeFeedbackModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityGovernanceKnowledgeFeedbackModel.updated_at.desc(),
                        ReflectionCapacityGovernanceKnowledgeFeedbackModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        return CapacityGovernanceKnowledgeFeedbackPage(
            items=tuple(_knowledge_feedback_record(row) for row in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="knowledge_feedback",
                    updated_at=page_rows[-1].updated_at,
                    item_id=page_rows[-1].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def report_knowledge_feedback(
        self,
        *,
        postmortem_id: UUID,
        expected_postmortem_version: int,
        expected_knowledge_version: str,
        expected_content_fingerprint: str,
        content: CapacityGovernanceKnowledgeFeedbackInput,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackRecord:
        normalized_knowledge_version = expected_knowledge_version.strip()
        normalized_fingerprint = expected_content_fingerprint.strip().lower()
        if expected_postmortem_version < 1:
            raise ValueError("expected_postmortem_version must be positive")
        if not normalized_knowledge_version or len(normalized_knowledge_version) > 100:
            raise ValueError("expected_knowledge_version must contain 1 to 100 characters")
        if len(normalized_fingerprint) != 64:
            raise ValueError("expected_content_fingerprint must contain 64 characters")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_KNOWLEDGE_FEEDBACK_REPORT,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                postmortem = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if postmortem is None:
                    raise KeyError("Unknown capacity governance postmortem")
                if (
                    postmortem.status != CapacityGovernancePostmortemStatus.PUBLISHED.value
                    or postmortem.version != expected_postmortem_version
                    or postmortem.knowledge_version != normalized_knowledge_version
                    or postmortem.content_fingerprint != normalized_fingerprint
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Knowledge feedback requires the current published knowledge version"
                    )
                existing = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeFeedbackModel.id).where(
                        ReflectionCapacityGovernanceKnowledgeFeedbackModel.postmortem_id
                        == postmortem.id,
                        ReflectionCapacityGovernanceKnowledgeFeedbackModel.reported_principal_id
                        == authorized.principal_id,
                        ReflectionCapacityGovernanceKnowledgeFeedbackModel.postmortem_version
                        == postmortem.version,
                    )
                )
                if existing is not None:
                    raise ReflectionCapacityGovernanceConflictError(
                        "The reporter already submitted feedback for this knowledge version"
                    )
                now = utc_now()
                row = ReflectionCapacityGovernanceKnowledgeFeedbackModel(
                    id=uuid4(),
                    tenant_id=authorized.tenant_id,
                    postmortem_id=postmortem.id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=self.handler_version,
                    postmortem_version=postmortem.version,
                    knowledge_version=normalized_knowledge_version,
                    content_fingerprint=normalized_fingerprint,
                    signal=content.signal.value,
                    reason=content.reason.value,
                    status=CapacityGovernanceKnowledgeFeedbackStatus.AWAITING_REVIEW.value,
                    version=1,
                    reported_by=authorized.subject,
                    reported_principal_id=authorized.principal_id,
                    reported_token_id=authorized.token_id,
                    reported_at=now,
                )
                session.add(row)
                await session.flush()
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.knowledge_feedback.report",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=postmortem.incident_id,
                    postmortem_id=postmortem.id,
                    metadata={
                        "feedback_reason": row.reason,
                        "feedback_signal": row.signal,
                        "feedback_status": row.status,
                    },
                )
            return _knowledge_feedback_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.knowledge_feedback.report",
                exc=exc,
                postmortem_id=postmortem_id,
            )
            raise

    async def confirm_knowledge_feedback(
        self,
        *,
        feedback_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackRecord:
        return await self._review_knowledge_feedback(
            feedback_id=feedback_id,
            expected_version=expected_version,
            confirm=True,
            actor=actor,
        )

    async def dismiss_knowledge_feedback(
        self,
        *,
        feedback_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackRecord:
        return await self._review_knowledge_feedback(
            feedback_id=feedback_id,
            expected_version=expected_version,
            confirm=False,
            actor=actor,
        )

    async def _review_knowledge_feedback(
        self,
        *,
        feedback_id: UUID,
        expected_version: int,
        confirm: bool,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeFeedbackRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        decision = "confirm" if confirm else "dismiss"
        action = f"capacity.knowledge_feedback.{decision}"
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                row = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeFeedbackModel)
                    .where(
                        ReflectionCapacityGovernanceKnowledgeFeedbackModel.id == feedback_id,
                        *self._knowledge_feedback_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance knowledge feedback")
                if (
                    row.version != expected_version
                    or row.status != CapacityGovernanceKnowledgeFeedbackStatus.AWAITING_REVIEW.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Knowledge feedback changed before review"
                    )
                if row.reported_principal_id == authorized.principal_id:
                    raise CapacityGovernanceAuthorizationError(
                        "Knowledge feedback reporter cannot review the same feedback"
                    )
                postmortem = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == row.postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if (
                    postmortem is None
                    or postmortem.status != CapacityGovernancePostmortemStatus.PUBLISHED.value
                    or postmortem.version != row.postmortem_version
                    or postmortem.knowledge_version != row.knowledge_version
                    or postmortem.content_fingerprint != row.content_fingerprint
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Knowledge source changed before feedback review"
                    )
                now = utc_now()
                row.status = (
                    CapacityGovernanceKnowledgeFeedbackStatus.CONFIRMED.value
                    if confirm
                    else CapacityGovernanceKnowledgeFeedbackStatus.DISMISSED.value
                )
                row.reviewed_by = authorized.subject
                row.reviewed_principal_id = authorized.principal_id
                row.reviewed_token_id = authorized.token_id
                row.reviewed_at = now
                row.version += 1
                row.updated_at = now
                if (
                    confirm
                    and row.signal == CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN.value
                ):
                    postmortem.status = CapacityGovernancePostmortemStatus.QUARANTINED.value
                    postmortem.last_quarantined_at = now
                    postmortem.quarantine_feedback_id = row.id
                    postmortem.version += 1
                    postmortem.updated_at = now
                    superseded_ids = tuple(
                        await session.scalars(
                            update(ReflectionCapacityGovernanceKnowledgeFeedbackModel)
                            .where(
                                ReflectionCapacityGovernanceKnowledgeFeedbackModel.id != row.id,
                                ReflectionCapacityGovernanceKnowledgeFeedbackModel.postmortem_id
                                == row.postmortem_id,
                                ReflectionCapacityGovernanceKnowledgeFeedbackModel.postmortem_version
                                == row.postmortem_version,
                                ReflectionCapacityGovernanceKnowledgeFeedbackModel.status
                                == CapacityGovernanceKnowledgeFeedbackStatus.AWAITING_REVIEW.value,
                                *self._knowledge_feedback_scope(authorized.tenant_id),
                            )
                            .values(
                                status=(CapacityGovernanceKnowledgeFeedbackStatus.SUPERSEDED.value),
                                version=(
                                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.version + 1
                                ),
                                updated_at=now,
                            )
                            .returning(ReflectionCapacityGovernanceKnowledgeFeedbackModel.id)
                        )
                    )
                    superseded_feedback = len(superseded_ids)
                else:
                    superseded_feedback = 0
                self._append_audit(
                    session,
                    authorized=authorized,
                    action=action,
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=postmortem.incident_id,
                    postmortem_id=postmortem.id,
                    metadata={
                        "feedback_reason": row.reason,
                        "feedback_signal": row.signal,
                        "feedback_status": row.status,
                        "postmortem_status": postmortem.status,
                        "superseded_feedback": superseded_feedback,
                    },
                )
            return _knowledge_feedback_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action=action,
                exc=exc,
            )
            raise

    async def list_knowledge_quality_snapshots(
        self,
        query: CapacityGovernanceKnowledgeQualitySnapshotQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeQualitySnapshotPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_KNOWLEDGE_QUALITY_READ,
            )
            cursor_filters = _knowledge_quality_cursor_filters(query)
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="knowledge_quality",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters = self._knowledge_quality_scope(scope.tenant_id)
            if query.assessment is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.assessment
                    == query.assessment.value
                )
            if query.postmortem_id is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.postmortem_id
                    == query.postmortem_id
                )
            if after is not None:
                captured_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at
                        < captured_at,
                        and_(
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at
                            == captured_at,
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at.desc(),
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        return CapacityGovernanceKnowledgeQualitySnapshotPage(
            items=tuple(_knowledge_quality_snapshot_record(row) for row in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="knowledge_quality",
                    updated_at=page_rows[-1].captured_at,
                    item_id=page_rows[-1].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def knowledge_quality_trend(
        self,
        query: CapacityGovernanceKnowledgeQualityTrendQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeQualityTrendReport:
        if query.limit > self.knowledge_quality_maximum_trend_buckets:
            raise ValueError("knowledge quality trend limit exceeds configured maximum")
        bucket_starts = _knowledge_quality_trend_bucket_starts(query)
        if len(bucket_starts) > self.knowledge_quality_maximum_trend_buckets:
            raise ValueError("knowledge quality trend window exceeds configured maximum")
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_KNOWLEDGE_QUALITY_READ,
            )
            cursor_filters = _knowledge_quality_trend_cursor_filters(query)
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="knowledge_quality_trend",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            if after is not None:
                after_bucket, _ = after
                bucket_starts = tuple(bucket for bucket in bucket_starts if bucket < after_bucket)
            page_bucket_starts = bucket_starts[: query.limit]
            bucket_expr = func.date_trunc(
                query.bucket.value,
                ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at,
            ).label("bucket_started_at")
            filters = self._knowledge_quality_scope(scope.tenant_id)
            filters.extend(
                (
                    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at
                    >= query.captured_from,
                    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at
                    < query.captured_to,
                )
            )
            if query.assessment is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.assessment
                    == query.assessment.value
                )
            rows = (
                await session.execute(
                    select(
                        bucket_expr,
                        func.count(
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id
                        ).label("total_snapshots"),
                        func.count(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id)
                        .filter(
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.assessment
                            == CapacityGovernanceKnowledgeQualityAssessment.INSUFFICIENT.value
                        )
                        .label("insufficient_count"),
                        func.count(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id)
                        .filter(
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.assessment
                            == CapacityGovernanceKnowledgeQualityAssessment.HEALTHY.value
                        )
                        .label("healthy_count"),
                        func.count(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id)
                        .filter(
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.assessment
                            == CapacityGovernanceKnowledgeQualityAssessment.DEGRADED.value
                        )
                        .label("degraded_count"),
                        func.count(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id)
                        .filter(
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.assessment
                            == CapacityGovernanceKnowledgeQualityAssessment.UNSAFE.value
                        )
                        .label("unsafe_count"),
                        func.count(
                            func.distinct(
                                ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.postmortem_id
                            )
                        ).label("distinct_postmortems"),
                    )
                    .where(*filters)
                    .group_by(bucket_expr)
                )
            ).all()
        aggregated = {row.bucket_started_at: row for row in rows}
        points = tuple(
            CapacityGovernanceKnowledgeQualityTrendPoint(
                bucket_started_at=bucket_start,
                total_snapshots=(
                    int(aggregated[bucket_start].total_snapshots)
                    if bucket_start in aggregated
                    else 0
                ),
                insufficient_count=(
                    int(aggregated[bucket_start].insufficient_count)
                    if bucket_start in aggregated
                    else 0
                ),
                healthy_count=(
                    int(aggregated[bucket_start].healthy_count) if bucket_start in aggregated else 0
                ),
                degraded_count=(
                    int(aggregated[bucket_start].degraded_count)
                    if bucket_start in aggregated
                    else 0
                ),
                unsafe_count=(
                    int(aggregated[bucket_start].unsafe_count) if bucket_start in aggregated else 0
                ),
                distinct_postmortems=(
                    int(aggregated[bucket_start].distinct_postmortems)
                    if bucket_start in aggregated
                    else 0
                ),
            )
            for bucket_start in page_bucket_starts
        )
        return CapacityGovernanceKnowledgeQualityTrendReport(
            handler_version=self.handler_version,
            bucket=query.bucket,
            captured_from=query.captured_from,
            captured_to=query.captured_to,
            assessment=query.assessment,
            points=points,
            next_cursor=(
                _encode_cursor(
                    kind="knowledge_quality_trend",
                    updated_at=points[-1].bucket_started_at,
                    item_id=UUID(int=0),
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(bucket_starts) > query.limit and points
                else None
            ),
            generated_at=utc_now(),
        )

    async def capture_knowledge_quality_snapshot(
        self,
        *,
        postmortem_id: UUID,
        expected_postmortem_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeQualitySnapshotRecord:
        if expected_postmortem_version < 1:
            raise ValueError("expected_postmortem_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_KNOWLEDGE_QUALITY_ASSESS,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                postmortem = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if postmortem is None:
                    raise KeyError("Unknown capacity governance postmortem")
                if (
                    postmortem.version != expected_postmortem_version
                    or postmortem.status
                    not in {
                        CapacityGovernancePostmortemStatus.PUBLISHED.value,
                        CapacityGovernancePostmortemStatus.QUARANTINED.value,
                    }
                    or postmortem.knowledge_version is None
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Knowledge quality requires the current published or quarantined version"
                    )
                evidence = await self._knowledge_quality_evidence(
                    session,
                    postmortem=postmortem,
                )
                existing = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel).where(
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.postmortem_id
                        == postmortem.id,
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.postmortem_version
                        == postmortem.version,
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.evidence_fingerprint
                        == evidence["fingerprint"],
                        *self._knowledge_quality_scope(authorized.tenant_id),
                    )
                )
                if existing is not None:
                    return _knowledge_quality_snapshot_record(existing)
                now = utc_now()
                row = ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel(
                    id=uuid4(),
                    tenant_id=authorized.tenant_id,
                    postmortem_id=postmortem.id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=self.handler_version,
                    postmortem_version=postmortem.version,
                    knowledge_version=postmortem.knowledge_version,
                    content_fingerprint=postmortem.content_fingerprint,
                    evidence_fingerprint=evidence["fingerprint"],
                    assessment=evidence["assessment"],
                    total_feedback=evidence["total_feedback"],
                    awaiting_review_count=evidence["awaiting_review_count"],
                    confirmed_helpful_count=evidence["confirmed_helpful_count"],
                    confirmed_not_helpful_count=evidence["confirmed_not_helpful_count"],
                    confirmed_safety_count=evidence["confirmed_safety_count"],
                    dismissed_count=evidence["dismissed_count"],
                    superseded_count=evidence["superseded_count"],
                    captured_by=authorized.subject,
                    captured_principal_id=authorized.principal_id,
                    captured_token_id=authorized.token_id,
                    captured_at=now,
                    created_at=now,
                )
                session.add(row)
                await session.flush()
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.knowledge_quality.capture",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=postmortem.incident_id,
                    postmortem_id=postmortem.id,
                    metadata={
                        "knowledge_quality_assessment": row.assessment,
                        "quality_total_feedback": row.total_feedback,
                    },
                )
            return _knowledge_quality_snapshot_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.knowledge_quality.capture",
                exc=exc,
                postmortem_id=postmortem_id,
            )
            raise

    async def list_knowledge_recertifications(
        self,
        query: CapacityGovernanceKnowledgeRecertificationQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecertificationPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session, actor=actor, permission=CAPACITY_KNOWLEDGE_RECERTIFICATION_READ
            )
            filters = self._knowledge_recertification_scope(scope.tenant_id)
            if query.status is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeRecertificationModel.status
                    == query.status.value
                )
            if query.postmortem_id is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeRecertificationModel.postmortem_id
                    == query.postmortem_id
                )
            after = None
            if query.cursor:
                after = _decode_cursor(
                    query.cursor,
                    kind="knowledge_recertification",
                    filters={
                        "status": query.status.value if query.status else None,
                        "postmortem_id": str(query.postmortem_id) if query.postmortem_id else None,
                    },
                    scope_hash=self._scope_hash(scope),
                )
            if after:
                updated_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernanceKnowledgeRecertificationModel.updated_at
                        < updated_at,
                        and_(
                            ReflectionCapacityGovernanceKnowledgeRecertificationModel.updated_at
                            == updated_at,
                            ReflectionCapacityGovernanceKnowledgeRecertificationModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceKnowledgeRecertificationModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityGovernanceKnowledgeRecertificationModel.updated_at.desc(),
                        ReflectionCapacityGovernanceKnowledgeRecertificationModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        cursor_filters = {
            "status": query.status.value if query.status else None,
            "postmortem_id": str(query.postmortem_id) if query.postmortem_id else None,
        }
        return CapacityGovernanceKnowledgeRecertificationPage(
            items=tuple(_knowledge_recertification_record(row) for row in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="knowledge_recertification",
                    updated_at=page_rows[-1].updated_at,
                    item_id=page_rows[-1].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def request_knowledge_recertification(
        self,
        *,
        content: CapacityGovernanceKnowledgeRecertificationInput,
        actor: AuthenticatedPrincipal,
        idempotency_key: str | None = None,
    ) -> CapacityGovernanceKnowledgeRecertificationRecord:
        key = (
            idempotency_key
            or hashlib.sha256(
                f"{content.postmortem_id}:{content.expected_postmortem_version}:"
                f"{content.quality_snapshot_id}:{content.quality_evidence_fingerprint}:"
                f"{content.decision.value}:{content.reason.value}".encode()
            ).hexdigest()
        ).strip()
        if not 1 <= len(key) <= 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_KNOWLEDGE_RECERTIFICATION_REQUEST,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                existing = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeRecertificationModel)
                    .where(
                        ReflectionCapacityGovernanceKnowledgeRecertificationModel.tenant_id
                        == authorized.tenant_id,
                        ReflectionCapacityGovernanceKnowledgeRecertificationModel.idempotency_key
                        == key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    if (
                        existing.postmortem_id != content.postmortem_id
                        or existing.postmortem_version != content.expected_postmortem_version
                        or existing.decision != content.decision.value
                        or existing.reason != content.reason.value
                    ):
                        raise ReflectionCapacityGovernanceConflictError(
                            "recertification idempotency key reused"
                        )
                    return _knowledge_recertification_record(existing)
                postmortem = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == content.postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if postmortem is None:
                    raise KeyError("Unknown capacity governance postmortem")
                if (
                    postmortem.status != CapacityGovernancePostmortemStatus.PUBLISHED.value
                    or postmortem.version != content.expected_postmortem_version
                    or postmortem.knowledge_version != content.knowledge_version
                    or postmortem.content_fingerprint != content.content_fingerprint
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "recertification source is stale"
                    )
                snapshot = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel).where(
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id
                        == content.quality_snapshot_id,
                        *self._knowledge_quality_scope(authorized.tenant_id),
                    )
                )
                evidence = await self._knowledge_quality_evidence(session, postmortem=postmortem)
                if (
                    snapshot is None
                    or snapshot.postmortem_id != postmortem.id
                    or snapshot.postmortem_version != postmortem.version
                    or snapshot.knowledge_version != postmortem.knowledge_version
                    or snapshot.content_fingerprint != postmortem.content_fingerprint
                    or snapshot.evidence_fingerprint != content.quality_evidence_fingerprint
                    or evidence["fingerprint"] != content.quality_evidence_fingerprint
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "recertification quality evidence is stale"
                    )
                now = utc_now()
                row = ReflectionCapacityGovernanceKnowledgeRecertificationModel(
                    id=uuid4(),
                    tenant_id=authorized.tenant_id,
                    postmortem_id=postmortem.id,
                    quality_snapshot_id=snapshot.id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=self.handler_version,
                    postmortem_version=postmortem.version,
                    knowledge_version=postmortem.knowledge_version,
                    content_fingerprint=postmortem.content_fingerprint,
                    quality_evidence_fingerprint=snapshot.evidence_fingerprint,
                    decision=content.decision.value,
                    reason=content.reason.value,
                    status=CapacityGovernanceKnowledgeRecertificationStatus.AWAITING_REVIEW.value,
                    version=1,
                    idempotency_key=key,
                    requested_by=authorized.subject,
                    requested_principal_id=authorized.principal_id,
                    requested_token_id=authorized.token_id,
                    requested_at=now,
                )
                session.add(row)
                await session.flush()
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.knowledge_recertification.request",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=postmortem.incident_id,
                    postmortem_id=postmortem.id,
                    metadata={"knowledge_recertification_status": row.status},
                )
            return _knowledge_recertification_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.knowledge_recertification.request",
                exc=exc,
                postmortem_id=content.postmortem_id,
            )
            raise

    async def review_knowledge_recertification(
        self,
        *,
        recertification_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecertificationRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                row = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeRecertificationModel)
                    .where(
                        ReflectionCapacityGovernanceKnowledgeRecertificationModel.id
                        == recertification_id,
                        *self._knowledge_recertification_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown governance knowledge recertification")
                await self._authorize(
                    session,
                    actor=actor,
                    permission=(
                        CAPACITY_KNOWLEDGE_RETIREMENT
                        if row.decision
                        == CapacityGovernanceKnowledgeRecertificationDecision.RETIRE.value
                        else CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW
                    ),
                )
                if (
                    row.version != expected_version
                    or row.status
                    != CapacityGovernanceKnowledgeRecertificationStatus.AWAITING_REVIEW.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "recertification changed before review"
                    )
                if row.requested_principal_id == authorized.principal_id:
                    raise CapacityGovernanceAuthorizationError(
                        "recertification requester cannot review the same request"
                    )
                postmortem = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == row.postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if (
                    postmortem is None
                    or postmortem.version != row.postmortem_version
                    or postmortem.content_fingerprint != row.content_fingerprint
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "recertification source changed before review"
                    )
                snapshot = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel).where(
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id
                        == row.quality_snapshot_id,
                        *self._knowledge_quality_scope(authorized.tenant_id),
                    )
                )
                evidence = await self._knowledge_quality_evidence(session, postmortem=postmortem)
                if (
                    snapshot is None
                    or snapshot.evidence_fingerprint != row.quality_evidence_fingerprint
                    or evidence["fingerprint"] != row.quality_evidence_fingerprint
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "recertification evidence changed before review"
                    )
                if row.decision == CapacityGovernanceKnowledgeRecertificationDecision.CERTIFY.value:
                    if (
                        postmortem.status != CapacityGovernancePostmortemStatus.PUBLISHED.value
                        or snapshot.assessment
                        != CapacityGovernanceKnowledgeQualityAssessment.HEALTHY.value
                    ):
                        raise ReflectionCapacityGovernanceConflictError(
                            "certification requires current healthy quality evidence"
                        )
                    postmortem.last_certified_at = utc_now()
                    postmortem.version += 1
                    postmortem.updated_at = postmortem.last_certified_at
                    row.status = CapacityGovernanceKnowledgeRecertificationStatus.CERTIFIED.value
                elif (
                    row.decision == CapacityGovernanceKnowledgeRecertificationDecision.RETIRE.value
                ):
                    await self._authorize(
                        session, actor=actor, permission=CAPACITY_KNOWLEDGE_RETIREMENT
                    )
                    if postmortem.status != CapacityGovernancePostmortemStatus.PUBLISHED.value:
                        raise ReflectionCapacityGovernanceConflictError(
                            "only published knowledge can be retired"
                        )
                    now = utc_now()
                    postmortem.status = CapacityGovernancePostmortemStatus.RETIRED.value
                    postmortem.retired_at = now
                    postmortem.retired_by = authorized.subject
                    postmortem.retired_principal_id = authorized.principal_id
                    postmortem.retired_token_id = authorized.token_id
                    postmortem.version += 1
                    postmortem.updated_at = now
                    row.status = CapacityGovernanceKnowledgeRecertificationStatus.RETIRED.value
                else:
                    row.status = CapacityGovernanceKnowledgeRecertificationStatus.REJECTED.value
                now = utc_now()
                row.reviewed_by = authorized.subject
                row.reviewed_principal_id = authorized.principal_id
                row.reviewed_token_id = authorized.token_id
                row.reviewed_at = now
                row.version += 1
                row.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.knowledge_recertification.review",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=postmortem.incident_id,
                    postmortem_id=postmortem.id,
                    metadata={
                        "knowledge_recertification_status": row.status,
                        "postmortem_status": postmortem.status,
                    },
                )
            return _knowledge_recertification_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor, action="capacity.knowledge_recertification.review", exc=exc
            )
            raise

    async def retire_knowledge(
        self, *, recertification_id: UUID, expected_version: int, actor: AuthenticatedPrincipal
    ) -> CapacityGovernanceKnowledgeRecertificationRecord:
        return await self.review_knowledge_recertification(
            recertification_id=recertification_id, expected_version=expected_version, actor=actor
        )

    async def approve_knowledge_recertification(
        self,
        *,
        recertification_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecertificationRecord:
        return await self.review_knowledge_recertification(
            recertification_id=recertification_id,
            expected_version=expected_version,
            actor=actor,
        )

    async def reject_knowledge_recertification(
        self,
        *,
        recertification_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecertificationRecord:
        return await self.review_knowledge_recertification(
            recertification_id=recertification_id,
            expected_version=expected_version,
            actor=actor,
        )

    async def list_knowledge_recoveries(
        self,
        query: CapacityGovernanceKnowledgeRecoveryQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryPage:
        async with self._sessions() as session, session.begin():
            scope = await self._authorize(
                session,
                actor=actor,
                permission=CAPACITY_KNOWLEDGE_RECOVERY_READ,
            )
            cursor_filters = _knowledge_recovery_cursor_filters(query)
            after = (
                _decode_cursor(
                    query.cursor,
                    kind="knowledge_recovery",
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if query.cursor
                else None
            )
            filters = self._knowledge_recovery_scope(scope.tenant_id)
            if query.status is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeRecoveryModel.status == query.status.value
                )
            if query.postmortem_id is not None:
                filters.append(
                    ReflectionCapacityGovernanceKnowledgeRecoveryModel.postmortem_id
                    == query.postmortem_id
                )
            if after is not None:
                updated_at, item_id = after
                filters.append(
                    or_(
                        ReflectionCapacityGovernanceKnowledgeRecoveryModel.updated_at < updated_at,
                        and_(
                            ReflectionCapacityGovernanceKnowledgeRecoveryModel.updated_at
                            == updated_at,
                            ReflectionCapacityGovernanceKnowledgeRecoveryModel.id < item_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceKnowledgeRecoveryModel)
                    .where(*filters)
                    .order_by(
                        ReflectionCapacityGovernanceKnowledgeRecoveryModel.updated_at.desc(),
                        ReflectionCapacityGovernanceKnowledgeRecoveryModel.id.desc(),
                    )
                    .limit(query.limit + 1)
                )
            )
        page_rows = rows[: query.limit]
        return CapacityGovernanceKnowledgeRecoveryPage(
            items=tuple(_knowledge_recovery_record(row) for row in page_rows),
            next_cursor=(
                _encode_cursor(
                    kind="knowledge_recovery",
                    updated_at=page_rows[-1].updated_at,
                    item_id=page_rows[-1].id,
                    filters=cursor_filters,
                    scope_hash=self._scope_hash(scope),
                )
                if len(rows) > query.limit and page_rows
                else None
            ),
        )

    async def request_knowledge_recovery(
        self,
        *,
        postmortem_id: UUID,
        expected_postmortem_version: int,
        snapshot_id: UUID,
        reason: CapacityGovernanceKnowledgeRecoveryReason,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryRecord:
        if expected_postmortem_version < 1:
            raise ValueError("expected_postmortem_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_KNOWLEDGE_RECOVERY_REQUEST,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                postmortem = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if postmortem is None:
                    raise KeyError("Unknown capacity governance postmortem")
                snapshot, feedback = await self._validate_knowledge_recovery_source(
                    session,
                    postmortem=postmortem,
                    expected_postmortem_version=expected_postmortem_version,
                    snapshot_id=snapshot_id,
                    tenant_id=authorized.tenant_id,
                )
                if authorized.principal_id in {
                    feedback.reported_principal_id,
                    feedback.reviewed_principal_id,
                }:
                    raise CapacityGovernanceAuthorizationError(
                        "Knowledge recovery requester must be independent from safety feedback"
                    )
                existing = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeRecoveryModel.id).where(
                        ReflectionCapacityGovernanceKnowledgeRecoveryModel.postmortem_id
                        == postmortem.id,
                        ReflectionCapacityGovernanceKnowledgeRecoveryModel.postmortem_version
                        == postmortem.version,
                        ReflectionCapacityGovernanceKnowledgeRecoveryModel.status
                        == CapacityGovernanceKnowledgeRecoveryStatus.AWAITING_REVIEW.value,
                        *self._knowledge_recovery_scope(authorized.tenant_id),
                    )
                )
                if existing is not None:
                    raise ReflectionCapacityGovernanceConflictError(
                        "A recovery request already awaits review for this quarantine"
                    )
                now = utc_now()
                row = ReflectionCapacityGovernanceKnowledgeRecoveryModel(
                    id=uuid4(),
                    tenant_id=authorized.tenant_id,
                    postmortem_id=postmortem.id,
                    snapshot_id=snapshot.id,
                    quarantine_feedback_id=feedback.id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=self.handler_version,
                    postmortem_version=postmortem.version,
                    knowledge_version=postmortem.knowledge_version,
                    content_fingerprint=postmortem.content_fingerprint,
                    reason=reason.value,
                    status=CapacityGovernanceKnowledgeRecoveryStatus.AWAITING_REVIEW.value,
                    version=1,
                    requested_by=authorized.subject,
                    requested_principal_id=authorized.principal_id,
                    requested_token_id=authorized.token_id,
                    requested_at=now,
                )
                session.add(row)
                await session.flush()
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.knowledge_recovery.request",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=postmortem.incident_id,
                    postmortem_id=postmortem.id,
                    metadata={
                        "knowledge_recovery_reason": row.reason,
                        "knowledge_recovery_status": row.status,
                    },
                )
            return _knowledge_recovery_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.knowledge_recovery.request",
                exc=exc,
                postmortem_id=postmortem_id,
            )
            raise

    async def approve_knowledge_recovery(
        self,
        *,
        recovery_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryRecord:
        return await self._review_knowledge_recovery(
            recovery_id=recovery_id,
            expected_version=expected_version,
            approve=True,
            actor=actor,
        )

    async def reject_knowledge_recovery(
        self,
        *,
        recovery_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryRecord:
        return await self._review_knowledge_recovery(
            recovery_id=recovery_id,
            expected_version=expected_version,
            approve=False,
            actor=actor,
        )

    async def _review_knowledge_recovery(
        self,
        *,
        recovery_id: UUID,
        expected_version: int,
        approve: bool,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceKnowledgeRecoveryRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        decision = "approve" if approve else "reject"
        action = f"capacity.knowledge_recovery.{decision}"
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
                    for_update=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                row = await session.scalar(
                    select(ReflectionCapacityGovernanceKnowledgeRecoveryModel)
                    .where(
                        ReflectionCapacityGovernanceKnowledgeRecoveryModel.id == recovery_id,
                        *self._knowledge_recovery_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance knowledge recovery")
                if (
                    row.version != expected_version
                    or row.status != CapacityGovernanceKnowledgeRecoveryStatus.AWAITING_REVIEW.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Knowledge recovery changed before review"
                    )
                if row.requested_principal_id == authorized.principal_id:
                    raise CapacityGovernanceAuthorizationError(
                        "Knowledge recovery requester cannot review the same request"
                    )
                postmortem = await session.scalar(
                    select(ReflectionCapacityGovernancePostmortemModel)
                    .where(
                        ReflectionCapacityGovernancePostmortemModel.id == row.postmortem_id,
                        *self._postmortem_scope(authorized.tenant_id),
                    )
                    .with_for_update()
                )
                if postmortem is None:
                    raise KeyError("Unknown capacity governance postmortem")
                snapshot, feedback = await self._validate_knowledge_recovery_source(
                    session,
                    postmortem=postmortem,
                    expected_postmortem_version=row.postmortem_version,
                    snapshot_id=row.snapshot_id,
                    tenant_id=authorized.tenant_id,
                )
                if feedback.id != row.quarantine_feedback_id:
                    raise ReflectionCapacityGovernanceConflictError(
                        "Knowledge recovery quarantine evidence changed"
                    )
                if authorized.principal_id == feedback.reviewed_principal_id:
                    raise CapacityGovernanceAuthorizationError(
                        "Knowledge recovery reviewer cannot be the safety feedback reviewer"
                    )
                if authorized.principal_id == feedback.reported_principal_id:
                    raise CapacityGovernanceAuthorizationError(
                        "Knowledge recovery reviewer cannot be the safety feedback reporter"
                    )
                now = utc_now()
                row.status = (
                    CapacityGovernanceKnowledgeRecoveryStatus.APPROVED.value
                    if approve
                    else CapacityGovernanceKnowledgeRecoveryStatus.REJECTED.value
                )
                row.reviewed_by = authorized.subject
                row.reviewed_principal_id = authorized.principal_id
                row.reviewed_token_id = authorized.token_id
                row.reviewed_at = now
                row.version += 1
                row.updated_at = now
                if approve:
                    restored_version = _restored_knowledge_version(postmortem)
                    row.restored_knowledge_version = restored_version
                    postmortem.status = CapacityGovernancePostmortemStatus.PUBLISHED.value
                    postmortem.knowledge_version = restored_version
                    postmortem.restore_count += 1
                    postmortem.last_restored_at = now
                    postmortem.version += 1
                    postmortem.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action=action,
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    incident_id=postmortem.incident_id,
                    postmortem_id=postmortem.id,
                    metadata={
                        "knowledge_quality_assessment": snapshot.assessment,
                        "knowledge_recovery_reason": row.reason,
                        "knowledge_recovery_status": row.status,
                        "postmortem_status": postmortem.status,
                    },
                )
            return _knowledge_recovery_record(row)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action=action,
                exc=exc,
            )
            raise

    async def _validate_knowledge_recovery_source(
        self,
        session: AsyncSession,
        *,
        postmortem: ReflectionCapacityGovernancePostmortemModel,
        expected_postmortem_version: int,
        snapshot_id: UUID,
        tenant_id: UUID,
    ) -> tuple[
        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel,
        ReflectionCapacityGovernanceKnowledgeFeedbackModel,
    ]:
        if (
            postmortem.status != CapacityGovernancePostmortemStatus.QUARANTINED.value
            or postmortem.version != expected_postmortem_version
            or postmortem.knowledge_version is None
            or postmortem.last_quarantined_at is None
            or postmortem.quarantine_feedback_id is None
        ):
            raise ReflectionCapacityGovernanceConflictError(
                "Knowledge recovery requires the current quarantined version"
            )
        if utc_now() - postmortem.last_quarantined_at < GOVERNANCE_KNOWLEDGE_QUARANTINE_RETENTION:
            raise ReflectionCapacityGovernanceConflictError(
                "Knowledge quarantine retention period has not elapsed"
            )
        snapshot = await session.scalar(
            select(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel).where(
                ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id == snapshot_id,
                *self._knowledge_quality_scope(tenant_id),
            )
        )
        feedback = await session.scalar(
            select(ReflectionCapacityGovernanceKnowledgeFeedbackModel).where(
                ReflectionCapacityGovernanceKnowledgeFeedbackModel.id
                == postmortem.quarantine_feedback_id,
                *self._knowledge_feedback_scope(tenant_id),
            )
        )
        if (
            snapshot is None
            or snapshot.postmortem_id != postmortem.id
            or snapshot.postmortem_version != postmortem.version
            or snapshot.knowledge_version != postmortem.knowledge_version
            or snapshot.content_fingerprint != postmortem.content_fingerprint
            or snapshot.assessment != CapacityGovernanceKnowledgeQualityAssessment.UNSAFE.value
            or feedback is None
            or feedback.postmortem_id != postmortem.id
            or feedback.knowledge_version != postmortem.knowledge_version
            or feedback.content_fingerprint != postmortem.content_fingerprint
            or feedback.status != CapacityGovernanceKnowledgeFeedbackStatus.CONFIRMED.value
            or feedback.signal != CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN.value
        ):
            raise ReflectionCapacityGovernanceConflictError(
                "Knowledge recovery source evidence is stale or incomplete"
            )
        evidence = await self._knowledge_quality_evidence(
            session,
            postmortem=postmortem,
        )
        if evidence["fingerprint"] != snapshot.evidence_fingerprint:
            raise ReflectionCapacityGovernanceConflictError(
                "Knowledge quality changed after the recovery snapshot"
            )
        return snapshot, feedback

    async def _knowledge_quality_evidence(
        self,
        session: AsyncSession,
        *,
        postmortem: ReflectionCapacityGovernancePostmortemModel,
    ) -> dict[str, str | int]:
        if postmortem.knowledge_version is None:
            raise ReflectionCapacityGovernanceConflictError(
                "Knowledge quality requires a published knowledge version"
            )
        rows = tuple(
            await session.scalars(
                select(ReflectionCapacityGovernanceKnowledgeFeedbackModel)
                .where(
                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.postmortem_id
                    == postmortem.id,
                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.knowledge_version
                    == postmortem.knowledge_version,
                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.content_fingerprint
                    == postmortem.content_fingerprint,
                    *self._knowledge_feedback_scope(postmortem.tenant_id),
                )
                .order_by(ReflectionCapacityGovernanceKnowledgeFeedbackModel.id)
            )
        )
        awaiting = sum(
            row.status == CapacityGovernanceKnowledgeFeedbackStatus.AWAITING_REVIEW.value
            for row in rows
        )
        helpful = sum(
            row.status == CapacityGovernanceKnowledgeFeedbackStatus.CONFIRMED.value
            and row.signal == CapacityGovernanceKnowledgeFeedbackSignal.HELPFUL.value
            for row in rows
        )
        not_helpful = sum(
            row.status == CapacityGovernanceKnowledgeFeedbackStatus.CONFIRMED.value
            and row.signal == CapacityGovernanceKnowledgeFeedbackSignal.NOT_HELPFUL.value
            for row in rows
        )
        safety = sum(
            row.status == CapacityGovernanceKnowledgeFeedbackStatus.CONFIRMED.value
            and row.signal == CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN.value
            for row in rows
        )
        dismissed = sum(
            row.status == CapacityGovernanceKnowledgeFeedbackStatus.DISMISSED.value for row in rows
        )
        superseded = sum(
            row.status == CapacityGovernanceKnowledgeFeedbackStatus.SUPERSEDED.value for row in rows
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    {
                        "id": str(row.id),
                        "reason": row.reason,
                        "signal": row.signal,
                        "status": row.status,
                        "updated_at": row.updated_at.isoformat(),
                        "version": row.version,
                    }
                    for row in rows
                ],
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        assessment = governance_knowledge_quality_assessment(
            confirmed_helpful=helpful,
            confirmed_not_helpful=not_helpful,
            confirmed_safety=safety,
        )
        return {
            "fingerprint": fingerprint,
            "assessment": assessment.value,
            "total_feedback": len(rows),
            "awaiting_review_count": awaiting,
            "confirmed_helpful_count": helpful,
            "confirmed_not_helpful_count": not_helpful,
            "confirmed_safety_count": safety,
            "dismissed_count": dismissed,
            "superseded_count": superseded,
        }

    async def _validate_postmortem_review(
        self,
        session: AsyncSession,
        *,
        row: ReflectionCapacityGovernancePostmortemModel,
        authorized: AuthorizedGlobalActor,
        expected_version: int,
        for_update: bool,
    ) -> None:
        if (
            row.version != expected_version
            or row.status != CapacityGovernancePostmortemStatus.AWAITING_REVIEW.value
        ):
            raise ReflectionCapacityGovernanceConflictError("Postmortem changed before review")
        if row.requested_principal_id == authorized.principal_id:
            raise CapacityGovernanceAuthorizationError(
                "Postmortem requester cannot review the same postmortem"
            )
        remediation_statement = select(ReflectionCapacityGovernanceRemediationModel).where(
            ReflectionCapacityGovernanceRemediationModel.id == row.remediation_id,
            *self._remediation_scope(authorized.tenant_id),
        )
        incident_statement = select(ReflectionCapacityGovernanceIncidentModel).where(
            ReflectionCapacityGovernanceIncidentModel.id == row.incident_id,
            *self._incident_scope(authorized.tenant_id),
        )
        if for_update:
            remediation_statement = remediation_statement.with_for_update()
            incident_statement = incident_statement.with_for_update()
        remediation = await session.scalar(remediation_statement)
        incident = await session.scalar(incident_statement)
        if (
            remediation is None
            or incident is None
            or remediation.status != CapacityGovernanceRemediationStatus.VERIFIED.value
            or remediation.version != row.remediation_version
            or remediation.incident_id != row.incident_id
            or remediation.incident_cycle != row.incident_cycle
            or incident.version != row.incident_version
            or incident.reopened_count != row.incident_cycle
            or incident.status != CapacityGovernanceIncidentStatus.RESOLVED.value
        ):
            raise ReflectionCapacityGovernanceConflictError(
                "Postmortem source facts changed before review"
            )

    async def scan_incidents(
        self,
        *,
        actor: AuthenticatedPrincipal | None = None,
    ) -> CapacityIncidentScanReport:
        now = utc_now()
        thresholds = self.incident_thresholds
        try:
            async with self._sessions() as session, session.begin():
                authorized = None
                if actor is not None:
                    authorized = await self._authorize(
                        session,
                        actor=actor,
                        permission=CAPACITY_INCIDENTS_MANAGE,
                        for_update=True,
                    )
                    tenant_id = authorized.tenant_id
                else:
                    resolved_tenant_id = await session.scalar(
                        select(TenantModel.id).where(TenantModel.slug == self.governance_tenant)
                    )
                    if resolved_tenant_id is None:
                        raise CapacityGovernanceAuthorizationError(
                            "Unknown capacity governance tenant"
                        )
                    tenant_id = resolved_tenant_id
                await session.execute(select(func.pg_advisory_xact_lock(self._incident_lock_id())))
                bucket_seconds = thresholds.audit_window_seconds
                bucket_epoch = int(now.timestamp())
                bucket_start = datetime.fromtimestamp(
                    bucket_epoch - (bucket_epoch % bucket_seconds),
                    tz=now.tzinfo,
                )
                audit_outcomes = tuple(
                    await session.scalars(
                        select(ReflectionCapacityGovernanceAuditEventModel.outcome)
                        .where(
                            ReflectionCapacityGovernanceAuditEventModel.tenant_id == tenant_id,
                            ReflectionCapacityGovernanceAuditEventModel.handler_version
                            == self.handler_version,
                            ReflectionCapacityGovernanceAuditEventModel.outcome.in_(
                                (
                                    CapacityGovernanceAuditOutcome.DENIED.value,
                                    CapacityGovernanceAuditOutcome.CONFLICT.value,
                                )
                            ),
                            ReflectionCapacityGovernanceAuditEventModel.created_at >= bucket_start,
                            ReflectionCapacityGovernanceAuditEventModel.created_at <= now,
                        )
                        .order_by(
                            ReflectionCapacityGovernanceAuditEventModel.created_at.desc(),
                            ReflectionCapacityGovernanceAuditEventModel.id.desc(),
                        )
                        .limit(thresholds.audit_maximum_events + 1)
                    )
                )
                audit_truncated = len(audit_outcomes) > thresholds.audit_maximum_events
                audit_outcomes = audit_outcomes[: thresholds.audit_maximum_events]
                alert_rows = tuple(
                    await session.scalars(
                        select(ReflectionCapacityGovernanceAlertModel)
                        .where(*self._alert_scope())
                        .order_by(
                            ReflectionCapacityGovernanceAlertModel.updated_at.desc(),
                            ReflectionCapacityGovernanceAlertModel.id.desc(),
                        )
                        .limit(thresholds.maximum_alerts + 1)
                    )
                )
                alerts_truncated = len(alert_rows) > thresholds.maximum_alerts
                alert_rows = alert_rows[: thresholds.maximum_alerts]
                quality_thresholds = self.knowledge_quality_risk_thresholds
                quality_window_started_at = now - timedelta(
                    seconds=quality_thresholds.window_seconds
                )
                quality_snapshot_rows = tuple(
                    await session.scalars(
                        select(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel)
                        .where(
                            *self._knowledge_quality_scope(tenant_id),
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at
                            >= quality_window_started_at,
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at
                            <= now,
                        )
                        .order_by(
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.captured_at.desc(),
                            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id.desc(),
                        )
                        .limit(quality_thresholds.maximum_snapshots + 1)
                    )
                )
                quality_snapshots_truncated = (
                    len(quality_snapshot_rows) > quality_thresholds.maximum_snapshots
                )
                quality_snapshot_rows = quality_snapshot_rows[
                    : quality_thresholds.maximum_snapshots
                ]
                postmortem_rows = tuple(
                    await session.scalars(
                        select(ReflectionCapacityGovernancePostmortemModel)
                        .where(
                            *self._postmortem_scope(tenant_id),
                            ReflectionCapacityGovernancePostmortemModel.restore_count >= 1,
                            ReflectionCapacityGovernancePostmortemModel.status.in_(
                                (
                                    CapacityGovernancePostmortemStatus.PUBLISHED.value,
                                    CapacityGovernancePostmortemStatus.QUARANTINED.value,
                                )
                            ),
                        )
                        .order_by(
                            ReflectionCapacityGovernancePostmortemModel.updated_at.desc(),
                            ReflectionCapacityGovernancePostmortemModel.id.desc(),
                        )
                        .limit(quality_thresholds.maximum_snapshots + 1)
                    )
                )
                postmortems_truncated = len(postmortem_rows) > quality_thresholds.maximum_snapshots
                postmortem_rows = postmortem_rows[: quality_thresholds.maximum_snapshots]
                drill_report = await self._incident_drill_report(
                    session,
                    checked_at=now,
                )
                candidates: list[CapacityGovernanceIncidentCandidate] = []
                audit_counts = Counter(audit_outcomes)
                audit_candidate = build_audit_failure_incident_candidate(
                    tenant_id=tenant_id,
                    handler_version=self.handler_version,
                    bucket_start=bucket_start,
                    denied_count=audit_counts[CapacityGovernanceAuditOutcome.DENIED.value],
                    conflict_count=audit_counts[CapacityGovernanceAuditOutcome.CONFLICT.value],
                    thresholds=thresholds,
                )
                if audit_candidate is not None:
                    candidates.append(audit_candidate)
                for alert in alert_rows:
                    alert_status = CapacityGovernanceAlertStatus(alert.status)
                    sla_candidate = build_alert_sla_incident_candidate(
                        tenant_id=tenant_id,
                        handler_version=self.handler_version,
                        alert_id=alert.id,
                        alert_version=alert.version,
                        alert_status=alert_status,
                        first_seen_at=alert.first_seen_at,
                        updated_at=alert.updated_at,
                        now=now,
                        response_warning_seconds=self.alert_response_warning_seconds,
                        response_critical_seconds=self.alert_response_critical_seconds,
                    )
                    if sla_candidate is not None:
                        candidates.append(sla_candidate)
                    reopen_candidate = build_alert_reopen_incident_candidate(
                        tenant_id=tenant_id,
                        handler_version=self.handler_version,
                        alert_id=alert.id,
                        alert_version=alert.version,
                        alert_status=alert_status,
                        reopened_count=alert.reopened_count,
                        updated_at=alert.updated_at,
                        thresholds=thresholds,
                    )
                    if reopen_candidate is not None:
                        candidates.append(reopen_candidate)
                candidates.extend(
                    build_drill_incident_candidates(
                        tenant_id=tenant_id,
                        handler_version=self.handler_version,
                        report=drill_report,
                    )
                )
                quality_by_postmortem: dict[
                    UUID,
                    list[CapacityGovernanceKnowledgeQualitySnapshotRecord],
                ] = {}
                for snapshot_row in quality_snapshot_rows:
                    snapshot = _knowledge_quality_snapshot_record(snapshot_row)
                    quality_by_postmortem.setdefault(snapshot.postmortem_id, []).append(snapshot)
                if not quality_snapshots_truncated:
                    for postmortem_id, snapshots in quality_by_postmortem.items():
                        bounded_snapshots = tuple(snapshots)
                        unsafe_candidate = build_persistent_unsafe_knowledge_incident_candidate(
                            tenant_id=tenant_id,
                            handler_version=self.handler_version,
                            postmortem_id=postmortem_id,
                            snapshots=bounded_snapshots,
                            now=now,
                            thresholds=quality_thresholds,
                        )
                        if unsafe_candidate is not None:
                            candidates.append(unsafe_candidate)
                        degraded_candidate = build_repeated_degraded_knowledge_incident_candidate(
                            tenant_id=tenant_id,
                            handler_version=self.handler_version,
                            postmortem_id=postmortem_id,
                            snapshots=bounded_snapshots,
                            now=now,
                            thresholds=quality_thresholds,
                        )
                        if degraded_candidate is not None:
                            candidates.append(degraded_candidate)
                if not postmortems_truncated:
                    for postmortem in postmortem_rows:
                        requarantine_candidate = (
                            build_post_recovery_requarantine_incident_candidate(
                                tenant_id=tenant_id,
                                handler_version=self.handler_version,
                                postmortem_id=postmortem.id,
                                postmortem_status=CapacityGovernancePostmortemStatus(
                                    postmortem.status
                                ),
                                postmortem_version=postmortem.version,
                                knowledge_version=postmortem.knowledge_version or "",
                                content_fingerprint=postmortem.content_fingerprint,
                                restore_count=postmortem.restore_count,
                                last_restored_at=postmortem.last_restored_at,
                                last_quarantined_at=postmortem.last_quarantined_at,
                            )
                        )
                        if requarantine_candidate is not None:
                            candidates.append(requarantine_candidate)
                candidates_truncated = len(candidates) > thresholds.maximum_incidents
                candidates = candidates[: thresholds.maximum_incidents]
                existing_rows = tuple(
                    await session.scalars(
                        select(ReflectionCapacityGovernanceIncidentModel)
                        .where(*self._incident_scope(tenant_id))
                        .order_by(
                            ReflectionCapacityGovernanceIncidentModel.updated_at.desc(),
                            ReflectionCapacityGovernanceIncidentModel.id.desc(),
                        )
                        .limit(thresholds.maximum_incidents + 1)
                        .with_for_update()
                    )
                )
                incidents_truncated = len(existing_rows) > thresholds.maximum_incidents
                existing_rows = existing_rows[: thresholds.maximum_incidents]
                existing_by_fingerprint = {row.fingerprint: row for row in existing_rows}
                matched_fingerprints = {candidate.fingerprint for candidate in candidates}
                opened = 0
                updated = 0
                resolved = 0
                for candidate in candidates:
                    row = existing_by_fingerprint.get(candidate.fingerprint)
                    if row is None:
                        row = ReflectionCapacityGovernanceIncidentModel(
                            id=uuid4(),
                            tenant_id=tenant_id,
                            job_type=REFLECTION_JOB_TYPE,
                            handler_version=self.handler_version,
                            signal=candidate.signal.value,
                            rule_version=candidate.rule_version,
                            severity=candidate.severity.value,
                            status=CapacityGovernanceIncidentStatus.OPEN.value,
                            version=1,
                            source_id=candidate.source_id,
                            fingerprint=candidate.fingerprint,
                            evidence_fingerprint=candidate.evidence_fingerprint,
                            first_seen_at=now,
                            last_seen_at=now,
                            last_evidence_at=candidate.evidence_at,
                            occurrence_count=1,
                            reopened_count=0,
                            evidence=candidate.details,
                        )
                        session.add(row)
                        existing_by_fingerprint[candidate.fingerprint] = row
                        opened += 1
                        continue
                    if row.evidence_fingerprint == candidate.evidence_fingerprint:
                        continue
                    if row.status == CapacityGovernanceIncidentStatus.RESOLVED.value:
                        row.status = CapacityGovernanceIncidentStatus.OPEN.value
                        row.resolved_at = None
                        row.acknowledged_by = None
                        row.acknowledged_principal_id = None
                        row.acknowledged_token_id = None
                        row.acknowledged_at = None
                        row.reopened_count += 1
                    row.rule_version = candidate.rule_version
                    row.severity = candidate.severity.value
                    row.source_id = candidate.source_id
                    row.evidence_fingerprint = candidate.evidence_fingerprint
                    row.last_seen_at = now
                    row.last_evidence_at = candidate.evidence_at
                    row.occurrence_count += 1
                    row.evidence = candidate.details
                    row.version += 1
                    row.updated_at = now
                    updated += 1
                alerts_by_id = {row.id: row for row in alert_rows}
                failed_drill_checks = {
                    check.name for check in drill_report.checks if not check.passed
                }
                latest_quality_by_postmortem = {
                    postmortem_id: max(
                        snapshots,
                        key=lambda snapshot: (snapshot.captured_at, str(snapshot.id)),
                    )
                    for postmortem_id, snapshots in quality_by_postmortem.items()
                }
                postmortems_by_id = {row.id: row for row in postmortem_rows}
                for row in existing_rows:
                    if (
                        row.fingerprint in matched_fingerprints
                        or row.status == CapacityGovernanceIncidentStatus.RESOLVED.value
                    ):
                        continue
                    signal = CapacityGovernanceIncidentSignal(row.signal)
                    has_new_recovery_fact = False
                    if signal is CapacityGovernanceIncidentSignal.AUDIT_FAILURE_SPIKE:
                        stored_bucket = row.evidence.get("bucket_start")
                        if isinstance(stored_bucket, str):
                            try:
                                has_new_recovery_fact = bucket_start > datetime.fromisoformat(
                                    stored_bucket
                                )
                            except ValueError:
                                has_new_recovery_fact = False
                    elif signal in {
                        CapacityGovernanceIncidentSignal.ALERT_SLA_BREACHED,
                        CapacityGovernanceIncidentSignal.ALERT_REOPEN_REPEAT,
                    }:
                        source = (
                            alerts_by_id.get(row.source_id) if row.source_id is not None else None
                        )
                        has_new_recovery_fact = (
                            source is not None and source.updated_at > row.last_evidence_at
                        )
                    elif signal is CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED:
                        check_name = row.evidence.get("check_name")
                        has_new_recovery_fact = (
                            isinstance(check_name, str)
                            and check_name not in failed_drill_checks
                            and drill_report.checked_at > row.last_evidence_at
                        )
                    elif signal in {
                        CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT,
                        CapacityGovernanceIncidentSignal.KNOWLEDGE_DEGRADED_REPEAT,
                    }:
                        stored_postmortem_id = row.evidence.get("postmortem_id")
                        try:
                            risk_postmortem_id: UUID | None = UUID(str(stored_postmortem_id))
                        except (TypeError, ValueError):
                            risk_postmortem_id = None
                        latest_quality = (
                            latest_quality_by_postmortem.get(risk_postmortem_id)
                            if risk_postmortem_id is not None
                            else None
                        )
                        has_new_recovery_fact = (
                            not quality_snapshots_truncated
                            and latest_quality is not None
                            and latest_quality.captured_at > row.last_evidence_at
                        )
                    elif signal is CapacityGovernanceIncidentSignal.KNOWLEDGE_REQUARANTINED:
                        risk_postmortem = (
                            postmortems_by_id.get(row.source_id)
                            if row.source_id is not None
                            else None
                        )
                        has_new_recovery_fact = (
                            not postmortems_truncated
                            and risk_postmortem is not None
                            and risk_postmortem.updated_at > row.last_evidence_at
                        )
                    if not has_new_recovery_fact:
                        continue
                    row.status = CapacityGovernanceIncidentStatus.RESOLVED.value
                    row.resolved_at = now
                    row.version += 1
                    row.updated_at = now
                    resolved += 1
                if opened or updated or resolved:
                    metadata: dict[str, str | int | bool | None] = {
                        "incidents_opened": opened,
                        "incidents_resolved": resolved,
                        "incidents_updated": updated,
                    }
                    if authorized is not None:
                        self._append_audit(
                            session,
                            authorized=authorized,
                            action="capacity.incident.scan",
                            outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                            metadata=metadata,
                        )
                    else:
                        self._append_system_audit(
                            session,
                            tenant_id=tenant_id,
                            action="capacity.incident.scan",
                            metadata=metadata,
                        )
            return CapacityIncidentScanReport(
                handler_version=self.handler_version,
                scanned_audit_events=len(audit_outcomes),
                scanned_alerts=len(alert_rows),
                scanned_quality_snapshots=len(quality_snapshot_rows),
                scanned_postmortems=len(postmortem_rows),
                evaluated_drill_checks=len(drill_report.checks),
                matched_signals=len(candidates),
                opened_incidents=opened,
                updated_incidents=updated,
                resolved_incidents=resolved,
                truncated=(
                    audit_truncated
                    or alerts_truncated
                    or quality_snapshots_truncated
                    or postmortems_truncated
                    or candidates_truncated
                    or incidents_truncated
                ),
                scanned_at=now,
            )
        except Exception as exc:
            if actor is not None:
                await self._audit_failure(
                    actor=actor,
                    action="capacity.incident.scan",
                    exc=exc,
                )
            raise

    async def acknowledge_alert(
        self,
        *,
        alert_id: UUID,
        expected_version: int,
        actor: AuthenticatedPrincipal,
    ) -> CapacityGovernanceAlertRecord:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        try:
            async with self._sessions() as session, session.begin():
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_ALERTS_MANAGE,
                    for_update=True,
                )
                row = await session.scalar(
                    select(ReflectionCapacityGovernanceAlertModel)
                    .where(
                        ReflectionCapacityGovernanceAlertModel.id == alert_id,
                        *self._alert_scope(),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise KeyError("Unknown capacity governance alert")
                if (
                    row.version != expected_version
                    or row.status == CapacityGovernanceAlertStatus.RESOLVED.value
                ):
                    raise ReflectionCapacityGovernanceConflictError(
                        "Capacity governance alert changed before acknowledgement"
                    )
                if row.status == CapacityGovernanceAlertStatus.OPEN.value:
                    now = utc_now()
                    row.status = CapacityGovernanceAlertStatus.ACKNOWLEDGED.value
                    row.acknowledged_by = authorized.subject
                    row.acknowledged_principal_id = authorized.principal_id
                    row.acknowledged_token_id = authorized.token_id
                    row.acknowledged_at = now
                    row.version += 1
                    row.updated_at = now
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.alert.acknowledge",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    alert_id=row.id,
                )
            return _alert_record(
                row,
                response_warning_seconds=self.alert_response_warning_seconds,
                response_critical_seconds=self.alert_response_critical_seconds,
            )
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action="capacity.alert.acknowledge",
                exc=exc,
                alert_id=alert_id,
            )
            raise

    async def scan_drift(
        self,
        *,
        actor: AuthenticatedPrincipal | None = None,
    ) -> CapacityDriftScanReport:
        now = utc_now()
        async with self._sessions() as session, session.begin():
            authorized = None
            if actor is not None:
                authorized = await self._authorize(
                    session,
                    actor=actor,
                    permission=CAPACITY_ALERTS_MANAGE,
                    for_update=True,
                )
            await session.execute(select(func.pg_advisory_xact_lock(self._drift_lock_id())))
            active = await session.scalar(self._active_policy_statement())
            expected_thresholds = (
                ReflectionCapacityThresholds.model_validate(active.thresholds)
                if active is not None
                else self.fallback_thresholds
            )
            expected_fingerprint = capacity_threshold_fingerprint(expected_thresholds)
            rows = tuple(
                (
                    await session.execute(
                        select(
                            ReflectionCapacityObservationModel.observed_at,
                            ReflectionCapacityObservationModel.thresholds,
                        )
                        .where(
                            ReflectionCapacityObservationModel.job_type == REFLECTION_JOB_TYPE,
                            ReflectionCapacityObservationModel.handler_version
                            == self.handler_version,
                            ReflectionCapacityObservationModel.observed_at
                            >= now - timedelta(seconds=self.drift_window_seconds),
                            ReflectionCapacityObservationModel.observed_at <= now,
                        )
                        .order_by(ReflectionCapacityObservationModel.observed_at.desc())
                        .limit(self.drift_maximum_observations)
                    )
                ).all()
            )
            fingerprints = [
                capacity_threshold_fingerprint(
                    ReflectionCapacityThresholds.model_validate(thresholds)
                )
                for _, thresholds in rows
            ]
            counts = Counter(fingerprints)
            latest_by_fingerprint: dict[str, datetime] = {}
            for (observed_at, _), fingerprint in zip(rows, fingerprints, strict=True):
                latest_by_fingerprint.setdefault(fingerprint, observed_at)
            mismatches = {
                fingerprint: count
                for fingerprint, count in counts.items()
                if fingerprint != expected_fingerprint
            }
            active_mismatch_fingerprints = set(mismatches)
            existing = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceAlertModel)
                    .where(*self._alert_scope())
                    .with_for_update()
                )
            )
            existing_by_key = {row.dedupe_key: row for row in existing}
            opened = 0
            updated = 0
            resolved = 0
            for observed_fingerprint, count in mismatches.items():
                if count < self.drift_minimum_observations:
                    continue
                dedupe_key = capacity_drift_dedupe_key(
                    handler_version=self.handler_version,
                    expected_fingerprint=expected_fingerprint,
                    observed_fingerprint=observed_fingerprint,
                )
                severity = (
                    CapacityGovernanceAlertSeverity.CRITICAL
                    if count >= self.drift_critical_observations
                    else CapacityGovernanceAlertSeverity.WARNING
                )
                latest_observation_at = latest_by_fingerprint[observed_fingerprint]
                row = existing_by_key.get(dedupe_key)
                if row is None:
                    session.add(
                        ReflectionCapacityGovernanceAlertModel(
                            id=uuid4(),
                            job_type=REFLECTION_JOB_TYPE,
                            handler_version=self.handler_version,
                            alert_type=CapacityGovernanceAlertType.POLICY_DRIFT.value,
                            severity=severity.value,
                            status=CapacityGovernanceAlertStatus.OPEN.value,
                            version=1,
                            dedupe_key=dedupe_key,
                            expected_policy_id=active.id if active is not None else None,
                            expected_policy_version=(
                                active.policy_version if active is not None else None
                            ),
                            expected_fingerprint=expected_fingerprint,
                            observed_fingerprint=observed_fingerprint,
                            first_seen_at=now,
                            last_seen_at=now,
                            last_observation_at=latest_observation_at,
                            sample_count=count,
                            details={
                                "drift_window_seconds": self.drift_window_seconds,
                                "expected_source": (
                                    "active_policy" if active is not None else "settings_fallback"
                                ),
                            },
                            reopened_count=0,
                        )
                    )
                    opened += 1
                    continue
                has_new_observation = latest_observation_at > row.last_observation_at
                if not has_new_observation:
                    continue
                if row.status == CapacityGovernanceAlertStatus.RESOLVED.value:
                    row.status = CapacityGovernanceAlertStatus.OPEN.value
                    row.resolved_at = None
                    row.acknowledged_by = None
                    row.acknowledged_principal_id = None
                    row.acknowledged_token_id = None
                    row.acknowledged_at = None
                    row.reopened_count += 1
                row.severity = severity.value
                row.last_seen_at = now
                row.last_observation_at = latest_observation_at
                row.sample_count = count
                row.version += 1
                row.updated_at = now
                updated += 1
            newest_observation_at = rows[0][0] if rows else None
            for row in existing:
                if row.status == CapacityGovernanceAlertStatus.RESOLVED.value:
                    continue
                if (
                    row.expected_fingerprint == expected_fingerprint
                    and row.observed_fingerprint in active_mismatch_fingerprints
                ):
                    continue
                if (
                    newest_observation_at is None
                    or newest_observation_at <= row.last_observation_at
                ):
                    continue
                row.status = CapacityGovernanceAlertStatus.RESOLVED.value
                row.resolved_at = now
                row.version += 1
                row.updated_at = now
                resolved += 1
            if authorized is not None:
                self._append_audit(
                    session,
                    authorized=authorized,
                    action="capacity.drift.scan",
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    metadata={
                        "opened": opened,
                        "resolved": resolved,
                        "updated": updated,
                    },
                )
        return CapacityDriftScanReport(
            handler_version=self.handler_version,
            expected_policy_id=active.id if active is not None else None,
            expected_policy_version=active.policy_version if active is not None else None,
            expected_fingerprint=expected_fingerprint,
            scanned_observations=len(rows),
            drifted_observations=sum(mismatches.values()),
            opened_alerts=opened,
            updated_alerts=updated,
            resolved_alerts=resolved,
            insufficient_samples=len(rows) < self.drift_minimum_observations,
            scanned_at=now,
        )

    async def _incident_drill_report(
        self,
        session: AsyncSession,
        *,
        checked_at: datetime,
    ) -> CapacityGovernanceDrillReport:
        trigger_present = bool(
            await session.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_trigger "
                    "WHERE tgname = 'trg_reflection_capacity_governance_audit_no_update' "
                    "AND tgrelid = 'reflection_capacity_governance_audit_events'::regclass "
                    "AND NOT tgisinternal)"
                )
            )
        )
        alert_constraint_names = frozenset(
            await session.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'reflection_capacity_governance_alerts'::regclass"
                )
            )
        )
        incident_constraint_names = frozenset(
            await session.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = "
                    "'reflection_capacity_governance_incidents'::regclass"
                )
            )
        )
        remediation_constraint_names = frozenset(
            await session.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = "
                    "'reflection_capacity_governance_remediations'::regclass"
                )
            )
        )
        postmortem_constraint_names = frozenset(
            await session.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = "
                    "'reflection_capacity_governance_postmortems'::regclass"
                )
            )
        )
        knowledge_feedback_constraint_names = frozenset(
            await session.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = "
                    "'reflection_capacity_governance_knowledge_feedback'::regclass"
                )
            )
        )
        knowledge_quality_constraint_names = frozenset(
            await session.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = "
                    "'reflection_capacity_governance_knowledge_quality_snapshots'::regclass"
                )
            )
        )
        knowledge_recovery_constraint_names = frozenset(
            await session.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = "
                    "'reflection_capacity_governance_knowledge_recoveries'::regclass"
                )
            )
        )
        quality_trigger_present = bool(
            await session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_trigger "
                    "WHERE tgname = "
                    "'trg_capacity_knowledge_quality_snapshots_append_only' "
                    "AND tgrelid = "
                    "'reflection_capacity_governance_knowledge_quality_snapshots'::regclass "
                    "AND NOT tgisinternal)"
                )
            )
        )
        audit_index_names = frozenset(
            await session.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = 'reflection_capacity_governance_audit_events'"
                )
            )
        )
        incident_index_names = frozenset(
            await session.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = 'reflection_capacity_governance_incidents'"
                )
            )
        )
        remediation_index_names = frozenset(
            await session.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = 'reflection_capacity_governance_remediations'"
                )
            )
        )
        postmortem_index_names = frozenset(
            await session.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = 'reflection_capacity_governance_postmortems'"
                )
            )
        )
        knowledge_feedback_index_names = frozenset(
            await session.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = "
                    "'reflection_capacity_governance_knowledge_feedback'"
                )
            )
        )
        knowledge_quality_index_names = frozenset(
            await session.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = "
                    "'reflection_capacity_governance_knowledge_quality_snapshots'"
                )
            )
        )
        knowledge_recovery_index_names = frozenset(
            await session.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = "
                    "'reflection_capacity_governance_knowledge_recoveries'"
                )
            )
        )
        privileged_permissions = {
            CAPACITY_GOVERNANCE_REQUEST,
            CAPACITY_GOVERNANCE_APPROVE,
            CAPACITY_GOVERNANCE_PUBLISH,
            CAPACITY_GOVERNANCE_REVIEW,
            CAPACITY_GOVERNANCE_ROLLBACK,
            CAPACITY_ALERTS_MANAGE,
            CAPACITY_AUDIT_READ,
            CAPACITY_INCIDENTS_MANAGE,
            CAPACITY_REMEDIATIONS_REQUEST,
            CAPACITY_REMEDIATIONS_APPROVE,
            CAPACITY_REMEDIATIONS_EXECUTE,
            CAPACITY_REMEDIATIONS_VERIFY,
            CAPACITY_POSTMORTEMS_REQUEST,
            CAPACITY_POSTMORTEMS_REVIEW,
            CAPACITY_KNOWLEDGE_FEEDBACK_REPORT,
            CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
            CAPACITY_KNOWLEDGE_QUALITY_ASSESS,
            CAPACITY_KNOWLEDGE_RECOVERY_REQUEST,
            CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
        }
        assignment_counts = {
            permission: sum(permission in role.permissions for role in CAPACITY_GOVERNANCE_ROLES)
            for permission in privileged_permissions
        }
        role_separation = all(count == 1 for count in assignment_counts.values())
        alert_lifecycle_constraints = {
            "ck_reflection_capacity_governance_alerts_policy",
            "ck_reflection_capacity_governance_alerts_lifecycle",
        }
        incident_lifecycle_constraints = {
            "ck_reflection_capacity_governance_incidents_lifecycle",
            "ck_reflection_capacity_governance_incidents_fingerprints",
        }
        expected_audit_indexes = {
            "ix_reflection_capacity_governance_audit_tenant_created",
            "ix_reflection_capacity_governance_audit_filter_created",
        }
        expected_incident_indexes = {
            "ix_reflection_capacity_governance_incidents_tenant_status",
            "ix_reflection_capacity_governance_incidents_source",
        }
        remediation_lifecycle_constraints = {
            "ck_capacity_remediations_lifecycle",
            "uq_capacity_remediations_incident_cycle",
        }
        expected_remediation_indexes = {
            "ix_capacity_remediations_tenant_status",
            "ix_capacity_remediations_incident",
        }
        postmortem_lifecycle_constraints = {
            "ck_capacity_postmortems_lifecycle",
            "ck_capacity_postmortems_quarantine_history",
            "ck_capacity_postmortems_restore_history",
            "uq_capacity_postmortems_remediation",
            "uq_capacity_postmortems_tenant_fingerprint",
        }
        expected_postmortem_indexes = {
            "ix_capacity_postmortems_tenant_status",
            "ix_capacity_postmortems_source",
            "ix_capacity_postmortems_search_vector_gin",
            "ix_capacity_postmortems_embedding_hnsw",
        }
        knowledge_feedback_lifecycle_constraints = {
            "ck_capacity_knowledge_feedback_status",
            "ck_capacity_knowledge_feedback_signal",
            "ck_capacity_knowledge_feedback_reason",
            "ck_capacity_knowledge_feedback_safety_pair",
            "ck_capacity_knowledge_feedback_versions",
            "ck_capacity_knowledge_feedback_lifecycle",
            "uq_capacity_knowledge_feedback_reporter_version",
        }
        expected_knowledge_feedback_indexes = {
            "uq_capacity_knowledge_feedback_reporter_version",
            "ix_capacity_knowledge_feedback_tenant_status",
            "ix_capacity_knowledge_feedback_postmortem",
        }
        knowledge_quality_constraints = {
            "ck_capacity_knowledge_quality_assessment",
            "ck_capacity_knowledge_quality_counts",
            "ck_capacity_knowledge_quality_versions",
            "uq_capacity_knowledge_quality_evidence",
        }
        expected_knowledge_quality_indexes = {
            "uq_capacity_knowledge_quality_evidence",
            "ix_capacity_knowledge_quality_tenant_assessment",
            "ix_capacity_knowledge_quality_tenant_captured",
            "ix_capacity_knowledge_quality_postmortem",
        }
        knowledge_recovery_constraints = {
            "ck_capacity_knowledge_recoveries_reason",
            "ck_capacity_knowledge_recoveries_status",
            "ck_capacity_knowledge_recoveries_versions",
            "ck_capacity_knowledge_recoveries_lifecycle",
        }
        expected_knowledge_recovery_indexes = {
            "uq_capacity_knowledge_recoveries_active",
            "ix_capacity_knowledge_recoveries_tenant_status",
            "ix_capacity_knowledge_recoveries_postmortem",
        }
        checks = (
            CapacityGovernanceDrillCheck(
                name="current_actor_revalidated",
                passed=True,
                detail=(
                    "Current tenant, Principal, Token, permission and global scope "
                    "passed PostgreSQL revalidation when an actor was supplied."
                ),
            ),
            CapacityGovernanceDrillCheck(
                name="role_separation",
                passed=role_separation,
                detail=(
                    "Each privileged capacity permission is assigned to exactly one "
                    "least-privilege role template."
                ),
            ),
            CapacityGovernanceDrillCheck(
                name="audit_append_only",
                passed=trigger_present,
                detail="The PostgreSQL audit UPDATE rejection trigger is present.",
            ),
            CapacityGovernanceDrillCheck(
                name="alert_lifecycle_constraints",
                passed=alert_lifecycle_constraints <= alert_constraint_names,
                detail="Policy binding and alert lifecycle CHECK constraints are present.",
            ),
            CapacityGovernanceDrillCheck(
                name="audit_query_indexes",
                passed=expected_audit_indexes <= audit_index_names,
                detail="Tenant chronology and bounded audit query indexes are present.",
            ),
            CapacityGovernanceDrillCheck(
                name="incident_lifecycle_constraints",
                passed=(incident_lifecycle_constraints <= incident_constraint_names),
                detail="Incident lifecycle and fingerprint CHECK constraints are present.",
            ),
            CapacityGovernanceDrillCheck(
                name="incident_query_indexes",
                passed=expected_incident_indexes <= incident_index_names,
                detail="Bounded incident status and source indexes are present.",
            ),
            CapacityGovernanceDrillCheck(
                name="remediation_lifecycle_constraints",
                passed=(remediation_lifecycle_constraints <= remediation_constraint_names),
                detail=(
                    "Remediation lifecycle and one-plan-per-incident-cycle constraints are present."
                ),
            ),
            CapacityGovernanceDrillCheck(
                name="remediation_query_indexes",
                passed=expected_remediation_indexes <= remediation_index_names,
                detail="Bounded remediation status and incident indexes are present.",
            ),
            CapacityGovernanceDrillCheck(
                name="postmortem_lifecycle_constraints",
                passed=(postmortem_lifecycle_constraints <= postmortem_constraint_names),
                detail=(
                    "Postmortem lifecycle, source uniqueness and fingerprint "
                    "constraints are present."
                ),
            ),
            CapacityGovernanceDrillCheck(
                name="postmortem_query_indexes",
                passed=expected_postmortem_indexes <= postmortem_index_names,
                detail=("Bounded postmortem and hybrid governance knowledge indexes are present."),
            ),
            CapacityGovernanceDrillCheck(
                name="knowledge_feedback_lifecycle_constraints",
                passed=(
                    knowledge_feedback_lifecycle_constraints <= knowledge_feedback_constraint_names
                ),
                detail=(
                    "Feedback classifications, review lifecycle and reporter-version "
                    "uniqueness constraints are present."
                ),
            ),
            CapacityGovernanceDrillCheck(
                name="knowledge_feedback_query_indexes",
                passed=(expected_knowledge_feedback_indexes <= knowledge_feedback_index_names),
                detail=(
                    "Feedback reporter-version uniqueness plus bounded status and "
                    "postmortem query indexes are present."
                ),
            ),
            CapacityGovernanceDrillCheck(
                name="knowledge_quality_snapshot_controls",
                passed=(
                    quality_trigger_present
                    and knowledge_quality_constraints <= knowledge_quality_constraint_names
                ),
                detail=(
                    "Quality snapshot classification, count, evidence uniqueness and "
                    "append-only UPDATE controls are present."
                ),
            ),
            CapacityGovernanceDrillCheck(
                name="knowledge_quality_query_indexes",
                passed=(expected_knowledge_quality_indexes <= knowledge_quality_index_names),
                detail=(
                    "Quality evidence uniqueness plus bounded assessment, captured-time "
                    "trend and postmortem indexes are present."
                ),
            ),
            CapacityGovernanceDrillCheck(
                name="knowledge_recovery_lifecycle_constraints",
                passed=(knowledge_recovery_constraints <= knowledge_recovery_constraint_names),
                detail=("Recovery reason, lifecycle and version constraints are present."),
            ),
            CapacityGovernanceDrillCheck(
                name="knowledge_recovery_query_indexes",
                passed=(expected_knowledge_recovery_indexes <= knowledge_recovery_index_names),
                detail=(
                    "Single active recovery plus bounded status and postmortem indexes are present."
                ),
            ),
        )
        return CapacityGovernanceDrillReport(
            passed=all(check.passed for check in checks),
            checks=checks,
            checked_at=checked_at,
        )

    async def _governance_action(
        self,
        *,
        action: str,
        permission: str,
        actor: AuthenticatedPrincipal,
        target_request_id: UUID | None,
        operation: Callable[
            [CapacityOperatorResolver],
            Awaitable[ReflectionCapacityChangeRequestRecord],
        ],
    ) -> ReflectionCapacityChangeRequestRecord:
        async def resolver(session: AsyncSession) -> str:
            authorized = await self._authorize(
                session,
                actor=actor,
                permission=permission,
                for_update=True,
            )
            self._append_audit(
                session,
                authorized=authorized,
                action=action,
                outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                request_id=target_request_id,
            )
            return authorized.subject

        try:
            result = await operation(resolver)
        except Exception as exc:
            await self._audit_failure(
                actor=actor,
                action=action,
                exc=exc,
                request_id=target_request_id,
            )
            raise
        return result

    async def _authorize(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedPrincipal,
        permission: str,
        for_update: bool = False,
    ) -> AuthorizedGlobalActor:
        return await authorize_global_operation(
            session,
            actor=actor,
            governance_tenant=self.governance_tenant,
            permission=permission,
            for_update=for_update,
        )

    async def _audit_failure(
        self,
        *,
        actor: AuthenticatedPrincipal,
        action: str,
        exc: Exception,
        request_id: UUID | None = None,
        alert_id: UUID | None = None,
        incident_id: UUID | None = None,
        postmortem_id: UUID | None = None,
    ) -> None:
        if actor.principal_id is None or actor.token_id is None:
            return
        try:
            async with self._sessions() as session, session.begin():
                tenant_id = await session.scalar(
                    select(TenantModel.id).where(TenantModel.slug == self.governance_tenant)
                )
                if tenant_id is None:
                    return
                outcome = (
                    CapacityGovernanceAuditOutcome.DENIED
                    if isinstance(exc, CapacityGovernanceAuthorizationError)
                    else CapacityGovernanceAuditOutcome.CONFLICT
                )
                safe_incident_id = None
                if incident_id is not None:
                    safe_incident_id = await session.scalar(
                        select(ReflectionCapacityGovernanceIncidentModel.id).where(
                            ReflectionCapacityGovernanceIncidentModel.id == incident_id,
                            ReflectionCapacityGovernanceIncidentModel.tenant_id == tenant_id,
                            ReflectionCapacityGovernanceIncidentModel.handler_version
                            == self.handler_version,
                        )
                    )
                safe_postmortem_id = None
                if postmortem_id is not None:
                    safe_postmortem_id = await session.scalar(
                        select(ReflectionCapacityGovernancePostmortemModel.id).where(
                            ReflectionCapacityGovernancePostmortemModel.id == postmortem_id,
                            ReflectionCapacityGovernancePostmortemModel.tenant_id == tenant_id,
                            ReflectionCapacityGovernancePostmortemModel.handler_version
                            == self.handler_version,
                        )
                    )
                session.add(
                    ReflectionCapacityGovernanceAuditEventModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        actor_principal_id=actor.principal_id,
                        actor_token_id=actor.token_id,
                        handler_version=self.handler_version,
                        request_id=request_id,
                        alert_id=alert_id,
                        incident_id=safe_incident_id,
                        postmortem_id=safe_postmortem_id,
                        action=action,
                        outcome=outcome.value,
                        safe_metadata={"error_type": type(exc).__name__},
                    )
                )
        except Exception:
            return

    def _append_audit(
        self,
        session: AsyncSession,
        *,
        authorized: AuthorizedGlobalActor,
        action: str,
        outcome: CapacityGovernanceAuditOutcome,
        request_id: UUID | None = None,
        alert_id: UUID | None = None,
        incident_id: UUID | None = None,
        postmortem_id: UUID | None = None,
        metadata: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        session.add(
            ReflectionCapacityGovernanceAuditEventModel(
                id=uuid4(),
                tenant_id=authorized.tenant_id,
                actor_principal_id=authorized.principal_id,
                actor_token_id=authorized.token_id,
                handler_version=self.handler_version,
                request_id=request_id,
                alert_id=alert_id,
                incident_id=incident_id,
                postmortem_id=postmortem_id,
                action=action,
                outcome=outcome.value,
                safe_metadata=metadata or {},
            )
        )

    def _append_system_audit(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        action: str,
        metadata: dict[str, str | int | bool | None],
    ) -> None:
        session.add(
            ReflectionCapacityGovernanceAuditEventModel(
                id=uuid4(),
                tenant_id=tenant_id,
                actor_principal_id=None,
                actor_token_id=None,
                handler_version=self.handler_version,
                request_id=None,
                alert_id=None,
                incident_id=None,
                postmortem_id=None,
                action=action,
                outcome=CapacityGovernanceAuditOutcome.SUCCESS.value,
                safe_metadata=metadata,
            )
        )

    def _request_scope(self) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityChangeRequestModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityChangeRequestModel.handler_version == self.handler_version,
        ]

    def _alert_scope(self) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityGovernanceAlertModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityGovernanceAlertModel.handler_version == self.handler_version,
        ]

    def _incident_scope(self, tenant_id: UUID) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityGovernanceIncidentModel.tenant_id == tenant_id,
            ReflectionCapacityGovernanceIncidentModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityGovernanceIncidentModel.handler_version == self.handler_version,
        ]

    def _remediation_scope(self, tenant_id: UUID) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityGovernanceRemediationModel.tenant_id == tenant_id,
            ReflectionCapacityGovernanceRemediationModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityGovernanceRemediationModel.handler_version == self.handler_version,
        ]

    def _postmortem_scope(self, tenant_id: UUID) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityGovernancePostmortemModel.tenant_id == tenant_id,
            ReflectionCapacityGovernancePostmortemModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityGovernancePostmortemModel.handler_version == self.handler_version,
        ]

    def _knowledge_feedback_scope(self, tenant_id: UUID) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityGovernanceKnowledgeFeedbackModel.tenant_id == tenant_id,
            ReflectionCapacityGovernanceKnowledgeFeedbackModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityGovernanceKnowledgeFeedbackModel.handler_version
            == self.handler_version,
        ]

    def _knowledge_quality_scope(self, tenant_id: UUID) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.tenant_id == tenant_id,
            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.job_type
            == REFLECTION_JOB_TYPE,
            ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.handler_version
            == self.handler_version,
        ]

    def _knowledge_recovery_scope(self, tenant_id: UUID) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityGovernanceKnowledgeRecoveryModel.tenant_id == tenant_id,
            ReflectionCapacityGovernanceKnowledgeRecoveryModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityGovernanceKnowledgeRecoveryModel.handler_version
            == self.handler_version,
        ]

    def _knowledge_recertification_scope(self, tenant_id: UUID) -> list[ColumnElement[bool]]:
        return [
            ReflectionCapacityGovernanceKnowledgeRecertificationModel.tenant_id == tenant_id,
            ReflectionCapacityGovernanceKnowledgeRecertificationModel.job_type
            == REFLECTION_JOB_TYPE,
            ReflectionCapacityGovernanceKnowledgeRecertificationModel.handler_version
            == self.handler_version,
        ]

    def _active_policy_statement(
        self,
    ) -> Select[tuple[ReflectionCapacityPolicyModel]]:
        return select(ReflectionCapacityPolicyModel).where(
            ReflectionCapacityPolicyModel.job_type == REFLECTION_JOB_TYPE,
            ReflectionCapacityPolicyModel.handler_version == self.handler_version,
            ReflectionCapacityPolicyModel.status == "active",
        )

    def _scope_hash(self, actor: AuthorizedGlobalActor) -> str:
        return hashlib.sha256(
            f"{actor.tenant_id}|{actor.principal_id}|{self.handler_version}".encode()
        ).hexdigest()

    def _drift_lock_id(self) -> int:
        digest = hashlib.sha256(
            f"reflection-capacity-drift|{self.handler_version}".encode()
        ).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    def _incident_lock_id(self) -> int:
        digest = hashlib.sha256(
            (
                f"reflection-capacity-incident|{self.governance_tenant}|{self.handler_version}"
            ).encode()
        ).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _alert_record(
    row: ReflectionCapacityGovernanceAlertModel,
    *,
    response_warning_seconds: int,
    response_critical_seconds: int,
) -> CapacityGovernanceAlertRecord:
    return CapacityGovernanceAlertRecord(
        id=row.id,
        handler_version=row.handler_version,
        alert_type=CapacityGovernanceAlertType(row.alert_type),
        severity=CapacityGovernanceAlertSeverity(row.severity),
        status=CapacityGovernanceAlertStatus(row.status),
        version=row.version,
        expected_policy_id=row.expected_policy_id,
        expected_policy_version=row.expected_policy_version,
        expected_fingerprint=row.expected_fingerprint,
        observed_fingerprint=row.observed_fingerprint,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        last_observation_at=row.last_observation_at,
        sample_count=row.sample_count,
        details=row.details,
        acknowledged_by=row.acknowledged_by,
        acknowledged_principal_id=row.acknowledged_principal_id,
        acknowledged_token_id=row.acknowledged_token_id,
        acknowledged_at=row.acknowledged_at,
        resolved_at=row.resolved_at,
        reopened_count=row.reopened_count,
        sla=assess_capacity_alert_sla(
            status=CapacityGovernanceAlertStatus(row.status),
            first_seen_at=row.first_seen_at,
            now=utc_now(),
            response_warning_seconds=response_warning_seconds,
            response_critical_seconds=response_critical_seconds,
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


_INCIDENT_EVIDENCE_KEYS = frozenset(
    {
        "alert_version",
        "audit_window_seconds",
        "bucket_start",
        "check_name",
        "conflict_count",
        "denied_count",
        "reopened_count",
        "sample_count",
        "sla_state",
    }
)


def _incident_record(
    row: ReflectionCapacityGovernanceIncidentModel,
) -> CapacityGovernanceIncidentRecord:
    return CapacityGovernanceIncidentRecord(
        id=row.id,
        handler_version=row.handler_version,
        signal=CapacityGovernanceIncidentSignal(row.signal),
        rule_version=row.rule_version,
        severity=CapacityGovernanceIncidentSeverity(row.severity),
        status=CapacityGovernanceIncidentStatus(row.status),
        version=row.version,
        source_id=row.source_id,
        fingerprint=row.fingerprint,
        evidence_fingerprint=row.evidence_fingerprint,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        last_evidence_at=row.last_evidence_at,
        occurrence_count=row.occurrence_count,
        reopened_count=row.reopened_count,
        details={
            key: value
            for key, value in row.evidence.items()
            if key in _INCIDENT_EVIDENCE_KEYS
            and (value is None or isinstance(value, str | int | float | bool))
        },
        acknowledged_by=row.acknowledged_by,
        acknowledged_at=row.acknowledged_at,
        resolved_at=row.resolved_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _remediation_record(
    row: ReflectionCapacityGovernanceRemediationModel,
) -> CapacityGovernanceRemediationRecord:
    return CapacityGovernanceRemediationRecord(
        id=row.id,
        incident_id=row.incident_id,
        handler_version=row.handler_version,
        incident_cycle=row.incident_cycle,
        playbook=CapacityGovernanceRemediationPlaybook(row.playbook),
        status=CapacityGovernanceRemediationStatus(row.status),
        version=row.version,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        rejected_by=row.rejected_by,
        rejected_at=row.rejected_at,
        executed_by=row.executed_by,
        executed_at=row.executed_at,
        execution_result=(
            CapacityGovernanceRemediationExecutionResult(row.execution_result)
            if row.execution_result is not None
            else None
        ),
        execution_evidence=(
            CapacityGovernanceRemediationEvidence(row.execution_evidence)
            if row.execution_evidence is not None
            else None
        ),
        incident_version_at_execution=row.incident_version_at_execution,
        verified_by=row.verified_by,
        verified_at=row.verified_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _postmortem_record(
    row: ReflectionCapacityGovernancePostmortemModel,
) -> CapacityGovernancePostmortemRecord:
    return CapacityGovernancePostmortemRecord(
        id=row.id,
        incident_id=row.incident_id,
        remediation_id=row.remediation_id,
        handler_version=row.handler_version,
        incident_cycle=row.incident_cycle,
        incident_version=row.incident_version,
        remediation_version=row.remediation_version,
        status=CapacityGovernancePostmortemStatus(row.status),
        version=row.version,
        root_cause=CapacityGovernancePostmortemRootCause(row.root_cause),
        impact=CapacityGovernancePostmortemImpact(row.impact),
        prevention=CapacityGovernancePostmortemPrevention(row.prevention),
        summary=row.summary,
        content_fingerprint=row.content_fingerprint,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        knowledge_namespace=row.knowledge_namespace,
        knowledge_source_key=row.knowledge_source_key,
        knowledge_version=row.knowledge_version,
        published_at=row.published_at,
        last_quarantined_at=row.last_quarantined_at,
        quarantine_feedback_id=row.quarantine_feedback_id,
        restore_count=row.restore_count,
        last_restored_at=row.last_restored_at,
        last_certified_at=row.last_certified_at,
        retired_at=row.retired_at,
        retired_by=row.retired_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _knowledge_feedback_record(
    row: ReflectionCapacityGovernanceKnowledgeFeedbackModel,
) -> CapacityGovernanceKnowledgeFeedbackRecord:
    return CapacityGovernanceKnowledgeFeedbackRecord(
        id=row.id,
        postmortem_id=row.postmortem_id,
        handler_version=row.handler_version,
        postmortem_version=row.postmortem_version,
        knowledge_version=row.knowledge_version,
        content_fingerprint=row.content_fingerprint,
        signal=CapacityGovernanceKnowledgeFeedbackSignal(row.signal),
        reason=CapacityGovernanceKnowledgeFeedbackReason(row.reason),
        status=CapacityGovernanceKnowledgeFeedbackStatus(row.status),
        version=row.version,
        reported_by=row.reported_by,
        reported_at=row.reported_at,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _knowledge_quality_snapshot_record(
    row: ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel,
) -> CapacityGovernanceKnowledgeQualitySnapshotRecord:
    return CapacityGovernanceKnowledgeQualitySnapshotRecord(
        id=row.id,
        postmortem_id=row.postmortem_id,
        handler_version=row.handler_version,
        postmortem_version=row.postmortem_version,
        knowledge_version=row.knowledge_version,
        content_fingerprint=row.content_fingerprint,
        evidence_fingerprint=row.evidence_fingerprint,
        assessment=CapacityGovernanceKnowledgeQualityAssessment(row.assessment),
        total_feedback=row.total_feedback,
        awaiting_review_count=row.awaiting_review_count,
        confirmed_helpful_count=row.confirmed_helpful_count,
        confirmed_not_helpful_count=row.confirmed_not_helpful_count,
        confirmed_safety_count=row.confirmed_safety_count,
        dismissed_count=row.dismissed_count,
        superseded_count=row.superseded_count,
        captured_by=row.captured_by,
        captured_at=row.captured_at,
        created_at=row.created_at,
    )


def _knowledge_recovery_record(
    row: ReflectionCapacityGovernanceKnowledgeRecoveryModel,
) -> CapacityGovernanceKnowledgeRecoveryRecord:
    return CapacityGovernanceKnowledgeRecoveryRecord(
        id=row.id,
        postmortem_id=row.postmortem_id,
        snapshot_id=row.snapshot_id,
        handler_version=row.handler_version,
        postmortem_version=row.postmortem_version,
        knowledge_version=row.knowledge_version,
        content_fingerprint=row.content_fingerprint,
        quarantine_feedback_id=row.quarantine_feedback_id,
        reason=CapacityGovernanceKnowledgeRecoveryReason(row.reason),
        status=CapacityGovernanceKnowledgeRecoveryStatus(row.status),
        version=row.version,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        restored_knowledge_version=row.restored_knowledge_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _knowledge_recertification_record(
    row: ReflectionCapacityGovernanceKnowledgeRecertificationModel,
) -> CapacityGovernanceKnowledgeRecertificationRecord:
    return CapacityGovernanceKnowledgeRecertificationRecord(
        id=row.id,
        postmortem_id=row.postmortem_id,
        quality_snapshot_id=row.quality_snapshot_id,
        handler_version=row.handler_version,
        postmortem_version=row.postmortem_version,
        knowledge_version=row.knowledge_version,
        content_fingerprint=row.content_fingerprint,
        quality_evidence_fingerprint=row.quality_evidence_fingerprint,
        decision=CapacityGovernanceKnowledgeRecertificationDecision(row.decision),
        reason=CapacityGovernanceKnowledgeRecertificationReason(row.reason),
        status=CapacityGovernanceKnowledgeRecertificationStatus(row.status),
        version=row.version,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _restored_knowledge_version(
    postmortem: ReflectionCapacityGovernancePostmortemModel,
) -> str:
    if postmortem.knowledge_version is None:
        raise ReflectionCapacityGovernanceConflictError(
            "Knowledge recovery requires a published knowledge version"
        )
    suffix = f"-r{postmortem.restore_count + 1}-v{postmortem.version + 1}"
    return f"{postmortem.knowledge_version[: 100 - len(suffix)]}{suffix}"


_AUDIT_METADATA_KEYS = frozenset(
    {
        "error_type",
        "execution_result",
        "feedback_reason",
        "feedback_signal",
        "feedback_status",
        "knowledge_quality_assessment",
        "knowledge_recovery_reason",
        "knowledge_recovery_status",
        "incidents_opened",
        "incidents_resolved",
        "incidents_updated",
        "opened",
        "playbook",
        "postmortem_status",
        "quality_total_feedback",
        "remediation_status",
        "resolved",
        "superseded_feedback",
        "updated",
    }
)


def _audit_record(
    row: ReflectionCapacityGovernanceAuditEventModel,
    actor_subject: str | None,
) -> CapacityGovernanceAuditRecord:
    return CapacityGovernanceAuditRecord(
        id=row.id,
        handler_version=row.handler_version,
        actor_subject=actor_subject,
        request_id=row.request_id,
        alert_id=row.alert_id,
        incident_id=row.incident_id,
        postmortem_id=row.postmortem_id,
        action=row.action,
        outcome=CapacityGovernanceAuditOutcome(row.outcome),
        safe_metadata={
            key: value
            for key, value in row.safe_metadata.items()
            if key in _AUDIT_METADATA_KEYS
            and (value is None or isinstance(value, str | int | float | bool))
        },
        created_at=row.created_at,
    )


def _audit_cursor_filters(
    query: CapacityGovernanceAuditQuery,
) -> dict[str, str | None]:
    return {
        "action": query.action,
        "actor_subject": query.actor_subject,
        "occurred_from": (
            query.occurred_from.isoformat() if query.occurred_from is not None else None
        ),
        "occurred_to": (query.occurred_to.isoformat() if query.occurred_to is not None else None),
        "outcome": query.outcome.value if query.outcome is not None else None,
    }


def _incident_cursor_filters(
    query: CapacityGovernanceIncidentQuery,
) -> dict[str, str | None]:
    return {
        "severity": query.severity.value if query.severity is not None else None,
        "signal": query.signal.value if query.signal is not None else None,
        "status": query.status.value if query.status is not None else None,
    }


def _remediation_cursor_filters(
    query: CapacityGovernanceRemediationQuery,
) -> dict[str, str | None]:
    return {
        "incident_id": str(query.incident_id) if query.incident_id is not None else None,
        "status": query.status.value if query.status is not None else None,
    }


def _postmortem_cursor_filters(
    query: CapacityGovernancePostmortemQuery,
) -> dict[str, str | None]:
    return {
        "incident_id": str(query.incident_id) if query.incident_id is not None else None,
        "remediation_id": (str(query.remediation_id) if query.remediation_id is not None else None),
        "status": query.status.value if query.status is not None else None,
    }


def _knowledge_feedback_cursor_filters(
    query: CapacityGovernanceKnowledgeFeedbackQuery,
) -> dict[str, str | None]:
    return {
        "postmortem_id": (str(query.postmortem_id) if query.postmortem_id is not None else None),
        "signal": query.signal.value if query.signal is not None else None,
        "status": query.status.value if query.status is not None else None,
    }


def _knowledge_quality_cursor_filters(
    query: CapacityGovernanceKnowledgeQualitySnapshotQuery,
) -> dict[str, str | None]:
    return {
        "assessment": query.assessment.value if query.assessment is not None else None,
        "postmortem_id": (str(query.postmortem_id) if query.postmortem_id is not None else None),
    }


def _knowledge_quality_trend_cursor_filters(
    query: CapacityGovernanceKnowledgeQualityTrendQuery,
) -> dict[str, str | None]:
    return {
        "assessment": query.assessment.value if query.assessment is not None else None,
        "bucket": query.bucket.value,
        "captured_from": query.captured_from.isoformat(),
        "captured_to": query.captured_to.isoformat(),
    }


def _knowledge_quality_trend_bucket_starts(
    query: CapacityGovernanceKnowledgeQualityTrendQuery,
) -> tuple[datetime, ...]:
    bucket_seconds = (
        3_600 if query.bucket is CapacityGovernanceKnowledgeQualityTrendBucket.HOUR else 86_400
    )
    start_epoch = int(query.captured_from.timestamp())
    bucket = datetime.fromtimestamp(
        start_epoch - (start_epoch % bucket_seconds),
        tz=UTC,
    )
    starts: list[datetime] = []
    while bucket < query.captured_to:
        starts.append(bucket)
        bucket += timedelta(seconds=bucket_seconds)
    return tuple(reversed(starts))


def _knowledge_recovery_cursor_filters(
    query: CapacityGovernanceKnowledgeRecoveryQuery,
) -> dict[str, str | None]:
    return {
        "postmortem_id": (str(query.postmortem_id) if query.postmortem_id is not None else None),
        "status": query.status.value if query.status is not None else None,
    }


def _encode_cursor(
    *,
    kind: str,
    updated_at: datetime,
    item_id: UUID,
    filters: dict[str, str | None],
    scope_hash: str,
) -> str:
    payload = json.dumps(
        {
            "filters": filters,
            "id": str(item_id),
            "kind": kind,
            "scope": scope_hash,
            "updated_at": updated_at.isoformat(),
            "v": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    value: str,
    *,
    kind: str,
    filters: dict[str, str | None],
    scope_hash: str,
) -> tuple[datetime, UUID]:
    if not value or len(value) > 500 or "=" in value:
        raise CapacityGovernanceCursorError("invalid capacity governance cursor")
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
        if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
            raise ValueError
        payload = json.loads(decoded.decode())
        if set(payload) != {"filters", "id", "kind", "scope", "updated_at", "v"}:
            raise ValueError
        if (
            payload["v"] != 1
            or payload["kind"] != kind
            or payload["filters"] != filters
            or payload["scope"] != scope_hash
        ):
            raise ValueError
        updated_at = datetime.fromisoformat(payload["updated_at"])
        item_id = UUID(payload["id"])
        if updated_at.tzinfo is None:
            raise ValueError
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise CapacityGovernanceCursorError("invalid capacity governance cursor") from exc
    return updated_at, item_id
