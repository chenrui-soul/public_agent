from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from public_agent.api.app import create_app
from public_agent.api.capacity_governance import CapacityGovernancePrincipal
from public_agent.operations.capacity_control import (
    CAPACITY_GOVERNANCE_ROLES,
    CapacityChangeRequestPage,
    CapacityDriftScanReport,
    CapacityGovernanceAlertPage,
    CapacityGovernanceAuditPage,
    CapacityGovernanceAuthorizationError,
    CapacityGovernanceDrillCheck,
    CapacityGovernanceDrillReport,
    CapacityGovernanceIncidentPage,
    CapacityGovernanceIncidentRecord,
    CapacityGovernanceIncidentSeverity,
    CapacityGovernanceIncidentSignal,
    CapacityGovernanceIncidentStatus,
    CapacityGovernanceKnowledgeFeedbackPage,
    CapacityGovernanceKnowledgeFeedbackReason,
    CapacityGovernanceKnowledgeFeedbackRecord,
    CapacityGovernanceKnowledgeFeedbackSignal,
    CapacityGovernanceKnowledgeFeedbackStatus,
    CapacityGovernanceKnowledgeQualityAssessment,
    CapacityGovernanceKnowledgeQualitySnapshotPage,
    CapacityGovernanceKnowledgeQualitySnapshotRecord,
    CapacityGovernanceKnowledgeQualityTrendBucket,
    CapacityGovernanceKnowledgeQualityTrendPoint,
    CapacityGovernanceKnowledgeQualityTrendReport,
    CapacityGovernanceKnowledgeRecoveryPage,
    CapacityGovernanceKnowledgeRecoveryReason,
    CapacityGovernanceKnowledgeRecoveryRecord,
    CapacityGovernanceKnowledgeRecoveryStatus,
    CapacityGovernancePostmortemImpact,
    CapacityGovernancePostmortemPage,
    CapacityGovernancePostmortemPrevention,
    CapacityGovernancePostmortemRecord,
    CapacityGovernancePostmortemRootCause,
    CapacityGovernancePostmortemStatus,
    CapacityGovernanceRemediationPage,
    CapacityGovernanceRemediationPlaybook,
    CapacityGovernanceRemediationRecord,
    CapacityGovernanceRemediationStatus,
    CapacityGovernanceSummary,
)


