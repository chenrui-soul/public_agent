from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_governance import (
    ReflectionCapacityChangeRequestRecord,
    ReflectionCapacityChangeStatus,
    ReflectionCapacityPolicyRecord,
)

CAPACITY_GOVERNANCE_READ = "operations.capacity:read"
CAPACITY_GOVERNANCE_REQUEST = "operations.capacity:request"
CAPACITY_GOVERNANCE_APPROVE = "operations.capacity:approve"
CAPACITY_GOVERNANCE_PUBLISH = "operations.capacity:publish"
CAPACITY_GOVERNANCE_REVIEW = "operations.capacity:review"
CAPACITY_GOVERNANCE_ROLLBACK = "operations.capacity:rollback"
CAPACITY_ALERTS_READ = "operations.capacity_alerts:read"
CAPACITY_ALERTS_MANAGE = "operations.capacity_alerts:manage"
CAPACITY_AUDIT_READ = "operations.capacity_audit:read"
CAPACITY_INCIDENTS_READ = "operations.capacity_incidents:read"
CAPACITY_INCIDENTS_MANAGE = "operations.capacity_incidents:manage"
CAPACITY_REMEDIATIONS_READ = "operations.capacity_remediations:read"
CAPACITY_REMEDIATIONS_REQUEST = "operations.capacity_remediations:request"
CAPACITY_REMEDIATIONS_APPROVE = "operations.capacity_remediations:approve"
CAPACITY_REMEDIATIONS_EXECUTE = "operations.capacity_remediations:execute"
CAPACITY_REMEDIATIONS_VERIFY = "operations.capacity_remediations:verify"
CAPACITY_POSTMORTEMS_READ = "operations.capacity_postmortems:read"
CAPACITY_POSTMORTEMS_REQUEST = "operations.capacity_postmortems:request"
CAPACITY_POSTMORTEMS_REVIEW = "operations.capacity_postmortems:review"
CAPACITY_KNOWLEDGE_FEEDBACK_READ = "operations.capacity_knowledge_feedback:read"
CAPACITY_KNOWLEDGE_FEEDBACK_REPORT = "operations.capacity_knowledge_feedback:report"
CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW = "operations.capacity_knowledge_feedback:review"
CAPACITY_KNOWLEDGE_QUALITY_READ = "operations.capacity_knowledge_quality:read"
CAPACITY_KNOWLEDGE_QUALITY_ASSESS = "operations.capacity_knowledge_quality:assess"
CAPACITY_KNOWLEDGE_RECOVERY_READ = "operations.capacity_knowledge_recovery:read"
CAPACITY_KNOWLEDGE_RECOVERY_REQUEST = "operations.capacity_knowledge_recovery:request"
CAPACITY_KNOWLEDGE_RECOVERY_REVIEW = "operations.capacity_knowledge_recovery:review"
CAPACITY_KNOWLEDGE_RECERTIFICATION_READ = "operations.capacity_knowledge_recertification:read"
CAPACITY_KNOWLEDGE_RECERTIFICATION_REQUEST = "operations.capacity_knowledge_recertification:request"
CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW = "operations.capacity_knowledge_recertification:review"
CAPACITY_KNOWLEDGE_RETIREMENT = "operations.capacity_knowledge_retirement:decide"

GOVERNANCE_KNOWLEDGE_NAMESPACE = "operations.governance.postmortems"
GOVERNANCE_KNOWLEDGE_DOMAIN = "operations-governance"
GOVERNANCE_KNOWLEDGE_ACCESS_TAG = "operations.governance:advisory"
GOVERNANCE_KNOWLEDGE_QUARANTINE_RETENTION = timedelta(hours=24)

CAPACITY_GOVERNANCE_PERMISSIONS = frozenset(
    {
        CAPACITY_GOVERNANCE_READ,
        CAPACITY_GOVERNANCE_REQUEST,
        CAPACITY_GOVERNANCE_APPROVE,
        CAPACITY_GOVERNANCE_PUBLISH,
        CAPACITY_GOVERNANCE_REVIEW,
        CAPACITY_GOVERNANCE_ROLLBACK,
        CAPACITY_ALERTS_READ,
        CAPACITY_ALERTS_MANAGE,
        CAPACITY_AUDIT_READ,
        CAPACITY_INCIDENTS_READ,
        CAPACITY_INCIDENTS_MANAGE,
        CAPACITY_REMEDIATIONS_READ,
        CAPACITY_REMEDIATIONS_REQUEST,
        CAPACITY_REMEDIATIONS_APPROVE,
        CAPACITY_REMEDIATIONS_EXECUTE,
        CAPACITY_REMEDIATIONS_VERIFY,
        CAPACITY_POSTMORTEMS_READ,
        CAPACITY_POSTMORTEMS_REQUEST,
        CAPACITY_POSTMORTEMS_REVIEW,
        CAPACITY_KNOWLEDGE_FEEDBACK_READ,
        CAPACITY_KNOWLEDGE_FEEDBACK_REPORT,
        CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
        CAPACITY_KNOWLEDGE_QUALITY_READ,
        CAPACITY_KNOWLEDGE_QUALITY_ASSESS,
        CAPACITY_KNOWLEDGE_RECOVERY_READ,
        CAPACITY_KNOWLEDGE_RECOVERY_REQUEST,
        CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
        CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
        CAPACITY_KNOWLEDGE_RECERTIFICATION_REQUEST,
        CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW,
        CAPACITY_KNOWLEDGE_RETIREMENT,
    }
)