class _HealthyDatabase:
    async def ping(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class _CapacityService:
    def __init__(self) -> None:
        self.actor_subject: str | None = None

    async def list_roles(self, *, actor: CapacityGovernancePrincipal):
        self.actor_subject = actor.subject
        return CAPACITY_GOVERNANCE_ROLES

    async def summary(self, *, actor: CapacityGovernancePrincipal):
        self.actor_subject = actor.subject
        return CapacityGovernanceSummary(
            handler_version="reflection-v1",
            active_policy=None,
            request_counts={},
            alert_counts={},
            latest_alert_at=None,
        )

    async def list_change_requests(self, _query, *, actor):
        self.actor_subject = actor.subject
        return CapacityChangeRequestPage(items=())

    async def list_alerts(self, _query, *, actor):
        self.actor_subject = actor.subject
        return CapacityGovernanceAlertPage(items=())

    async def list_audit_events(self, _query, *, actor):
        self.actor_subject = actor.subject
        return CapacityGovernanceAuditPage(items=())

    async def governance_drill(self, *, actor):
        self.actor_subject = actor.subject
        return CapacityGovernanceDrillReport(
            passed=True,
            checks=(
                CapacityGovernanceDrillCheck(
                    name="current_actor_revalidated",
                    passed=True,
                    detail="Current PostgreSQL identity was revalidated.",
                ),
            ),
            checked_at=datetime(2026, 8, 25, tzinfo=UTC),
        )

    async def list_incidents(self, _query, *, actor):
        self.actor_subject = actor.subject
        return CapacityGovernanceIncidentPage(items=(_incident_record(),))

    async def get_incident(self, _incident_id, *, actor):
        self.actor_subject = actor.subject
        return _incident_record()

    async def acknowledge_incident(self, *, incident_id, expected_version, actor):
        self.actor_subject = actor.subject
        return _incident_record().model_copy(
            update={
                "id": incident_id,
                "status": CapacityGovernanceIncidentStatus.ACKNOWLEDGED,
                "version": expected_version + 1,
                "acknowledged_by": actor.subject,
            }
        )

    async def list_remediations(self, _query, *, actor):
        self.actor_subject = actor.subject
        return CapacityGovernanceRemediationPage(items=(_remediation_record(),))

    async def get_remediation(self, _remediation_id, *, actor):
        self.actor_subject = actor.subject
        return _remediation_record()

    async def create_remediation(
        self,
        *,
        incident_id,
        expected_incident_version,
        playbook,
        actor,
    ):
        self.actor_subject = actor.subject
        return _remediation_record().model_copy(
            update={"incident_id": incident_id, "playbook": playbook}
        )

    async def approve_remediation(self, *, remediation_id, expected_version, actor):
        self.actor_subject = actor.subject
        return _remediation_record().model_copy(
            update={
                "id": remediation_id,
                "status": CapacityGovernanceRemediationStatus.APPROVED,
                "version": expected_version + 1,
                "approved_by": actor.subject,
            }
        )

    async def reject_remediation(self, *, remediation_id, expected_version, actor):
        self.actor_subject = actor.subject
        return _remediation_record().model_copy(
            update={
                "id": remediation_id,
                "status": CapacityGovernanceRemediationStatus.REJECTED,
                "version": expected_version + 1,
                "rejected_by": actor.subject,
            }
        )

    async def record_remediation_execution(
        self, *, remediation_id, expected_version, result, evidence, actor
    ):
        self.actor_subject = actor.subject
        return _remediation_record().model_copy(
            update={
                "id": remediation_id,
                "status": CapacityGovernanceRemediationStatus.VERIFICATION_PENDING,
                "version": expected_version + 1,
                "executed_by": actor.subject,
                "execution_result": result,
                "execution_evidence": evidence,
                "incident_version_at_execution": 2,
            }
        )

    async def verify_remediation(self, *, remediation_id, expected_version, actor):
        self.actor_subject = actor.subject
        return _remediation_record().model_copy(
            update={
                "id": remediation_id,
                "status": CapacityGovernanceRemediationStatus.VERIFIED,
                "version": expected_version + 1,
                "verified_by": actor.subject,
            }
        )

    async def list_postmortems(self, _query, *, actor):
        self.actor_subject = actor.subject
        return CapacityGovernancePostmortemPage(items=(_postmortem_record(),))

    async def get_postmortem(self, _postmortem_id, *, actor):
        self.actor_subject = actor.subject
        return _postmortem_record()

    async def create_postmortem(
        self, *, remediation_id, expected_remediation_version, content, actor
    ):
        self.actor_subject = actor.subject
        return _postmortem_record().model_copy(
            update={
                "remediation_id": remediation_id,
                "remediation_version": expected_remediation_version,
                "root_cause": content.root_cause,
                "impact": content.impact,
                "prevention": content.prevention,
                "summary": content.summary,
            }
        )

    async def approve_postmortem(self, *, postmortem_id, expected_version, actor):
        self.actor_subject = actor.subject
        return _postmortem_record().model_copy(
            update={
                "id": postmortem_id,
                "status": CapacityGovernancePostmortemStatus.PUBLISHED,
                "version": expected_version + 1,
                "reviewed_by": actor.subject,
                "knowledge_namespace": "operations.governance.postmortems",
                "knowledge_source_key": f"governance-postmortem:{postmortem_id}",
                "knowledge_version": "3-4-aaaaaaaaaaaa",
                "published_at": datetime(2026, 8, 25, tzinfo=UTC),
            }
        )

    async def reject_postmortem(self, *, postmortem_id, expected_version, actor):
        self.actor_subject = actor.subject
        return _postmortem_record().model_copy(
            update={
                "id": postmortem_id,
                "status": CapacityGovernancePostmortemStatus.REJECTED,
                "version": expected_version + 1,
                "reviewed_by": actor.subject,
            }
        )

    async def list_knowledge_feedback(self, _query, *, actor):
        self.actor_subject = actor.subject
        return CapacityGovernanceKnowledgeFeedbackPage(items=(_feedback_record(),))

    async def report_knowledge_feedback(
        self,
        *,
        postmortem_id,
        expected_postmortem_version,
        expected_knowledge_version,
        expected_content_fingerprint,
        content,
        actor,
    ):
        self.actor_subject = actor.subject
        return _feedback_record().model_copy(
            update={
                "postmortem_id": postmortem_id,
                "postmortem_version": expected_postmortem_version,
                "knowledge_version": expected_knowledge_version,
                "content_fingerprint": expected_content_fingerprint,
                "signal": content.signal,
                "reason": content.reason,
            }
        )

    async def confirm_knowledge_feedback(self, *, feedback_id, expected_version, actor):
        self.actor_subject = actor.subject
        return _feedback_record().model_copy(
            update={
                "id": feedback_id,
                "status": CapacityGovernanceKnowledgeFeedbackStatus.CONFIRMED,
                "version": expected_version + 1,
                "reviewed_by": actor.subject,
                "reviewed_at": datetime(2026, 8, 25, tzinfo=UTC),
            }
        )

    async def dismiss_knowledge_feedback(self, *, feedback_id, expected_version, actor):
        self.actor_subject = actor.subject
        return _feedback_record().model_copy(
            update={
                "id": feedback_id,
                "status": CapacityGovernanceKnowledgeFeedbackStatus.DISMISSED,
                "version": expected_version + 1,
                "reviewed_by": actor.subject,
                "reviewed_at": datetime(2026, 8, 25, tzinfo=UTC),
            }
        )

    async def list_knowledge_quality_snapshots(self, _query, *, actor):
        self.last_actor = actor
        return CapacityGovernanceKnowledgeQualitySnapshotPage(items=(_quality_snapshot_record(),))

    async def capture_knowledge_quality_snapshot(
        self, *, postmortem_id, expected_postmortem_version, actor
    ):
        self.last_actor = actor
        return _quality_snapshot_record().model_copy(
            update={
                "postmortem_id": postmortem_id,
                "postmortem_version": expected_postmortem_version,
            }
        )

    async def knowledge_quality_trend(self, query, *, actor):
        self.actor_subject = actor.subject
        return _quality_trend_report().model_copy(
            update={
                "bucket": query.bucket,
                "captured_from": query.captured_from,
                "captured_to": query.captured_to,
                "assessment": query.assessment,
            }
        )

    async def list_knowledge_recoveries(self, _query, *, actor):
        self.last_actor = actor
        return CapacityGovernanceKnowledgeRecoveryPage(items=(_recovery_record(),))

    async def request_knowledge_recovery(
        self,
        *,
        postmortem_id,
        expected_postmortem_version,
        snapshot_id,
        reason,
        actor,
    ):
        self.last_actor = actor
        return _recovery_record().model_copy(
            update={
                "postmortem_id": postmortem_id,
                "postmortem_version": expected_postmortem_version,
                "snapshot_id": snapshot_id,
                "reason": reason,
            }
        )

    async def approve_knowledge_recovery(self, *, recovery_id, expected_version, actor):
        self.last_actor = actor
        return _recovery_record().model_copy(
            update={
                "id": recovery_id,
                "status": CapacityGovernanceKnowledgeRecoveryStatus.APPROVED,
                "version": expected_version + 1,
                "reviewed_by": actor.subject,
                "reviewed_at": datetime(2026, 8, 25, 22, 0, tzinfo=UTC),
                "restored_knowledge_version": "3-4-aaaaaaaaaaaa-r1-v4",
            }
        )

    async def reject_knowledge_recovery(self, *, recovery_id, expected_version, actor):
        self.last_actor = actor
        return _recovery_record().model_copy(
            update={
                "id": recovery_id,
                "status": CapacityGovernanceKnowledgeRecoveryStatus.REJECTED,
                "version": expected_version + 1,
                "reviewed_by": actor.subject,
                "reviewed_at": datetime(2026, 8, 25, 22, 0, tzinfo=UTC),
            }
        )

    async def scan_drift(self, *, actor=None):
        self.actor_subject = actor.subject
        return CapacityDriftScanReport(
            handler_version="reflection-v1",
            expected_policy_id=None,
            expected_policy_version=None,
            expected_fingerprint="a" * 64,
            scanned_observations=0,
            drifted_observations=0,
            opened_alerts=0,
            updated_alerts=0,
            resolved_alerts=0,
            insufficient_samples=True,
            scanned_at=datetime(2026, 8, 25, tzinfo=UTC),
        )


def _principal() -> CapacityGovernancePrincipal:
    return CapacityGovernancePrincipal(
        principal_id=uuid4(),
        token_id=uuid4(),
        subject="capacity-operator",
        tenant_id="governance",
        all_agents=True,
        permissions=frozenset({"operations.capacity:read"}),
    )


def _incident_record() -> CapacityGovernanceIncidentRecord:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    return CapacityGovernanceIncidentRecord(
        id=UUID("00000000-0000-0000-0000-000000000024"),
        handler_version="reflection-v1",
        signal=CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED,
        rule_version="drill-check-failed/v1",
        severity=CapacityGovernanceIncidentSeverity.CRITICAL,
        status=CapacityGovernanceIncidentStatus.OPEN,
        version=1,
        source_id=None,
        fingerprint="a" * 64,
        evidence_fingerprint="b" * 64,
        first_seen_at=now,
        last_seen_at=now,
        last_evidence_at=now,
        occurrence_count=1,
        reopened_count=0,
        details={"check_name": "audit_append_only"},
        acknowledged_by=None,
        acknowledged_at=None,
        resolved_at=None,
        created_at=now,
        updated_at=now,
    )


def _remediation_record() -> CapacityGovernanceRemediationRecord:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    return CapacityGovernanceRemediationRecord(
        id=UUID("00000000-0000-0000-0000-000000000025"),
        incident_id=UUID("00000000-0000-0000-0000-000000000024"),
        handler_version="reflection-v1",
        incident_cycle=0,
        playbook=CapacityGovernanceRemediationPlaybook.DRILL_CONTROL_REPAIR,
        status=CapacityGovernanceRemediationStatus.AWAITING_APPROVAL,
        version=1,
        requested_by="capacity-operator",
        requested_at=now,
        created_at=now,
        updated_at=now,
    )


def _postmortem_record() -> CapacityGovernancePostmortemRecord:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    return CapacityGovernancePostmortemRecord(
        id=UUID("00000000-0000-0000-0000-000000000026"),
        incident_id=UUID("00000000-0000-0000-0000-000000000024"),
        remediation_id=UUID("00000000-0000-0000-0000-000000000025"),
        handler_version="reflection-v1",
        incident_cycle=0,
        incident_version=3,
        remediation_version=4,
        status=CapacityGovernancePostmortemStatus.AWAITING_REVIEW,
        version=1,
        root_cause=CapacityGovernancePostmortemRootCause.SCHEMA_CONTROL_GAP,
        impact=CapacityGovernancePostmortemImpact.NO_EXTERNAL_IMPACT,
        prevention=CapacityGovernancePostmortemPrevention.SCHEMA_VERIFICATION,
        summary="只读演练发现治理约束缺口, 恢复后验证通过。",
        content_fingerprint="a" * 64,
        requested_by="postmortem-requester",
        requested_at=now,
        created_at=now,
        updated_at=now,
    )


def _feedback_record() -> CapacityGovernanceKnowledgeFeedbackRecord:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    return CapacityGovernanceKnowledgeFeedbackRecord(
        id=UUID("00000000-0000-0000-0000-000000000027"),
        postmortem_id=UUID("00000000-0000-0000-0000-000000000026"),
        handler_version="reflection-v1",
        postmortem_version=2,
        knowledge_version="3-4-aaaaaaaaaaaa",
        content_fingerprint="a" * 64,
        signal=CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN,
        reason=CapacityGovernanceKnowledgeFeedbackReason.UNSAFE_CONTENT,
        status=CapacityGovernanceKnowledgeFeedbackStatus.AWAITING_REVIEW,
        version=1,
        reported_by="knowledge-feedback-reporter",
        reported_at=now,
        created_at=now,
        updated_at=now,
    )


def _quality_snapshot_record() -> CapacityGovernanceKnowledgeQualitySnapshotRecord:
    now = datetime(2026, 8, 25, 21, 0, tzinfo=UTC)
    return CapacityGovernanceKnowledgeQualitySnapshotRecord(
        id=UUID("00000000-0000-0000-0000-000000000028"),
        postmortem_id=UUID("00000000-0000-0000-0000-000000000026"),
        handler_version="reflection-v1",
        postmortem_version=3,
        knowledge_version="3-4-aaaaaaaaaaaa",
        content_fingerprint="a" * 64,
        evidence_fingerprint="b" * 64,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.UNSAFE,
        total_feedback=2,
        awaiting_review_count=0,
        confirmed_helpful_count=0,
        confirmed_not_helpful_count=0,
        confirmed_safety_count=1,
        dismissed_count=0,
        superseded_count=1,
        captured_by="knowledge-quality-assessor",
        captured_at=now,
        created_at=now,
    )


def _quality_trend_report() -> CapacityGovernanceKnowledgeQualityTrendReport:
    start = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)
    return CapacityGovernanceKnowledgeQualityTrendReport(
        handler_version="reflection-v1",
        bucket=CapacityGovernanceKnowledgeQualityTrendBucket.HOUR,
        captured_from=start,
        captured_to=datetime(2026, 8, 25, 22, 0, tzinfo=UTC),
        assessment=None,
        points=(
            CapacityGovernanceKnowledgeQualityTrendPoint(
                bucket_started_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                total_snapshots=2,
                insufficient_count=0,
                healthy_count=0,
                degraded_count=0,
                unsafe_count=2,
                distinct_postmortems=1,
            ),
        ),
        generated_at=datetime(2026, 8, 25, 22, 0, tzinfo=UTC),
    )


def _recovery_record() -> CapacityGovernanceKnowledgeRecoveryRecord:
    now = datetime(2026, 8, 25, 21, 30, tzinfo=UTC)
    return CapacityGovernanceKnowledgeRecoveryRecord(
        id=UUID("00000000-0000-0000-0000-000000000029"),
        postmortem_id=UUID("00000000-0000-0000-0000-000000000026"),
        snapshot_id=UUID("00000000-0000-0000-0000-000000000028"),
        handler_version="reflection-v1",
        postmortem_version=3,
        knowledge_version="3-4-aaaaaaaaaaaa",
        content_fingerprint="a" * 64,
        quarantine_feedback_id=UUID("00000000-0000-0000-0000-000000000027"),
        reason=CapacityGovernanceKnowledgeRecoveryReason.FALSE_POSITIVE,
        status=CapacityGovernanceKnowledgeRecoveryStatus.AWAITING_REVIEW,
        version=1,
        requested_by="knowledge-recovery-requester",
        requested_at=now,
        created_at=now,
        updated_at=now,
    )


def test_capacity_routes_and_console_are_not_exposed_without_service() -> None:
    with TestClient(create_app(database=_HealthyDatabase())) as client:
        assert client.get("/v1/operations/capacity-governance/summary").status_code == 404
        assert client.get("/console/capacity-governance").status_code == 404