class CapacityGovernanceRole(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    permissions: tuple[str, ...]


CAPACITY_GOVERNANCE_ROLES = (
    CapacityGovernanceRole(
        name="viewer",
        permissions=(CAPACITY_GOVERNANCE_READ, CAPACITY_ALERTS_READ),
    ),
    CapacityGovernanceRole(
        name="proposer",
        permissions=(CAPACITY_GOVERNANCE_READ, CAPACITY_GOVERNANCE_REQUEST),
    ),
    CapacityGovernanceRole(
        name="approver",
        permissions=(CAPACITY_GOVERNANCE_READ, CAPACITY_GOVERNANCE_APPROVE),
    ),
    CapacityGovernanceRole(
        name="publisher",
        permissions=(CAPACITY_GOVERNANCE_READ, CAPACITY_GOVERNANCE_PUBLISH),
    ),
    CapacityGovernanceRole(
        name="reviewer",
        permissions=(CAPACITY_GOVERNANCE_READ, CAPACITY_GOVERNANCE_REVIEW),
    ),
    CapacityGovernanceRole(
        name="rollback_operator",
        permissions=(CAPACITY_GOVERNANCE_READ, CAPACITY_GOVERNANCE_ROLLBACK),
    ),
    CapacityGovernanceRole(
        name="alert_operator",
        permissions=(CAPACITY_ALERTS_READ, CAPACITY_ALERTS_MANAGE),
    ),
    CapacityGovernanceRole(
        name="auditor",
        permissions=(CAPACITY_AUDIT_READ,),
    ),
    CapacityGovernanceRole(
        name="incident_viewer",
        permissions=(CAPACITY_INCIDENTS_READ,),
    ),
    CapacityGovernanceRole(
        name="incident_operator",
        permissions=(CAPACITY_INCIDENTS_READ, CAPACITY_INCIDENTS_MANAGE),
    ),
    CapacityGovernanceRole(
        name="remediation_viewer",
        permissions=(CAPACITY_REMEDIATIONS_READ,),
    ),
    CapacityGovernanceRole(
        name="remediation_requester",
        permissions=(CAPACITY_REMEDIATIONS_READ, CAPACITY_REMEDIATIONS_REQUEST),
    ),
    CapacityGovernanceRole(
        name="remediation_approver",
        permissions=(CAPACITY_REMEDIATIONS_READ, CAPACITY_REMEDIATIONS_APPROVE),
    ),
    CapacityGovernanceRole(
        name="remediation_executor",
        permissions=(CAPACITY_REMEDIATIONS_READ, CAPACITY_REMEDIATIONS_EXECUTE),
    ),
    CapacityGovernanceRole(
        name="remediation_verifier",
        permissions=(CAPACITY_REMEDIATIONS_READ, CAPACITY_REMEDIATIONS_VERIFY),
    ),
    CapacityGovernanceRole(
        name="postmortem_viewer",
        permissions=(CAPACITY_POSTMORTEMS_READ,),
    ),
    CapacityGovernanceRole(
        name="postmortem_requester",
        permissions=(CAPACITY_POSTMORTEMS_READ, CAPACITY_POSTMORTEMS_REQUEST),
    ),
    CapacityGovernanceRole(
        name="postmortem_reviewer",
        permissions=(CAPACITY_POSTMORTEMS_READ, CAPACITY_POSTMORTEMS_REVIEW),
    ),
    CapacityGovernanceRole(
        name="knowledge_feedback_viewer",
        permissions=(CAPACITY_KNOWLEDGE_FEEDBACK_READ,),
    ),
    CapacityGovernanceRole(
        name="knowledge_feedback_reporter",
        permissions=(
            CAPACITY_KNOWLEDGE_FEEDBACK_READ,
            CAPACITY_KNOWLEDGE_FEEDBACK_REPORT,
        ),
    ),
    CapacityGovernanceRole(
        name="knowledge_feedback_reviewer",
        permissions=(
            CAPACITY_KNOWLEDGE_FEEDBACK_READ,
            CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
        ),
    ),
    CapacityGovernanceRole(
        name="knowledge_quality_viewer",
        permissions=(CAPACITY_KNOWLEDGE_QUALITY_READ,),
    ),
    CapacityGovernanceRole(
        name="knowledge_quality_assessor",
        permissions=(CAPACITY_KNOWLEDGE_QUALITY_READ, CAPACITY_KNOWLEDGE_QUALITY_ASSESS),
    ),
    CapacityGovernanceRole(
        name="knowledge_recovery_viewer",
        permissions=(CAPACITY_KNOWLEDGE_RECOVERY_READ,),
    ),
    CapacityGovernanceRole(
        name="knowledge_recovery_requester",
        permissions=(CAPACITY_KNOWLEDGE_RECOVERY_READ, CAPACITY_KNOWLEDGE_RECOVERY_REQUEST),
    ),
    CapacityGovernanceRole(
        name="knowledge_recovery_reviewer",
        permissions=(CAPACITY_KNOWLEDGE_RECOVERY_READ, CAPACITY_KNOWLEDGE_RECOVERY_REVIEW),
    ),
    CapacityGovernanceRole(
        name="knowledge_recertification_viewer",
        permissions=(CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,),
    ),
    CapacityGovernanceRole(
        name="knowledge_recertification_requester",
        permissions=(
            CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
            CAPACITY_KNOWLEDGE_RECERTIFICATION_REQUEST,
        ),
    ),
    CapacityGovernanceRole(
        name="knowledge_recertification_reviewer",
        permissions=(
            CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
            CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW,
        ),
    ),
    CapacityGovernanceRole(
        name="knowledge_retirement_operator",
        permissions=(CAPACITY_KNOWLEDGE_RECERTIFICATION_READ, CAPACITY_KNOWLEDGE_RETIREMENT),
    ),
)


class CapacityGovernanceAlertType(StrEnum):
    POLICY_DRIFT = "policy_drift"


class CapacityGovernanceAlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class CapacityGovernanceAlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class CapacityGovernanceAuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    CONFLICT = "conflict"


class CapacityGovernanceIncidentSignal(StrEnum):
    AUDIT_FAILURE_SPIKE = "audit_failure_spike"
    ALERT_SLA_BREACHED = "alert_sla_breached"
    ALERT_REOPEN_REPEAT = "alert_reopen_repeat"
    DRILL_CHECK_FAILED = "drill_check_failed"
    KNOWLEDGE_UNSAFE_PERSISTENT = "knowledge_unsafe_persistent"
    KNOWLEDGE_DEGRADED_REPEAT = "knowledge_degraded_repeat"
    KNOWLEDGE_REQUARANTINED = "knowledge_requarantined"


class CapacityGovernanceIncidentSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class CapacityGovernanceIncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class CapacityGovernanceRemediationPlaybook(StrEnum):
    AUDIT_FAILURE_CONTAINMENT = "audit_failure_containment"
    ALERT_SLA_RECOVERY = "alert_sla_recovery"
    ALERT_REOPEN_STABILIZATION = "alert_reopen_stabilization"
    DRILL_CONTROL_REPAIR = "drill_control_repair"
    KNOWLEDGE_SAFETY_CONTAINMENT = "knowledge_safety_containment"
    KNOWLEDGE_QUALITY_REVIEW = "knowledge_quality_review"
    KNOWLEDGE_RECURRENCE_REVIEW = "knowledge_recurrence_review"


INCIDENT_REMEDIATION_PLAYBOOKS = {
    CapacityGovernanceIncidentSignal.AUDIT_FAILURE_SPIKE: (
        CapacityGovernanceRemediationPlaybook.AUDIT_FAILURE_CONTAINMENT
    ),
    CapacityGovernanceIncidentSignal.ALERT_SLA_BREACHED: (
        CapacityGovernanceRemediationPlaybook.ALERT_SLA_RECOVERY
    ),
    CapacityGovernanceIncidentSignal.ALERT_REOPEN_REPEAT: (
        CapacityGovernanceRemediationPlaybook.ALERT_REOPEN_STABILIZATION
    ),
    CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED: (
        CapacityGovernanceRemediationPlaybook.DRILL_CONTROL_REPAIR
    ),
    CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT: (
        CapacityGovernanceRemediationPlaybook.KNOWLEDGE_SAFETY_CONTAINMENT
    ),
    CapacityGovernanceIncidentSignal.KNOWLEDGE_DEGRADED_REPEAT: (
        CapacityGovernanceRemediationPlaybook.KNOWLEDGE_QUALITY_REVIEW
    ),
    CapacityGovernanceIncidentSignal.KNOWLEDGE_REQUARANTINED: (
        CapacityGovernanceRemediationPlaybook.KNOWLEDGE_RECURRENCE_REVIEW
    ),
}


class CapacityGovernanceRemediationStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    VERIFICATION_PENDING = "verification_pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    FAILED = "failed"


class CapacityGovernanceRemediationExecutionResult(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class CapacityGovernanceRemediationEvidence(StrEnum):
    CONTAINMENT_APPLIED = "containment_applied"
    CONFIGURATION_REVIEWED = "configuration_reviewed"
    MONITORING_EXTENDED = "monitoring_extended"
    SCHEMA_CONTROL_RESTORED = "schema_control_restored"
    KNOWLEDGE_QUARANTINE_REVIEWED = "knowledge_quarantine_reviewed"
    QUALITY_EVIDENCE_REVIEWED = "quality_evidence_reviewed"
    RESTORATION_HISTORY_REVIEWED = "restoration_history_reviewed"


REMEDIATION_PLAYBOOK_EVIDENCE = {
    CapacityGovernanceRemediationPlaybook.AUDIT_FAILURE_CONTAINMENT: (
        CapacityGovernanceRemediationEvidence.CONTAINMENT_APPLIED
    ),
    CapacityGovernanceRemediationPlaybook.ALERT_SLA_RECOVERY: (
        CapacityGovernanceRemediationEvidence.MONITORING_EXTENDED
    ),
    CapacityGovernanceRemediationPlaybook.ALERT_REOPEN_STABILIZATION: (
        CapacityGovernanceRemediationEvidence.CONFIGURATION_REVIEWED
    ),
    CapacityGovernanceRemediationPlaybook.DRILL_CONTROL_REPAIR: (
        CapacityGovernanceRemediationEvidence.SCHEMA_CONTROL_RESTORED
    ),
    CapacityGovernanceRemediationPlaybook.KNOWLEDGE_SAFETY_CONTAINMENT: (
        CapacityGovernanceRemediationEvidence.KNOWLEDGE_QUARANTINE_REVIEWED
    ),
    CapacityGovernanceRemediationPlaybook.KNOWLEDGE_QUALITY_REVIEW: (
        CapacityGovernanceRemediationEvidence.QUALITY_EVIDENCE_REVIEWED
    ),
    CapacityGovernanceRemediationPlaybook.KNOWLEDGE_RECURRENCE_REVIEW: (
        CapacityGovernanceRemediationEvidence.RESTORATION_HISTORY_REVIEWED
    ),
}


class CapacityGovernancePostmortemRootCause(StrEnum):
    AUTHORIZATION_CONTROL_GAP = "authorization_control_gap"
    POLICY_DRIFT = "policy_drift"
    OPERATIONAL_PROCESS_GAP = "operational_process_gap"
    OBSERVABILITY_GAP = "observability_gap"
    SCHEMA_CONTROL_GAP = "schema_control_gap"


class CapacityGovernancePostmortemImpact(StrEnum):
    GOVERNANCE_DELAY = "governance_delay"
    CONTROL_DEGRADATION = "control_degradation"
    REPEATED_ALERTING = "repeated_alerting"
    ACCESS_DISRUPTION = "access_disruption"
    NO_EXTERNAL_IMPACT = "no_external_impact"


class CapacityGovernancePostmortemPrevention(StrEnum):
    ACCESS_REVIEW = "access_review"
    POLICY_VALIDATION = "policy_validation"
    PROCESS_HARDENING = "process_hardening"
    MONITORING_EXPANSION = "monitoring_expansion"
    SCHEMA_VERIFICATION = "schema_verification"


class CapacityGovernancePostmortemStatus(StrEnum):
    AWAITING_REVIEW = "awaiting_review"
    PUBLISHED = "published"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"


class CapacityGovernanceKnowledgeFeedbackSignal(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"
    SAFETY_CONCERN = "safety_concern"


class CapacityGovernanceKnowledgeFeedbackReason(StrEnum):
    RELEVANCE = "relevance"
    ACCURACY = "accuracy"
    STALENESS = "staleness"
    UNSAFE_CONTENT = "unsafe_content"


class CapacityGovernanceKnowledgeFeedbackStatus(StrEnum):
    AWAITING_REVIEW = "awaiting_review"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"


class CapacityGovernanceKnowledgeQualityAssessment(StrEnum):
    INSUFFICIENT = "insufficient"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNSAFE = "unsafe"


class CapacityGovernanceKnowledgeQualityTrendBucket(StrEnum):
    HOUR = "hour"
    DAY = "day"


class CapacityGovernanceKnowledgeRecoveryReason(StrEnum):
    FALSE_POSITIVE = "false_positive"


class CapacityGovernanceKnowledgeRecoveryStatus(StrEnum):
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class CapacityGovernanceKnowledgeLifecycleStatus(StrEnum):
    CURRENT = "current"
    DUE = "due"
    OVERDUE = "overdue"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class CapacityGovernanceKnowledgeRecertificationDecision(StrEnum):
    CERTIFY = "certify"
    REJECT = "reject"
    RETIRE = "retire"


class CapacityGovernanceKnowledgeRecertificationReason(StrEnum):
    VALIDATION_PASSED = "validation_passed"
    STALE_EVIDENCE = "stale_evidence"
    QUALITY_RISK = "quality_risk"
    REPLACED = "replaced"
    SCOPE_ENDED = "scope_ended"


class CapacityGovernanceKnowledgeRecertificationPolicy(BaseModel):
    """Versioned, bounded policy used to derive governance knowledge due dates."""

    model_config = ConfigDict(frozen=True)

    policy_version: int = Field(ge=1)
    window_seconds: int = Field(default=2_592_000, ge=86_400, le=31_536_000)
    due_notice_seconds: int = Field(default=604_800, ge=3_600, le=31_536_000)

    @model_validator(mode="after")
    def require_ordered_window(self) -> CapacityGovernanceKnowledgeRecertificationPolicy:
        if self.due_notice_seconds >= self.window_seconds:
            raise ValueError("knowledge recertification due notice must be shorter than window")
        return self


class CapacityGovernanceKnowledgeRecertificationInput(BaseModel):
    """Safe decision input; all source facts are re-checked by the persistence layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    postmortem_id: UUID
    expected_postmortem_version: int = Field(ge=1)
    knowledge_version: str = Field(min_length=1, max_length=100)
    content_fingerprint: str = Field(min_length=64, max_length=64)
    quality_snapshot_id: UUID
    quality_evidence_fingerprint: str = Field(min_length=64, max_length=64)
    decision: CapacityGovernanceKnowledgeRecertificationDecision
    reason: CapacityGovernanceKnowledgeRecertificationReason

    @field_validator("knowledge_version")
    @classmethod
    def normalize_knowledge_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge version must not be blank")
        return normalized

    @field_validator("content_fingerprint", "quality_evidence_fingerprint")
    @classmethod
    def require_sha256_fingerprint(cls, value: str) -> str:
        normalized = value.strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ValueError("knowledge fingerprints must be lowercase SHA-256 values")
        return normalized

    @model_validator(mode="after")
    def require_restricted_reason(self) -> CapacityGovernanceKnowledgeRecertificationInput:
        allowed: dict[
            CapacityGovernanceKnowledgeRecertificationDecision,
            frozenset[CapacityGovernanceKnowledgeRecertificationReason],
        ] = {
            CapacityGovernanceKnowledgeRecertificationDecision.CERTIFY: frozenset(
                {CapacityGovernanceKnowledgeRecertificationReason.VALIDATION_PASSED}
            ),
            CapacityGovernanceKnowledgeRecertificationDecision.REJECT: frozenset(
                {
                    CapacityGovernanceKnowledgeRecertificationReason.STALE_EVIDENCE,
                    CapacityGovernanceKnowledgeRecertificationReason.QUALITY_RISK,
                }
            ),
            CapacityGovernanceKnowledgeRecertificationDecision.RETIRE: frozenset(
                {
                    CapacityGovernanceKnowledgeRecertificationReason.STALE_EVIDENCE,
                    CapacityGovernanceKnowledgeRecertificationReason.QUALITY_RISK,
                    CapacityGovernanceKnowledgeRecertificationReason.REPLACED,
                    CapacityGovernanceKnowledgeRecertificationReason.SCOPE_ENDED,
                }
            ),
        }
        if self.reason not in allowed[self.decision]:
            raise ValueError("recertification decision and reason are incompatible")
        return self


class CapacityGovernanceKnowledgeLifecycleRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    postmortem_id: UUID
    handler_version: str = Field(min_length=1, max_length=64)
    postmortem_version: int = Field(ge=1)
    knowledge_version: str = Field(min_length=1, max_length=100)
    content_fingerprint: str = Field(min_length=64, max_length=64)
    status: CapacityGovernanceKnowledgeLifecycleStatus
    anchor_at: datetime | None = None
    due_at: datetime | None = None
    last_certified_at: datetime | None = None
    quality_snapshot_id: UUID | None = None
    quality_evidence_fingerprint: str | None = None
    generated_at: datetime


class CapacityGovernanceKnowledgeLifecycleQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CapacityGovernanceKnowledgeLifecycleStatus | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


def project_governance_knowledge_lifecycle(
    *,
    postmortem_id: UUID,
    handler_version: str,
    postmortem_version: int,
    knowledge_version: str,
    content_fingerprint: str,
    postmortem_status: CapacityGovernancePostmortemStatus,
    published_at: datetime | None,
    last_restored_at: datetime | None,
    last_certified_at: datetime | None,
    policy: CapacityGovernanceKnowledgeRecertificationPolicy,
    now: datetime,
    retired: bool = False,
    quality_snapshot_id: UUID | None = None,
    quality_evidence_fingerprint: str | None = None,
) -> CapacityGovernanceKnowledgeLifecycleRecord:
    """Derive an operational status without mutating any governance fact."""

    for name, value in (
        ("now", now),
        ("published_at", published_at),
        ("last_restored_at", last_restored_at),
        ("last_certified_at", last_certified_at),
    ):
        if value is not None and value.tzinfo is None:
            raise ValueError(f"knowledge lifecycle {name} must be timezone-aware")
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError(f"knowledge lifecycle {name} must use UTC")
        if value is not None and value > now:
            raise ValueError(f"knowledge lifecycle {name} must not be in the future")
    if now.utcoffset() != timedelta(0):
        raise ValueError("knowledge lifecycle now must use UTC")
    if postmortem_version < 1:
        raise ValueError("knowledge lifecycle postmortem version must be positive")
    if not handler_version.strip() or not knowledge_version.strip():
        raise ValueError("knowledge lifecycle versions must not be blank")
    if re.fullmatch(r"[0-9a-f]{64}", content_fingerprint.lower()) is None:
        raise ValueError("knowledge lifecycle content fingerprint must be SHA-256")
    if quality_evidence_fingerprint is not None and re.fullmatch(
        r"[0-9a-f]{64}", quality_evidence_fingerprint.lower()
    ) is None:
        raise ValueError("knowledge lifecycle evidence fingerprint must be SHA-256")
    anchors = tuple(
        value
        for value in (published_at, last_restored_at, last_certified_at)
        if value is not None
    )
    anchor_at = max(anchors) if anchors else None
    if retired:
        status = CapacityGovernanceKnowledgeLifecycleStatus.RETIRED
        due_at = None
    elif postmortem_status is CapacityGovernancePostmortemStatus.QUARANTINED:
        status = CapacityGovernanceKnowledgeLifecycleStatus.QUARANTINED
        due_at = None
    elif postmortem_status is not CapacityGovernancePostmortemStatus.PUBLISHED:
        raise ValueError("knowledge lifecycle requires published or quarantined knowledge")
    else:
        if anchor_at is None:
            raise ValueError("published knowledge requires a lifecycle anchor")
        due_at = anchor_at + timedelta(seconds=policy.window_seconds)
        notice_at = due_at - timedelta(seconds=policy.due_notice_seconds)
        if now >= due_at:
            status = CapacityGovernanceKnowledgeLifecycleStatus.OVERDUE
        elif now >= notice_at:
            status = CapacityGovernanceKnowledgeLifecycleStatus.DUE
        else:
            status = CapacityGovernanceKnowledgeLifecycleStatus.CURRENT
    return CapacityGovernanceKnowledgeLifecycleRecord(
        postmortem_id=postmortem_id,
        handler_version=handler_version.strip(),
        postmortem_version=postmortem_version,
        knowledge_version=knowledge_version.strip(),
        content_fingerprint=content_fingerprint.lower(),
        status=status,
        anchor_at=anchor_at,
        due_at=due_at,
        last_certified_at=last_certified_at,
        quality_snapshot_id=quality_snapshot_id,
        quality_evidence_fingerprint=(
            quality_evidence_fingerprint.lower()
            if quality_evidence_fingerprint is not None
            else None
        ),
        generated_at=now.astimezone(UTC),
    )


class CapacityGovernanceKnowledgeFeedbackInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal: CapacityGovernanceKnowledgeFeedbackSignal
    reason: CapacityGovernanceKnowledgeFeedbackReason

    @model_validator(mode="after")
    def require_consistent_reason(self) -> CapacityGovernanceKnowledgeFeedbackInput:
        if (
            self.signal is CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN
        ) != (self.reason is CapacityGovernanceKnowledgeFeedbackReason.UNSAFE_CONTENT):
            raise ValueError("knowledge feedback safety signal and reason must match")
        return self


_UNSAFE_POSTMORTEM_SUMMARY = re.compile(
    r"authorization\s*:|bearer\s+[a-z0-9._-]+|"
    r"(?:postgres(?:ql)?|mysql|redis)://|api[_-]?key\s*[:=]|"
    r"secret\s*[:=]|password\s*[:=]|-----begin [^-]+ private key-----|```|"
    r"(?:^|\s)(?:kubectl|docker(?:\s+compose)?|sudo|curl|wget|psql|bash|"
    r"powershell|invoke-[a-z-]+|rm\s+-|drop\s+(?:table|database)|"
    r"delete\s+from|alter\s+table)\b",
    re.IGNORECASE,
)


class CapacityGovernancePostmortemInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    root_cause: CapacityGovernancePostmortemRootCause
    impact: CapacityGovernancePostmortemImpact
    prevention: CapacityGovernancePostmortemPrevention
    summary: str = Field(min_length=10, max_length=1_000)

    @field_validator("summary")
    @classmethod
    def normalize_safe_summary(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 10 or _UNSAFE_POSTMORTEM_SUMMARY.search(normalized):
            raise ValueError("postmortem safe summary contains unsafe content")
        return normalized


_POSTMORTEM_CLASSIFICATIONS = {
    CapacityGovernanceRemediationPlaybook.AUDIT_FAILURE_CONTAINMENT: (
        frozenset(
            {
                CapacityGovernancePostmortemRootCause.AUTHORIZATION_CONTROL_GAP,
                CapacityGovernancePostmortemRootCause.OPERATIONAL_PROCESS_GAP,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemImpact.ACCESS_DISRUPTION,
                CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
                CapacityGovernancePostmortemImpact.NO_EXTERNAL_IMPACT,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemPrevention.ACCESS_REVIEW,
                CapacityGovernancePostmortemPrevention.PROCESS_HARDENING,
            }
        ),
    ),
    CapacityGovernanceRemediationPlaybook.ALERT_SLA_RECOVERY: (
        frozenset(
            {
                CapacityGovernancePostmortemRootCause.OPERATIONAL_PROCESS_GAP,
                CapacityGovernancePostmortemRootCause.OBSERVABILITY_GAP,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemImpact.GOVERNANCE_DELAY,
                CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
                CapacityGovernancePostmortemImpact.NO_EXTERNAL_IMPACT,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemPrevention.PROCESS_HARDENING,
                CapacityGovernancePostmortemPrevention.MONITORING_EXPANSION,
            }
        ),
    ),
    CapacityGovernanceRemediationPlaybook.ALERT_REOPEN_STABILIZATION: (
        frozenset(
            {
                CapacityGovernancePostmortemRootCause.POLICY_DRIFT,
                CapacityGovernancePostmortemRootCause.OBSERVABILITY_GAP,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemImpact.REPEATED_ALERTING,
                CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
                CapacityGovernancePostmortemImpact.NO_EXTERNAL_IMPACT,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemPrevention.POLICY_VALIDATION,
                CapacityGovernancePostmortemPrevention.MONITORING_EXPANSION,
            }
        ),
    ),
    CapacityGovernanceRemediationPlaybook.DRILL_CONTROL_REPAIR: (
        frozenset(
            {
                CapacityGovernancePostmortemRootCause.SCHEMA_CONTROL_GAP,
                CapacityGovernancePostmortemRootCause.OPERATIONAL_PROCESS_GAP,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
                CapacityGovernancePostmortemImpact.NO_EXTERNAL_IMPACT,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemPrevention.SCHEMA_VERIFICATION,
                CapacityGovernancePostmortemPrevention.PROCESS_HARDENING,
            }
        ),
    ),
    CapacityGovernanceRemediationPlaybook.KNOWLEDGE_SAFETY_CONTAINMENT: (
        frozenset(
            {
                CapacityGovernancePostmortemRootCause.OPERATIONAL_PROCESS_GAP,
                CapacityGovernancePostmortemRootCause.OBSERVABILITY_GAP,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
                CapacityGovernancePostmortemImpact.ACCESS_DISRUPTION,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemPrevention.PROCESS_HARDENING,
                CapacityGovernancePostmortemPrevention.ACCESS_REVIEW,
            }
        ),
    ),
    CapacityGovernanceRemediationPlaybook.KNOWLEDGE_QUALITY_REVIEW: (
        frozenset(
            {
                CapacityGovernancePostmortemRootCause.OBSERVABILITY_GAP,
                CapacityGovernancePostmortemRootCause.OPERATIONAL_PROCESS_GAP,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
                CapacityGovernancePostmortemImpact.GOVERNANCE_DELAY,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemPrevention.MONITORING_EXPANSION,
                CapacityGovernancePostmortemPrevention.PROCESS_HARDENING,
            }
        ),
    ),
    CapacityGovernanceRemediationPlaybook.KNOWLEDGE_RECURRENCE_REVIEW: (
        frozenset(
            {
                CapacityGovernancePostmortemRootCause.OPERATIONAL_PROCESS_GAP,
                CapacityGovernancePostmortemRootCause.OBSERVABILITY_GAP,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemImpact.REPEATED_ALERTING,
                CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
            }
        ),
        frozenset(
            {
                CapacityGovernancePostmortemPrevention.PROCESS_HARDENING,
                CapacityGovernancePostmortemPrevention.MONITORING_EXPANSION,
            }
        ),
    ),
}


CAPACITY_INCIDENT_RULE_VERSIONS = {
    CapacityGovernanceIncidentSignal.AUDIT_FAILURE_SPIKE: "audit-failure-spike/v1",
    CapacityGovernanceIncidentSignal.ALERT_SLA_BREACHED: "alert-sla-breached/v1",
    CapacityGovernanceIncidentSignal.ALERT_REOPEN_REPEAT: "alert-reopen-repeat/v1",
    CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED: "drill-check-failed/v1",
    CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT: (
        "knowledge-unsafe-persistent/v1"
    ),
    CapacityGovernanceIncidentSignal.KNOWLEDGE_DEGRADED_REPEAT: (
        "knowledge-degraded-repeat/v1"
    ),
    CapacityGovernanceIncidentSignal.KNOWLEDGE_REQUARANTINED: (
        "knowledge-requarantined/v1"
    ),
}


class CapacityGovernanceAlertSLAState(StrEnum):
    WITHIN_SLA = "within_sla"
    DUE = "due"
    BREACHED = "breached"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class CapacityGovernanceAlertSLA(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: CapacityGovernanceAlertSLAState
    age_seconds: int = Field(ge=0)
    response_due_at: datetime
    escalation_due_at: datetime


class CapacityGovernanceAlertRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    handler_version: str
    alert_type: CapacityGovernanceAlertType
    severity: CapacityGovernanceAlertSeverity
    status: CapacityGovernanceAlertStatus
    version: int = Field(ge=1)
    expected_policy_id: UUID | None = None
    expected_policy_version: int | None = Field(default=None, ge=1)
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    observed_fingerprint: str = Field(min_length=64, max_length=64)
    first_seen_at: datetime
    last_seen_at: datetime
    last_observation_at: datetime
    sample_count: int = Field(ge=1)
    details: dict[str, str | int | float | bool | None]
    acknowledged_by: str | None = None
    acknowledged_principal_id: UUID | None = None
    acknowledged_token_id: UUID | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    reopened_count: int = Field(ge=0)
    sla: CapacityGovernanceAlertSLA
    created_at: datetime
    updated_at: datetime


class CapacityGovernanceAlertQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CapacityGovernanceAlertStatus | None = None
    severity: CapacityGovernanceAlertSeverity | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CapacityGovernanceAlertPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityGovernanceAlertRecord, ...]
    next_cursor: str | None = None


class CapacityChangeRequestQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ReflectionCapacityChangeStatus | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CapacityChangeRequestPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ReflectionCapacityChangeRequestRecord, ...]
    next_cursor: str | None = None


class CapacityGovernanceAuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    handler_version: str
    actor_subject: str | None
    request_id: UUID | None = None
    alert_id: UUID | None = None
    incident_id: UUID | None = None
    postmortem_id: UUID | None = None
    action: str
    outcome: CapacityGovernanceAuditOutcome
    safe_metadata: dict[str, str | int | float | bool | None]
    created_at: datetime


class CapacityGovernanceAuditQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor_subject: str | None = Field(default=None, min_length=1, max_length=200)
    action: str | None = Field(default=None, min_length=1, max_length=100)
    outcome: CapacityGovernanceAuditOutcome | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)

    @field_validator("actor_subject", "action")
    @classmethod
    def normalize_filter_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("capacity audit text filters must not be blank")
        return normalized

    @field_validator("occurred_from", "occurred_to")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("capacity audit times must be timezone-aware")
        return value

    @model_validator(mode="after")
    def require_ordered_window(self) -> CapacityGovernanceAuditQuery:
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_from > self.occurred_to
        ):
            raise ValueError("capacity audit time filters must be ordered")
        return self


class CapacityGovernanceAuditPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityGovernanceAuditRecord, ...]
    next_cursor: str | None = None


class CapacityGovernanceIncidentThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    audit_window_seconds: int = Field(default=300, ge=60, le=86_400)
    audit_warning_count: int = Field(default=5, ge=1, le=100_000)
    audit_critical_count: int = Field(default=10, ge=1, le=100_000)
    audit_maximum_events: int = Field(default=1_000, ge=1, le=100_000)
    reopen_warning_count: int = Field(default=2, ge=1, le=100_000)
    reopen_critical_count: int = Field(default=4, ge=1, le=100_000)
    maximum_alerts: int = Field(default=1_000, ge=1, le=100_000)
    maximum_incidents: int = Field(default=1_000, ge=1, le=100_000)

    @model_validator(mode="after")
    def require_ordered_thresholds(self) -> CapacityGovernanceIncidentThresholds:
        if self.audit_warning_count > self.audit_critical_count:
            raise ValueError("incident audit thresholds must be ordered")
        if self.audit_critical_count > self.audit_maximum_events:
            raise ValueError("incident audit maximum must cover the critical threshold")
        if self.reopen_warning_count > self.reopen_critical_count:
            raise ValueError("incident reopen thresholds must be ordered")
        return self


class CapacityGovernanceKnowledgeQualityRiskThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    window_seconds: int = Field(default=604_800, ge=3_600, le=2_592_000)
    unsafe_warning_count: int = Field(default=2, ge=2, le=100_000)
    unsafe_critical_count: int = Field(default=3, ge=2, le=100_000)
    degraded_warning_count: int = Field(default=2, ge=2, le=100_000)
    degraded_critical_count: int = Field(default=4, ge=2, le=100_000)
    maximum_snapshots: int = Field(default=1_000, ge=2, le=100_000)

    @model_validator(mode="after")
    def require_ordered_thresholds(
        self,
    ) -> CapacityGovernanceKnowledgeQualityRiskThresholds:
        if self.unsafe_warning_count > self.unsafe_critical_count:
            raise ValueError("knowledge quality unsafe thresholds must be ordered")
        if self.degraded_warning_count > self.degraded_critical_count:
            raise ValueError("knowledge quality degraded thresholds must be ordered")
        if max(self.unsafe_critical_count, self.degraded_critical_count) > (
            self.maximum_snapshots
        ):
            raise ValueError("knowledge quality maximum snapshots must cover critical")
        return self


class CapacityGovernanceIncidentCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal: CapacityGovernanceIncidentSignal
    rule_version: str = Field(min_length=1, max_length=64)
    severity: CapacityGovernanceIncidentSeverity
    fingerprint: str = Field(min_length=64, max_length=64)
    evidence_fingerprint: str = Field(min_length=64, max_length=64)
    source_id: UUID | None = None
    evidence_at: datetime
    details: dict[str, str | int | float | bool | None]


class CapacityGovernanceIncidentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    handler_version: str
    signal: CapacityGovernanceIncidentSignal
    rule_version: str
    severity: CapacityGovernanceIncidentSeverity
    status: CapacityGovernanceIncidentStatus
    version: int = Field(ge=1)
    source_id: UUID | None = None
    fingerprint: str = Field(min_length=64, max_length=64)
    evidence_fingerprint: str = Field(min_length=64, max_length=64)
    first_seen_at: datetime
    last_seen_at: datetime
    last_evidence_at: datetime
    occurrence_count: int = Field(ge=1)
    reopened_count: int = Field(ge=0)
    details: dict[str, str | int | float | bool | None]
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CapacityGovernanceIncidentQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal: CapacityGovernanceIncidentSignal | None = None
    severity: CapacityGovernanceIncidentSeverity | None = None
    status: CapacityGovernanceIncidentStatus | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CapacityGovernanceIncidentPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityGovernanceIncidentRecord, ...]
    next_cursor: str | None = None


class CapacityGovernanceRemediationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    incident_id: UUID
    handler_version: str
    incident_cycle: int = Field(ge=0)
    playbook: CapacityGovernanceRemediationPlaybook
    status: CapacityGovernanceRemediationStatus
    version: int = Field(ge=1)
    requested_by: str
    requested_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    executed_by: str | None = None
    executed_at: datetime | None = None
    execution_result: CapacityGovernanceRemediationExecutionResult | None = None
    execution_evidence: CapacityGovernanceRemediationEvidence | None = None
    incident_version_at_execution: int | None = Field(default=None, ge=1)
    verified_by: str | None = None
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CapacityGovernanceRemediationQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CapacityGovernanceRemediationStatus | None = None
    incident_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CapacityGovernanceRemediationPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityGovernanceRemediationRecord, ...]
    next_cursor: str | None = None


class CapacityGovernancePostmortemRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    incident_id: UUID
    remediation_id: UUID
    handler_version: str
    incident_cycle: int = Field(ge=0)
    incident_version: int = Field(ge=1)
    remediation_version: int = Field(ge=1)
    status: CapacityGovernancePostmortemStatus
    version: int = Field(ge=1)
    root_cause: CapacityGovernancePostmortemRootCause
    impact: CapacityGovernancePostmortemImpact
    prevention: CapacityGovernancePostmortemPrevention
    summary: str
    content_fingerprint: str = Field(min_length=64, max_length=64)
    requested_by: str
    requested_at: datetime
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    knowledge_namespace: str | None = None
    knowledge_source_key: str | None = None
    knowledge_version: str | None = None
    published_at: datetime | None = None
    last_quarantined_at: datetime | None = None
    quarantine_feedback_id: UUID | None = None
    restore_count: int = Field(default=0, ge=0)
    last_restored_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CapacityGovernancePostmortemQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CapacityGovernancePostmortemStatus | None = None
    incident_id: UUID | None = None
    remediation_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CapacityGovernancePostmortemPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityGovernancePostmortemRecord, ...]
    next_cursor: str | None = None


class CapacityGovernanceKnowledgeFeedbackRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    postmortem_id: UUID
    handler_version: str
    postmortem_version: int = Field(ge=1)
    knowledge_version: str = Field(min_length=1, max_length=100)
    content_fingerprint: str = Field(min_length=64, max_length=64)
    signal: CapacityGovernanceKnowledgeFeedbackSignal
    reason: CapacityGovernanceKnowledgeFeedbackReason
    status: CapacityGovernanceKnowledgeFeedbackStatus
    version: int = Field(ge=1)
    reported_by: str
    reported_at: datetime
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CapacityGovernanceKnowledgeFeedbackQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CapacityGovernanceKnowledgeFeedbackStatus | None = None
    signal: CapacityGovernanceKnowledgeFeedbackSignal | None = None
    postmortem_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CapacityGovernanceKnowledgeFeedbackPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityGovernanceKnowledgeFeedbackRecord, ...]
    next_cursor: str | None = None


class CapacityGovernanceKnowledgeQualitySnapshotRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    postmortem_id: UUID
    handler_version: str
    postmortem_version: int = Field(ge=1)
    knowledge_version: str = Field(min_length=1, max_length=100)
    content_fingerprint: str = Field(min_length=64, max_length=64)
    evidence_fingerprint: str = Field(min_length=64, max_length=64)
    assessment: CapacityGovernanceKnowledgeQualityAssessment
    total_feedback: int = Field(ge=0)
    awaiting_review_count: int = Field(ge=0)
    confirmed_helpful_count: int = Field(ge=0)
    confirmed_not_helpful_count: int = Field(ge=0)
    confirmed_safety_count: int = Field(ge=0)
    dismissed_count: int = Field(ge=0)
    superseded_count: int = Field(ge=0)
    captured_by: str
    captured_at: datetime
    created_at: datetime


class CapacityGovernanceKnowledgeQualitySnapshotQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    assessment: CapacityGovernanceKnowledgeQualityAssessment | None = None
    postmortem_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CapacityGovernanceKnowledgeQualitySnapshotPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityGovernanceKnowledgeQualitySnapshotRecord, ...]
    next_cursor: str | None = None


class CapacityGovernanceKnowledgeQualityTrendQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: CapacityGovernanceKnowledgeQualityTrendBucket = (
        CapacityGovernanceKnowledgeQualityTrendBucket.HOUR
    )
    captured_from: datetime
    captured_to: datetime
    assessment: CapacityGovernanceKnowledgeQualityAssessment | None = None
    limit: int = Field(default=168, ge=1, le=366)
    cursor: str | None = Field(default=None, max_length=500)

    @field_validator("captured_from", "captured_to")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("knowledge quality trend times must be timezone-aware")
        if value.utcoffset() != timedelta(0):
            raise ValueError("knowledge quality trend times must use UTC")
        return value

    @model_validator(mode="after")
    def require_ordered_window(self) -> CapacityGovernanceKnowledgeQualityTrendQuery:
        if self.captured_from >= self.captured_to:
            raise ValueError("knowledge quality trend window must be ordered")
        return self


class CapacityGovernanceKnowledgeQualityTrendPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket_started_at: datetime
    total_snapshots: int = Field(ge=0)
    insufficient_count: int = Field(ge=0)
    healthy_count: int = Field(ge=0)
    degraded_count: int = Field(ge=0)
    unsafe_count: int = Field(ge=0)
    distinct_postmortems: int = Field(ge=0)

    @field_validator("bucket_started_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("knowledge quality trend bucket must be timezone-aware")
        return value

    @model_validator(mode="after")
    def require_consistent_counts(
        self,
    ) -> CapacityGovernanceKnowledgeQualityTrendPoint:
        assessment_total = (
            self.insufficient_count
            + self.healthy_count
            + self.degraded_count
            + self.unsafe_count
        )
        if assessment_total != self.total_snapshots:
            raise ValueError("knowledge quality trend assessment counts must match total")
        if self.distinct_postmortems > self.total_snapshots:
            raise ValueError("knowledge quality trend postmortem count exceeds total")
        return self


class CapacityGovernanceKnowledgeQualityTrendReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    handler_version: str
    bucket: CapacityGovernanceKnowledgeQualityTrendBucket
    captured_from: datetime
    captured_to: datetime
    assessment: CapacityGovernanceKnowledgeQualityAssessment | None = None
    points: tuple[CapacityGovernanceKnowledgeQualityTrendPoint, ...]
    next_cursor: str | None = None
    generated_at: datetime


class CapacityGovernanceKnowledgeRecoveryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    postmortem_id: UUID
    snapshot_id: UUID
    handler_version: str
    postmortem_version: int = Field(ge=1)
    knowledge_version: str = Field(min_length=1, max_length=100)
    content_fingerprint: str = Field(min_length=64, max_length=64)
    quarantine_feedback_id: UUID
    reason: CapacityGovernanceKnowledgeRecoveryReason
    status: CapacityGovernanceKnowledgeRecoveryStatus
    version: int = Field(ge=1)
    requested_by: str
    requested_at: datetime
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    restored_knowledge_version: str | None = None
    created_at: datetime
    updated_at: datetime


class CapacityGovernanceKnowledgeRecoveryQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CapacityGovernanceKnowledgeRecoveryStatus | None = None
    postmortem_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class CapacityGovernanceKnowledgeRecoveryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CapacityGovernanceKnowledgeRecoveryRecord, ...]
    next_cursor: str | None = None


def expected_remediation_playbook(
    signal: CapacityGovernanceIncidentSignal,
) -> CapacityGovernanceRemediationPlaybook:
    return INCIDENT_REMEDIATION_PLAYBOOKS[signal]


def governance_knowledge_quality_assessment(
    *,
    confirmed_helpful: int,
    confirmed_not_helpful: int,
    confirmed_safety: int,
) -> CapacityGovernanceKnowledgeQualityAssessment:
    if min(confirmed_helpful, confirmed_not_helpful, confirmed_safety) < 0:
        raise ValueError("knowledge quality counts must be non-negative")
    if confirmed_safety:
        return CapacityGovernanceKnowledgeQualityAssessment.UNSAFE
    if confirmed_helpful + confirmed_not_helpful == 0:
        return CapacityGovernanceKnowledgeQualityAssessment.INSUFFICIENT
    if confirmed_not_helpful > confirmed_helpful:
        return CapacityGovernanceKnowledgeQualityAssessment.DEGRADED
    return CapacityGovernanceKnowledgeQualityAssessment.HEALTHY


def expected_remediation_evidence(
    playbook: CapacityGovernanceRemediationPlaybook,
) -> CapacityGovernanceRemediationEvidence:
    return REMEDIATION_PLAYBOOK_EVIDENCE[playbook]


def validate_postmortem_classification(
    playbook: CapacityGovernanceRemediationPlaybook,
    content: CapacityGovernancePostmortemInput,
) -> None:
    root_causes, impacts, preventions = _POSTMORTEM_CLASSIFICATIONS[playbook]
    if (
        content.root_cause not in root_causes
        or content.impact not in impacts
        or content.prevention not in preventions
    ):
        raise ValueError("postmortem classification does not match the remediation playbook")


def postmortem_content_fingerprint(
    *,
    incident_id: UUID,
    incident_cycle: int,
    incident_version: int,
    remediation_id: UUID,
    remediation_version: int,
    content: CapacityGovernancePostmortemInput,
) -> str:
    if incident_cycle < 0 or incident_version < 1 or remediation_version < 1:
        raise ValueError("postmortem source versions must be positive")
    return _canonical_fingerprint(
        {
            "incident_id": str(incident_id),
            "incident_cycle": incident_cycle,
            "incident_version": incident_version,
            "remediation_id": str(remediation_id),
            "remediation_version": remediation_version,
            "content": content.model_dump(mode="json"),
        }
    )


def render_postmortem_knowledge_content(
    record: CapacityGovernancePostmortemRecord,
) -> str:
    return "\n".join(
        (
            "治理事件复盘 (仅供值守参考, 不构成授权、恢复证据或执行指令)",
            f"根因分类: {record.root_cause.value}",
            f"影响分类: {record.impact.value}",
            f"预防措施: {record.prevention.value}",
            f"安全摘要: {record.summary}",
        )
    )


class CapacityIncidentScanReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    handler_version: str
    scanned_audit_events: int = Field(ge=0)
    scanned_alerts: int = Field(ge=0)
    scanned_quality_snapshots: int = Field(default=0, ge=0)
    scanned_postmortems: int = Field(default=0, ge=0)
    evaluated_drill_checks: int = Field(ge=0)
    matched_signals: int = Field(ge=0)
    opened_incidents: int = Field(ge=0)
    updated_incidents: int = Field(ge=0)
    resolved_incidents: int = Field(ge=0)
    truncated: bool
    scanned_at: datetime


class CapacityGovernanceDrillCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    detail: str


class CapacityGovernanceDrillReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    checks: tuple[CapacityGovernanceDrillCheck, ...]
    checked_at: datetime


class CapacityGovernanceSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    handler_version: str
    active_policy: ReflectionCapacityPolicyRecord | None
    request_counts: dict[str, int]
    alert_counts: dict[str, int]
    latest_alert_at: datetime | None


class CapacityDriftScanReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    handler_version: str
    expected_policy_id: UUID | None
    expected_policy_version: int | None
    expected_fingerprint: str
    scanned_observations: int = Field(ge=0)
    drifted_observations: int = Field(ge=0)
    opened_alerts: int = Field(ge=0)
    updated_alerts: int = Field(ge=0)
    resolved_alerts: int = Field(ge=0)
    insufficient_samples: bool
    scanned_at: datetime


class CapacityGovernanceAuthorizationError(PermissionError):
    """The current database-backed principal cannot perform this action."""


class CapacityGovernanceCursorError(ValueError):
    """A capacity governance keyset cursor is malformed or scope-mismatched."""


def capacity_threshold_fingerprint(thresholds: ReflectionCapacityThresholds) -> str:
    payload = json.dumps(
        thresholds.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def capacity_drift_dedupe_key(
    *,
    handler_version: str,
    expected_fingerprint: str,
    observed_fingerprint: str,
) -> str:
    payload = (
        f"policy_drift|{handler_version}|{expected_fingerprint}|{observed_fingerprint}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _canonical_fingerprint(payload: dict[str, object]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _incident_candidate(
    *,
    tenant_id: UUID,
    handler_version: str,
    signal: CapacityGovernanceIncidentSignal,
    severity: CapacityGovernanceIncidentSeverity,
    target: str,
    source_id: UUID | None,
    evidence_at: datetime,
    evidence: dict[str, object],
    details: dict[str, str | int | float | bool | None],
) -> CapacityGovernanceIncidentCandidate:
    if evidence_at.tzinfo is None:
        raise ValueError("capacity incident evidence time must be timezone-aware")
    rule_version = CAPACITY_INCIDENT_RULE_VERSIONS[signal]
    return CapacityGovernanceIncidentCandidate(
        signal=signal,
        rule_version=rule_version,
        severity=severity,
        fingerprint=_canonical_fingerprint(
            {
                "handler_version": handler_version,
                "rule_version": rule_version,
                "signal": signal.value,
                "target": target,
                "tenant_id": str(tenant_id),
            }
        ),
        evidence_fingerprint=_canonical_fingerprint(evidence),
        source_id=source_id,
        evidence_at=evidence_at,
        details=details,
    )


def build_audit_failure_incident_candidate(
    *,
    tenant_id: UUID,
    handler_version: str,
    bucket_start: datetime,
    denied_count: int,
    conflict_count: int,
    thresholds: CapacityGovernanceIncidentThresholds,
) -> CapacityGovernanceIncidentCandidate | None:
    if bucket_start.tzinfo is None:
        raise ValueError("capacity incident bucket time must be timezone-aware")
    if denied_count < 0 or conflict_count < 0:
        raise ValueError("capacity incident audit counts must not be negative")
    total = denied_count + conflict_count
    if total < thresholds.audit_warning_count:
        return None
    severity = (
        CapacityGovernanceIncidentSeverity.CRITICAL
        if total >= thresholds.audit_critical_count
        else CapacityGovernanceIncidentSeverity.WARNING
    )
    bucket = bucket_start.isoformat()
    return _incident_candidate(
        tenant_id=tenant_id,
        handler_version=handler_version,
        signal=CapacityGovernanceIncidentSignal.AUDIT_FAILURE_SPIKE,
        severity=severity,
        target=bucket,
        source_id=None,
        evidence_at=bucket_start,
        evidence={
            "bucket_start": bucket,
            "conflict_count": conflict_count,
            "denied_count": denied_count,
        },
        details={
            "audit_window_seconds": thresholds.audit_window_seconds,
            "bucket_start": bucket,
            "conflict_count": conflict_count,
            "denied_count": denied_count,
            "sample_count": total,
        },
    )


def build_alert_sla_incident_candidate(
    *,
    tenant_id: UUID,
    handler_version: str,
    alert_id: UUID,
    alert_version: int,
    alert_status: CapacityGovernanceAlertStatus,
    first_seen_at: datetime,
    updated_at: datetime,
    now: datetime,
    response_warning_seconds: int,
    response_critical_seconds: int,
) -> CapacityGovernanceIncidentCandidate | None:
    sla = assess_capacity_alert_sla(
        status=alert_status,
        first_seen_at=first_seen_at,
        now=now,
        response_warning_seconds=response_warning_seconds,
        response_critical_seconds=response_critical_seconds,
    )
    if sla.state is not CapacityGovernanceAlertSLAState.BREACHED:
        return None
    return _incident_candidate(
        tenant_id=tenant_id,
        handler_version=handler_version,
        signal=CapacityGovernanceIncidentSignal.ALERT_SLA_BREACHED,
        severity=CapacityGovernanceIncidentSeverity.CRITICAL,
        target=str(alert_id),
        source_id=alert_id,
        evidence_at=updated_at,
        evidence={
            "alert_id": str(alert_id),
            "alert_version": alert_version,
            "status": alert_status.value,
            "updated_at": updated_at.isoformat(),
        },
        details={
            "alert_version": alert_version,
            "sla_state": sla.state.value,
        },
    )


def build_alert_reopen_incident_candidate(
    *,
    tenant_id: UUID,
    handler_version: str,
    alert_id: UUID,
    alert_version: int,
    alert_status: CapacityGovernanceAlertStatus,
    reopened_count: int,
    updated_at: datetime,
    thresholds: CapacityGovernanceIncidentThresholds,
) -> CapacityGovernanceIncidentCandidate | None:
    if reopened_count < 0:
        raise ValueError("capacity alert reopened_count must not be negative")
    if (
        alert_status is CapacityGovernanceAlertStatus.RESOLVED
        or reopened_count < thresholds.reopen_warning_count
    ):
        return None
    severity = (
        CapacityGovernanceIncidentSeverity.CRITICAL
        if reopened_count >= thresholds.reopen_critical_count
        else CapacityGovernanceIncidentSeverity.WARNING
    )
    return _incident_candidate(
        tenant_id=tenant_id,
        handler_version=handler_version,
        signal=CapacityGovernanceIncidentSignal.ALERT_REOPEN_REPEAT,
        severity=severity,
        target=str(alert_id),
        source_id=alert_id,
        evidence_at=updated_at,
        evidence={
            "alert_id": str(alert_id),
            "alert_version": alert_version,
            "reopened_count": reopened_count,
            "status": alert_status.value,
            "updated_at": updated_at.isoformat(),
        },
        details={
            "alert_version": alert_version,
            "reopened_count": reopened_count,
        },
    )


def build_drill_incident_candidates(
    *,
    tenant_id: UUID,
    handler_version: str,
    report: CapacityGovernanceDrillReport,
) -> tuple[CapacityGovernanceIncidentCandidate, ...]:
    return tuple(
        _incident_candidate(
            tenant_id=tenant_id,
            handler_version=handler_version,
            signal=CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED,
            severity=CapacityGovernanceIncidentSeverity.CRITICAL,
            target=check.name,
            source_id=None,
            evidence_at=report.checked_at,
            evidence={"check_name": check.name, "passed": False},
            details={"check_name": check.name},
        )
        for check in report.checks
        if not check.passed
    )


def _bounded_knowledge_quality_risk_snapshots(
    *,
    handler_version: str,
    postmortem_id: UUID,
    snapshots: tuple[CapacityGovernanceKnowledgeQualitySnapshotRecord, ...],
    now: datetime,
    thresholds: CapacityGovernanceKnowledgeQualityRiskThresholds,
) -> tuple[CapacityGovernanceKnowledgeQualitySnapshotRecord, ...]:
    if now.tzinfo is None:
        raise ValueError("knowledge quality risk time must be timezone-aware")
    if len(snapshots) > thresholds.maximum_snapshots:
        raise ValueError("knowledge quality risk snapshots exceed the configured maximum")
    cutoff = now - timedelta(seconds=thresholds.window_seconds)
    independent: dict[str, CapacityGovernanceKnowledgeQualitySnapshotRecord] = {}
    for snapshot in snapshots:
        if snapshot.handler_version != handler_version:
            raise ValueError("knowledge quality risk snapshot handler does not match")
        if snapshot.postmortem_id != postmortem_id:
            raise ValueError("knowledge quality risk snapshot postmortem does not match")
        if snapshot.captured_at.tzinfo is None:
            raise ValueError("knowledge quality risk snapshot time must be timezone-aware")
        if snapshot.captured_at > now:
            raise ValueError("knowledge quality risk snapshot must not be in the future")
        if snapshot.captured_at >= cutoff:
            current = independent.get(snapshot.evidence_fingerprint)
            if current is None or snapshot.captured_at > current.captured_at:
                independent[snapshot.evidence_fingerprint] = snapshot
    return tuple(
        sorted(
            independent.values(),
            key=lambda snapshot: (snapshot.captured_at, str(snapshot.id)),
        )
    )


def _build_snapshot_quality_incident_candidate(
    *,
    tenant_id: UUID,
    handler_version: str,
    postmortem_id: UUID,
    snapshots: tuple[CapacityGovernanceKnowledgeQualitySnapshotRecord, ...],
    signal: CapacityGovernanceIncidentSignal,
    warning_count: int,
    critical_count: int,
) -> CapacityGovernanceIncidentCandidate | None:
    if len(snapshots) < warning_count:
        return None
    latest = snapshots[-1]
    severity = (
        CapacityGovernanceIncidentSeverity.CRITICAL
        if len(snapshots) >= critical_count
        else CapacityGovernanceIncidentSeverity.WARNING
    )
    evidence = tuple(
        {
            "captured_at": snapshot.captured_at.isoformat(),
            "evidence_fingerprint": snapshot.evidence_fingerprint,
            "knowledge_version": snapshot.knowledge_version,
            "postmortem_version": snapshot.postmortem_version,
            "snapshot_id": str(snapshot.id),
        }
        for snapshot in snapshots
    )
    return _incident_candidate(
        tenant_id=tenant_id,
        handler_version=handler_version,
        signal=signal,
        severity=severity,
        target=str(postmortem_id),
        source_id=latest.id,
        evidence={
            "assessment": latest.assessment.value,
            "postmortem_id": str(postmortem_id),
            "snapshots": evidence,
        },
        evidence_at=latest.captured_at,
        details={
            "independent_evidence_count": len(snapshots),
            "latest_knowledge_version": latest.knowledge_version,
            "latest_postmortem_version": latest.postmortem_version,
            "latest_snapshot_id": str(latest.id),
            "postmortem_id": str(postmortem_id),
        },
    )


def build_persistent_unsafe_knowledge_incident_candidate(
    *,
    tenant_id: UUID,
    handler_version: str,
    postmortem_id: UUID,
    snapshots: tuple[CapacityGovernanceKnowledgeQualitySnapshotRecord, ...],
    now: datetime,
    thresholds: CapacityGovernanceKnowledgeQualityRiskThresholds,
) -> CapacityGovernanceIncidentCandidate | None:
    bounded = _bounded_knowledge_quality_risk_snapshots(
        handler_version=handler_version,
        postmortem_id=postmortem_id,
        snapshots=snapshots,
        now=now,
        thresholds=thresholds,
    )
    if (
        not bounded
        or bounded[-1].assessment
        is not CapacityGovernanceKnowledgeQualityAssessment.UNSAFE
    ):
        return None
    unsafe = tuple(
        snapshot
        for snapshot in bounded
        if snapshot.assessment is CapacityGovernanceKnowledgeQualityAssessment.UNSAFE
    )
    return _build_snapshot_quality_incident_candidate(
        tenant_id=tenant_id,
        handler_version=handler_version,
        postmortem_id=postmortem_id,
        snapshots=unsafe,
        signal=CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT,
        warning_count=thresholds.unsafe_warning_count,
        critical_count=thresholds.unsafe_critical_count,
    )


def build_repeated_degraded_knowledge_incident_candidate(
    *,
    tenant_id: UUID,
    handler_version: str,
    postmortem_id: UUID,
    snapshots: tuple[CapacityGovernanceKnowledgeQualitySnapshotRecord, ...],
    now: datetime,
    thresholds: CapacityGovernanceKnowledgeQualityRiskThresholds,
) -> CapacityGovernanceIncidentCandidate | None:
    bounded = _bounded_knowledge_quality_risk_snapshots(
        handler_version=handler_version,
        postmortem_id=postmortem_id,
        snapshots=snapshots,
        now=now,
        thresholds=thresholds,
    )
    if (
        not bounded
        or bounded[-1].assessment
        is not CapacityGovernanceKnowledgeQualityAssessment.DEGRADED
    ):
        return None
    degraded = tuple(
        snapshot
        for snapshot in bounded
        if snapshot.assessment is CapacityGovernanceKnowledgeQualityAssessment.DEGRADED
    )
    return _build_snapshot_quality_incident_candidate(
        tenant_id=tenant_id,
        handler_version=handler_version,
        postmortem_id=postmortem_id,
        snapshots=degraded,
        signal=CapacityGovernanceIncidentSignal.KNOWLEDGE_DEGRADED_REPEAT,
        warning_count=thresholds.degraded_warning_count,
        critical_count=thresholds.degraded_critical_count,
    )


def build_post_recovery_requarantine_incident_candidate(
    *,
    tenant_id: UUID,
    handler_version: str,
    postmortem_id: UUID,
    postmortem_status: CapacityGovernancePostmortemStatus,
    postmortem_version: int,
    knowledge_version: str,
    content_fingerprint: str,
    restore_count: int,
    last_restored_at: datetime | None,
    last_quarantined_at: datetime | None,
) -> CapacityGovernanceIncidentCandidate | None:
    if postmortem_version < 1 or restore_count < 0:
        raise ValueError("knowledge requarantine versions and counts must be valid")
    if not knowledge_version or len(content_fingerprint) != 64:
        raise ValueError("knowledge requarantine version and fingerprint must be valid")
    for timestamp in (last_restored_at, last_quarantined_at):
        if timestamp is not None and timestamp.tzinfo is None:
            raise ValueError("knowledge requarantine times must be timezone-aware")
    if (
        postmortem_status is not CapacityGovernancePostmortemStatus.QUARANTINED
        or restore_count < 1
        or last_restored_at is None
        or last_quarantined_at is None
        or last_quarantined_at <= last_restored_at
    ):
        return None
    return _incident_candidate(
        tenant_id=tenant_id,
        handler_version=handler_version,
        signal=CapacityGovernanceIncidentSignal.KNOWLEDGE_REQUARANTINED,
        severity=CapacityGovernanceIncidentSeverity.CRITICAL,
        target=str(postmortem_id),
        source_id=postmortem_id,
        evidence_at=last_quarantined_at,
        evidence={
            "content_fingerprint": content_fingerprint,
            "knowledge_version": knowledge_version,
            "last_quarantined_at": last_quarantined_at.isoformat(),
            "last_restored_at": last_restored_at.isoformat(),
            "postmortem_id": str(postmortem_id),
            "postmortem_version": postmortem_version,
            "restore_count": restore_count,
            "status": postmortem_status.value,
        },
        details={
            "knowledge_version": knowledge_version,
            "postmortem_version": postmortem_version,
            "restore_count": restore_count,
        },
    )


def assess_capacity_alert_sla(
    *,
    status: CapacityGovernanceAlertStatus,
    first_seen_at: datetime,
    now: datetime,
    response_warning_seconds: int,
    response_critical_seconds: int,
) -> CapacityGovernanceAlertSLA:
    if first_seen_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("capacity alert SLA timestamps must be timezone-aware")
    if not 60 <= response_warning_seconds <= response_critical_seconds <= 2_592_000:
        raise ValueError("capacity alert SLA thresholds must be ordered")
    age_seconds = max(0, int((now - first_seen_at).total_seconds()))
    response_due_at = first_seen_at + timedelta(seconds=response_warning_seconds)
    escalation_due_at = first_seen_at + timedelta(seconds=response_critical_seconds)
    if status is CapacityGovernanceAlertStatus.RESOLVED:
        state = CapacityGovernanceAlertSLAState.RESOLVED
    elif status is CapacityGovernanceAlertStatus.ACKNOWLEDGED:
        state = CapacityGovernanceAlertSLAState.ACKNOWLEDGED
    elif now >= escalation_due_at:
        state = CapacityGovernanceAlertSLAState.BREACHED
    elif now >= response_due_at:
        state = CapacityGovernanceAlertSLAState.DUE
    else:
        state = CapacityGovernanceAlertSLAState.WITHIN_SLA
    return CapacityGovernanceAlertSLA(
        state=state,
        age_seconds=age_seconds,
        response_due_at=response_due_at,
        escalation_due_at=escalation_due_at,
    )