def test_capacity_console_is_a_data_free_no_store_shell() -> None:
    service = _CapacityService()
    app = create_app(
        database=_HealthyDatabase(),
        capacity_governance=service,
        capacity_governance_principal_dependency=_principal,
    )

    with TestClient(app) as client:
        response = client.get("/console/capacity-governance")
        style = client.get("/console/assets/capacity-governance.css")
        script = client.get("/console/assets/capacity-governance.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "body{margin:0;min-width:0}" in style.text
    assert "body{margin:0;min-width:320px}" not in style.text
    assert "Authorization" not in response.text
    assert "sessionStorage" in script.text
    assert "localStorage" not in script.text
    assert "innerHTML" not in script.text
    assert "window.confirm" in script.text
    assert "/audit-events" in script.text
    assert "/drill-report" in script.text
    assert "/incidents" in script.text
    assert "/remediations" in script.text
    assert "/postmortems" in script.text
    assert "/knowledge-feedback" in script.text
    assert "/knowledge-quality-snapshots" in script.text
    assert "/knowledge-quality-trend" in script.text
    assert "/knowledge-recoveries" in script.text
    assert "/knowledge-recertifications" in script.text
    assert "REMEDIATION WORKFLOW" in response.text
    assert 'id="postmortem-dialog"' in response.text
    assert 'id="postmortem-summary"' in response.text
    assert 'id="knowledge-feedback-dialog"' in response.text
    assert 'id="knowledge-feedback-status"' in response.text
    assert 'id="knowledge-feedback-signal"' in response.text
    assert 'id="knowledge-quality-trend-bucket"' in response.text
    assert 'id="knowledge-quality-trend-assessment"' in response.text
    assert 'id="knowledge-quality-trend-lookback"' in response.text
    assert 'id="knowledge-quality-trend"' in response.text
    assert 'id="knowledge-quality-trend-empty"' in response.text
    assert 'id="knowledge-quality-assessment"' in response.text
    assert 'id="knowledge-recovery-status"' in response.text
    assert 'id="knowledge-recertification-status"' in response.text
    assert 'id="knowledge-recertifications"' in response.text
    assert 'value="quarantined"' in response.text
    assert 'value="superseded"' in response.text
    assert 'value="unsafe"' in response.text
    assert 'value="approved"' in response.text
    assert 'value="knowledge_unsafe_persistent"' in response.text
    assert 'value="knowledge_degraded_repeat"' in response.text
    assert 'value="knowledge_requarantined"' in response.text
    assert "feedbackPostmortem" in script.text
    assert "syncFeedbackReasons" in script.text
    assert "captureKnowledgeQuality" in script.text
    assert "knowledgeQualityTrendPath" in script.text
    assert "renderKnowledgeQualityTrend" in script.text
    assert "requestKnowledgeRecovery" in script.text
    assert "批准后将生成新的知识版本并重新进入 RAG" in script.text
    assert "无治理知识反馈读取权限" in script.text
    assert "无治理知识质量趋势读取权限" in script.text
    assert "无治理知识质量读取权限" in script.text
    assert "无隔离恢复读取权限" in script.text
    assert "无治理知识再认证读取权限" in script.text
    assert "knowledgeRecertificationPath" in script.text
    assert "renderKnowledgeRecertifications" in script.text
    assert "'knowledgeQualityTrend'" in script.text
    assert "kind:'knowledgeQualityTrend'" in script.text
    assert "knowledge_unsafe_persistent:'knowledge_safety_containment'" in script.text
    assert "knowledge_degraded_repeat:'knowledge_quality_review'" in script.text
    assert "knowledge_requarantined:'knowledge_recurrence_review'" in script.text
    assert "knowledge_safety_containment:'knowledge_quarantine_reviewed'" in script.text
    assert "knowledge_quality_review:'quality_evidence_reviewed'" in script.text
    assert "knowledge_recurrence_review:'restoration_history_reviewed'" in script.text
    assert "$('knowledge-quality-trend-bucket').addEventListener('change'" in script.text
    assert "$('knowledge-quality-trend-assessment').addEventListener('change'" in script.text
    assert "$('knowledge-quality-trend-lookback').addEventListener('change'" in script.text
    assert "resetViews();setConnected(false)" in script.text
    assert "const summary=window.prompt" not in script.text
    assert "record.sla.state" in script.text
    assert "Promise.allSettled" in script.text


def test_capacity_api_uses_trusted_principal_and_returns_bounded_pages() -> None:
    service = _CapacityService()
    app = create_app(
        database=_HealthyDatabase(),
        capacity_governance=service,
        capacity_governance_principal_dependency=_principal,
    )

    with TestClient(app) as client:
        summary = client.get("/v1/operations/capacity-governance/summary")
        requests = client.get("/v1/operations/capacity-governance/requests?limit=10")
        alerts = client.get("/v1/operations/capacity-governance/alerts?limit=10")
        audit = client.get("/v1/operations/capacity-governance/audit-events?limit=10")
        drill = client.get("/v1/operations/capacity-governance/drill-report")
        incidents = client.get("/v1/operations/capacity-governance/incidents?limit=10")
        incident = client.get(
            "/v1/operations/capacity-governance/incidents/00000000-0000-0000-0000-000000000024"
        )
        acknowledged = client.post(
            "/v1/operations/capacity-governance/incidents/"
            "00000000-0000-0000-0000-000000000024/acknowledge",
            json={"expected_version": 1},
        )
        remediations = client.get("/v1/operations/capacity-governance/remediations?limit=10")
        remediation = client.get(
            "/v1/operations/capacity-governance/remediations/00000000-0000-0000-0000-000000000025"
        )
        created_remediation = client.post(
            "/v1/operations/capacity-governance/incidents/"
            "00000000-0000-0000-0000-000000000024/remediations",
            json={
                "expected_incident_version": 2,
                "playbook": "drill_control_repair",
            },
        )
        executed_remediation = client.post(
            "/v1/operations/capacity-governance/remediations/"
            "00000000-0000-0000-0000-000000000025/execution",
            json={
                "expected_version": 2,
                "result": "completed",
                "evidence": "schema_control_restored",
            },
        )
        postmortems = client.get("/v1/operations/capacity-governance/postmortems?limit=10")
        postmortem = client.get(
            "/v1/operations/capacity-governance/postmortems/00000000-0000-0000-0000-000000000026"
        )
        created_postmortem = client.post(
            "/v1/operations/capacity-governance/remediations/"
            "00000000-0000-0000-0000-000000000025/postmortems",
            json={
                "expected_remediation_version": 4,
                "root_cause": "schema_control_gap",
                "impact": "no_external_impact",
                "prevention": "schema_verification",
                "summary": "只读演练发现治理约束缺口, 恢复后验证通过。",
            },
        )
        approved_postmortem = client.post(
            "/v1/operations/capacity-governance/postmortems/"
            "00000000-0000-0000-0000-000000000026/approve",
            json={"expected_version": 1},
        )
        feedback = client.get("/v1/operations/capacity-governance/knowledge-feedback?limit=10")
        reported_feedback = client.post(
            "/v1/operations/capacity-governance/postmortems/"
            "00000000-0000-0000-0000-000000000026/feedback",
            json={
                "expected_postmortem_version": 2,
                "expected_knowledge_version": "3-4-aaaaaaaaaaaa",
                "expected_content_fingerprint": "a" * 64,
                "signal": "safety_concern",
                "reason": "unsafe_content",
            },
        )
        confirmed_feedback = client.post(
            "/v1/operations/capacity-governance/knowledge-feedback/"
            "00000000-0000-0000-0000-000000000027/confirm",
            json={"expected_version": 1},
        )
        quality_snapshots = client.get(
            "/v1/operations/capacity-governance/knowledge-quality-snapshots"
            "?assessment=unsafe&limit=10"
        )
        quality_trend = client.get(
            "/v1/operations/capacity-governance/knowledge-quality-trend"
            "?captured_from=2026-08-25T20:00:00Z"
            "&captured_to=2026-08-25T22:00:00Z"
            "&bucket=hour&assessment=unsafe&limit=24"
        )
        captured_snapshot = client.post(
            "/v1/operations/capacity-governance/postmortems/"
            "00000000-0000-0000-0000-000000000026/quality-snapshots",
            json={"expected_postmortem_version": 3},
        )
        recoveries = client.get(
            "/v1/operations/capacity-governance/knowledge-recoveries"
            "?status=awaiting_review&limit=10"
        )
        requested_recovery = client.post(
            "/v1/operations/capacity-governance/postmortems/"
            "00000000-0000-0000-0000-000000000026/recoveries",
            json={
                "expected_postmortem_version": 3,
                "snapshot_id": "00000000-0000-0000-0000-000000000028",
                "reason": "false_positive",
            },
        )
        approved_recovery = client.post(
            "/v1/operations/capacity-governance/knowledge-recoveries/"
            "00000000-0000-0000-0000-000000000029/approve",
            json={"expected_version": 1},
        )
        rejected_recovery = client.post(
            "/v1/operations/capacity-governance/knowledge-recoveries/"
            "00000000-0000-0000-0000-000000000029/reject",
            json={"expected_version": 1},
        )

    assert summary.status_code == 200
    assert summary.json()["handler_version"] == "reflection-v1"
    assert requests.json() == {"items": [], "next_cursor": None}
    assert alerts.json() == {"items": [], "next_cursor": None}
    assert audit.json() == {"items": [], "next_cursor": None}
    assert drill.json()["passed"] is True
    assert incidents.json()["items"][0]["signal"] == "drill_check_failed"
    assert incident.json()["details"] == {"check_name": "audit_append_only"}
    assert acknowledged.json()["status"] == "acknowledged"
    assert "acknowledged_token_id" not in str(acknowledged.json())
    assert remediations.json()["items"][0]["playbook"] == "drill_control_repair"
    assert remediation.status_code == 200
    assert created_remediation.status_code == 201
    assert executed_remediation.json()["execution_result"] == "completed"
    assert postmortems.json()["items"][0]["root_cause"] == "schema_control_gap"
    assert postmortem.status_code == 200
    assert created_postmortem.status_code == 201
    assert approved_postmortem.json()["status"] == "published"
    assert feedback.json()["items"][0]["signal"] == "safety_concern"
    assert reported_feedback.status_code == 201
    assert confirmed_feedback.json()["status"] == "confirmed"
    assert quality_snapshots.json()["items"][0]["assessment"] == "unsafe"
    assert quality_trend.status_code == 200
    assert quality_trend.json()["assessment"] == "unsafe"
    assert quality_trend.json()["points"][0]["unsafe_count"] == 2
    assert captured_snapshot.status_code == 201
    assert recoveries.json()["items"][0]["status"] == "awaiting_review"
    assert requested_recovery.status_code == 201
    assert approved_recovery.json()["status"] == "approved"
    assert rejected_recovery.json()["status"] == "rejected"
    assert "reported_token_id" not in str(feedback.json())
    assert "requested_token_id" not in str(remediations.json())
    assert service.actor_subject == "capacity-operator"


def test_capacity_api_maps_authorization_failure_to_safe_403() -> None:
    class _DeniedCapacityService(_CapacityService):
        async def summary(self, *, actor: CapacityGovernancePrincipal):
            raise CapacityGovernanceAuthorizationError("revoked")

    app = create_app(
        database=_HealthyDatabase(),
        capacity_governance=_DeniedCapacityService(),
        capacity_governance_principal_dependency=_principal,
    )

    with TestClient(app) as client:
        response = client.get("/v1/operations/capacity-governance/summary")

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "capacity_governance_forbidden",
            "message": ("The authenticated principal cannot perform this capacity action."),
        }
    }


def test_capacity_api_rejects_invalid_uuid_without_leaking_details() -> None:
    app = create_app(
        database=_HealthyDatabase(),
        capacity_governance=_CapacityService(),
        capacity_governance_principal_dependency=_principal,
    )

    with TestClient(app) as client:
        response = client.get("/v1/operations/capacity-governance/requests/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"


def test_principal_fixture_uses_real_uuid_identifiers() -> None:
    principal = _principal()

    assert isinstance(principal.principal_id, UUID)
    assert isinstance(principal.token_id, UUID)
