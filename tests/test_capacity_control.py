from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from public_agent.auth import DEFAULT_MANAGEABLE_PERMISSIONS
from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_control import (
    CAPACITY_ALERTS_MANAGE,
    CAPACITY_AUDIT_READ,
    CAPACITY_GOVERNANCE_PERMISSIONS,
    CAPACITY_GOVERNANCE_ROLES,
    CAPACITY_INCIDENTS_MANAGE,
    CAPACITY_INCIDENTS_READ,
    CAPACITY_KNOWLEDGE_FEEDBACK_READ,
    CAPACITY_KNOWLEDGE_FEEDBACK_REPORT,
    CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
    CAPACITY_KNOWLEDGE_QUALITY_ASSESS,
    CAPACITY_KNOWLEDGE_QUALITY_READ,
    CAPACITY_KNOWLEDGE_RECOVERY_READ,
    CAPACITY_KNOWLEDGE_RECOVERY_REQUEST,
    CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
    CAPACITY_POSTMORTEMS_READ,
    CAPACITY_POSTMORTEMS_REQUEST,
    CAPACITY_POSTMORTEMS_REVIEW,
    CAPACITY_REMEDIATIONS_APPROVE,
    CAPACITY_REMEDIATIONS_EXECUTE,
    CAPACITY_REMEDIATIONS_READ,
    CAPACITY_REMEDIATIONS_REQUEST,
    CAPACITY_REMEDIATIONS_VERIFY,
    CapacityGovernanceAlertSLAState,
    CapacityGovernanceAlertStatus,
    CapacityGovernanceAuditOutcome,
    CapacityGovernanceAuditQuery,
    CapacityGovernanceCursorError,
    CapacityGovernanceDrillCheck,
    CapacityGovernanceDrillReport,
    CapacityGovernanceIncidentQuery,
    CapacityGovernanceIncidentSeverity,
    CapacityGovernanceIncidentSignal,
    CapacityGovernanceIncidentThresholds,
    CapacityGovernanceKnowledgeFeedbackInput,
    CapacityGovernanceKnowledgeFeedbackReason,
    CapacityGovernanceKnowledgeFeedbackSignal,
    CapacityGovernanceKnowledgeQualityAssessment,
    CapacityGovernanceKnowledgeQualityRiskThresholds,
    CapacityGovernanceKnowledgeQualitySnapshotRecord,
    CapacityGovernanceKnowledgeQualityTrendBucket,
    CapacityGovernanceKnowledgeQualityTrendPoint,
    CapacityGovernanceKnowledgeQualityTrendQuery,
    CapacityGovernancePostmortemImpact,
    CapacityGovernancePostmortemInput,
    CapacityGovernancePostmortemPrevention,
    CapacityGovernancePostmortemRootCause,
    CapacityGovernancePostmortemStatus,
    CapacityGovernanceRemediationPlaybook,
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
    expected_remediation_playbook,
    governance_knowledge_quality_assessment,
    postmortem_content_fingerprint,
    validate_postmortem_classification,
)
from public_agent.storage.capacity_control import _decode_cursor, _encode_cursor


def _thresholds() -> ReflectionCapacityThresholds:
    return ReflectionCapacityThresholds(
        stale_after_seconds=180,
        minimum_workers=2,
        maximum_workers=20,
        target_jobs_per_worker=20,
        ready_warning=100,
        ready_critical=500,
        oldest_warning_seconds=300,
        oldest_critical_seconds=1_800,
        dead_letter_warning=1,
        dead_letter_critical=10,
    )


def test_capacity_threshold_fingerprint_is_canonical_and_sensitive() -> None:
    first = capacity_threshold_fingerprint(_thresholds())
    same = capacity_threshold_fingerprint(
        ReflectionCapacityThresholds.model_validate(
            dict(reversed(tuple(_thresholds().model_dump().items())))
        )
    )
    changed = capacity_threshold_fingerprint(
        _thresholds().model_copy(update={"ready_warning": 101})
    )

    assert first == same
    assert len(first) == 64
    assert changed != first


def test_drift_dedupe_key_binds_handler_expected_and_observed_fingerprints() -> None:
    first = capacity_drift_dedupe_key(
        handler_version="reflection-v1",
        expected_fingerprint="a" * 64,
        observed_fingerprint="b" * 64,
    )

    assert len(first) == 64
    assert first != capacity_drift_dedupe_key(
        handler_version="reflection-v2",
        expected_fingerprint="a" * 64,
        observed_fingerprint="b" * 64,
    )


def test_capacity_roles_are_least_privilege_and_manageable() -> None:
    by_name = {role.name: set(role.permissions) for role in CAPACITY_GOVERNANCE_ROLES}

    assert CAPACITY_GOVERNANCE_PERMISSIONS <= DEFAULT_MANAGEABLE_PERMISSIONS
    assert CAPACITY_ALERTS_MANAGE in by_name["alert_operator"]
    assert by_name["auditor"] == {CAPACITY_AUDIT_READ}
    assert by_name["incident_viewer"] == {CAPACITY_INCIDENTS_READ}
    assert by_name["incident_operator"] == {
        CAPACITY_INCIDENTS_READ,
        CAPACITY_INCIDENTS_MANAGE,
    }
    assert CAPACITY_INCIDENTS_READ not in by_name["auditor"]
    assert CAPACITY_AUDIT_READ not in by_name["incident_operator"]
    assert by_name["remediation_viewer"] == {CAPACITY_REMEDIATIONS_READ}
    assert by_name["remediation_requester"] == {
        CAPACITY_REMEDIATIONS_READ,
        CAPACITY_REMEDIATIONS_REQUEST,
    }
    assert by_name["remediation_approver"] == {
        CAPACITY_REMEDIATIONS_READ,
        CAPACITY_REMEDIATIONS_APPROVE,
    }
    assert by_name["remediation_executor"] == {
        CAPACITY_REMEDIATIONS_READ,
        CAPACITY_REMEDIATIONS_EXECUTE,
    }
    assert by_name["remediation_verifier"] == {
        CAPACITY_REMEDIATIONS_READ,
        CAPACITY_REMEDIATIONS_VERIFY,
    }
    assert by_name["postmortem_viewer"] == {CAPACITY_POSTMORTEMS_READ}
    assert by_name["postmortem_requester"] == {
        CAPACITY_POSTMORTEMS_READ,
        CAPACITY_POSTMORTEMS_REQUEST,
    }
    assert by_name["postmortem_reviewer"] == {
        CAPACITY_POSTMORTEMS_READ,
        CAPACITY_POSTMORTEMS_REVIEW,
    }
    assert CAPACITY_POSTMORTEMS_REVIEW not in by_name["postmortem_requester"]
    assert by_name["knowledge_feedback_viewer"] == {CAPACITY_KNOWLEDGE_FEEDBACK_READ}
    assert by_name["knowledge_feedback_reporter"] == {
        CAPACITY_KNOWLEDGE_FEEDBACK_READ,
        CAPACITY_KNOWLEDGE_FEEDBACK_REPORT,
    }
    assert by_name["knowledge_feedback_reviewer"] == {
        CAPACITY_KNOWLEDGE_FEEDBACK_READ,
        CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
    }
    assert CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW not in by_name[
        "knowledge_feedback_reporter"
    ]
    assert by_name["knowledge_quality_viewer"] == {CAPACITY_KNOWLEDGE_QUALITY_READ}
    assert by_name["knowledge_quality_assessor"] == {
        CAPACITY_KNOWLEDGE_QUALITY_READ,
        CAPACITY_KNOWLEDGE_QUALITY_ASSESS,
    }
    assert by_name["knowledge_recovery_viewer"] == {CAPACITY_KNOWLEDGE_RECOVERY_READ}
    assert by_name["knowledge_recovery_requester"] == {
        CAPACITY_KNOWLEDGE_RECOVERY_READ,
        CAPACITY_KNOWLEDGE_RECOVERY_REQUEST,
    }
    assert by_name["knowledge_recovery_reviewer"] == {
        CAPACITY_KNOWLEDGE_RECOVERY_READ,
        CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
    }
    assert CAPACITY_KNOWLEDGE_RECOVERY_REVIEW not in by_name[
        "knowledge_recovery_requester"
    ]
    assert "operations.capacity:approve" not in by_name["proposer"]
    assert "operations.capacity:publish" not in by_name["approver"]


@pytest.mark.parametrize(
    ("helpful", "not_helpful", "safety", "expected"),
    (
        (0, 0, 0, CapacityGovernanceKnowledgeQualityAssessment.INSUFFICIENT),
        (2, 1, 0, CapacityGovernanceKnowledgeQualityAssessment.HEALTHY),
        (1, 2, 0, CapacityGovernanceKnowledgeQualityAssessment.DEGRADED),
        (10, 0, 1, CapacityGovernanceKnowledgeQualityAssessment.UNSAFE),
    ),
)
def test_governance_knowledge_quality_assessment_is_deterministic(
    helpful: int,
    not_helpful: int,
    safety: int,
    expected: CapacityGovernanceKnowledgeQualityAssessment,
) -> None:
    assert (
        governance_knowledge_quality_assessment(
            confirmed_helpful=helpful,
            confirmed_not_helpful=not_helpful,
            confirmed_safety=safety,
        )
        is expected
    )
    with pytest.raises(ValueError, match="non-negative"):
        governance_knowledge_quality_assessment(
            confirmed_helpful=-1,
            confirmed_not_helpful=not_helpful,
            confirmed_safety=safety,
        )


@pytest.mark.parametrize(
    ("signal", "playbook"),
    (
        (
            CapacityGovernanceIncidentSignal.AUDIT_FAILURE_SPIKE,
            CapacityGovernanceRemediationPlaybook.AUDIT_FAILURE_CONTAINMENT,
        ),
        (
            CapacityGovernanceIncidentSignal.ALERT_SLA_BREACHED,
            CapacityGovernanceRemediationPlaybook.ALERT_SLA_RECOVERY,
        ),
        (
            CapacityGovernanceIncidentSignal.ALERT_REOPEN_REPEAT,
            CapacityGovernanceRemediationPlaybook.ALERT_REOPEN_STABILIZATION,
        ),
        (
            CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED,
            CapacityGovernanceRemediationPlaybook.DRILL_CONTROL_REPAIR,
        ),
        (
            CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT,
            CapacityGovernanceRemediationPlaybook.KNOWLEDGE_SAFETY_CONTAINMENT,
        ),
        (
            CapacityGovernanceIncidentSignal.KNOWLEDGE_DEGRADED_REPEAT,
            CapacityGovernanceRemediationPlaybook.KNOWLEDGE_QUALITY_REVIEW,
        ),
        (
            CapacityGovernanceIncidentSignal.KNOWLEDGE_REQUARANTINED,
            CapacityGovernanceRemediationPlaybook.KNOWLEDGE_RECURRENCE_REVIEW,
        ),
    ),
)
def test_incident_signals_have_fixed_remediation_playbooks(signal, playbook) -> None:
    assert expected_remediation_playbook(signal) is playbook


def test_postmortem_input_is_bounded_canonical_and_instruction_free() -> None:
    postmortem = CapacityGovernancePostmortemInput(
        root_cause=CapacityGovernancePostmortemRootCause.SCHEMA_CONTROL_GAP,
        impact=CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
        prevention=CapacityGovernancePostmortemPrevention.SCHEMA_VERIFICATION,
        summary="  只读演练发现约束缺失, 恢复后验证通过。  ",
    )

    assert postmortem.summary == "只读演练发现约束缺失, 恢复后验证通过。"
    validate_postmortem_classification(
        CapacityGovernanceRemediationPlaybook.DRILL_CONTROL_REPAIR,
        postmortem,
    )
    with pytest.raises(ValueError, match="classification"):
        validate_postmortem_classification(
            CapacityGovernanceRemediationPlaybook.ALERT_SLA_RECOVERY,
            postmortem,
        )
    for unsafe in (
        "Authorization: Bearer secret-token",
        "postgresql://operator:password@example.test/governance",
        "kubectl delete deployment reflection-worker",
        "```sh\ndocker compose down\n```",
    ):
        with pytest.raises(ValueError, match="safe summary"):
            CapacityGovernancePostmortemInput(
                root_cause=CapacityGovernancePostmortemRootCause.SCHEMA_CONTROL_GAP,
                impact=CapacityGovernancePostmortemImpact.CONTROL_DEGRADATION,
                prevention=CapacityGovernancePostmortemPrevention.SCHEMA_VERIFICATION,
                summary=unsafe,
            )


def test_postmortem_fingerprint_binds_source_versions_and_content() -> None:
    incident_id = uuid4()
    remediation_id = uuid4()
    payload = CapacityGovernancePostmortemInput(
        root_cause=CapacityGovernancePostmortemRootCause.SCHEMA_CONTROL_GAP,
        impact=CapacityGovernancePostmortemImpact.NO_EXTERNAL_IMPACT,
        prevention=CapacityGovernancePostmortemPrevention.SCHEMA_VERIFICATION,
        summary="演练发现并修复了治理约束缺口。",
    )
    first = postmortem_content_fingerprint(
        incident_id=incident_id,
        incident_cycle=0,
        incident_version=3,
        remediation_id=remediation_id,
        remediation_version=4,
        content=payload,
    )

    assert len(first) == 64
    assert first == postmortem_content_fingerprint(
        incident_id=incident_id,
        incident_cycle=0,
        incident_version=3,
        remediation_id=remediation_id,
        remediation_version=4,
        content=payload,
    )
    assert first != postmortem_content_fingerprint(
        incident_id=incident_id,
        incident_cycle=0,
        incident_version=4,
        remediation_id=remediation_id,
        remediation_version=4,
        content=payload,
    )


def test_knowledge_feedback_uses_consistent_bounded_classifications() -> None:
    feedback = CapacityGovernanceKnowledgeFeedbackInput(
        signal=CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN,
        reason=CapacityGovernanceKnowledgeFeedbackReason.UNSAFE_CONTENT,
    )

    assert feedback.reason is CapacityGovernanceKnowledgeFeedbackReason.UNSAFE_CONTENT
    with pytest.raises(ValueError, match="safety signal and reason"):
        CapacityGovernanceKnowledgeFeedbackInput(
            signal=CapacityGovernanceKnowledgeFeedbackSignal.NOT_HELPFUL,
            reason=CapacityGovernanceKnowledgeFeedbackReason.UNSAFE_CONTENT,
        )
    with pytest.raises(ValueError, match="safety signal and reason"):
        CapacityGovernanceKnowledgeFeedbackInput(
            signal=CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN,
            reason=CapacityGovernanceKnowledgeFeedbackReason.ACCURACY,
        )


def test_incident_rules_have_stable_fingerprints_and_new_evidence() -> None:
    tenant_id = uuid4()
    bucket_start = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    thresholds = CapacityGovernanceIncidentThresholds(
        audit_warning_count=3,
        audit_critical_count=5,
    )

    warning = build_audit_failure_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        bucket_start=bucket_start,
        denied_count=2,
        conflict_count=1,
        thresholds=thresholds,
    )
    critical = build_audit_failure_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        bucket_start=bucket_start,
        denied_count=4,
        conflict_count=1,
        thresholds=thresholds,
    )

    assert warning is not None
    assert critical is not None
    assert warning.signal is CapacityGovernanceIncidentSignal.AUDIT_FAILURE_SPIKE
    assert warning.severity is CapacityGovernanceIncidentSeverity.WARNING
    assert critical.severity is CapacityGovernanceIncidentSeverity.CRITICAL
    assert warning.rule_version == "audit-failure-spike/v1"
    assert warning.fingerprint == critical.fingerprint
    assert warning.evidence_fingerprint != critical.evidence_fingerprint
    assert len(warning.fingerprint) == 64
    assert build_audit_failure_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        bucket_start=bucket_start,
        denied_count=1,
        conflict_count=1,
        thresholds=thresholds,
    ) is None


def test_incident_rules_cover_sla_reopen_and_drill_signals() -> None:
    tenant_id = uuid4()
    alert_id = uuid4()
    now = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    thresholds = CapacityGovernanceIncidentThresholds(
        reopen_warning_count=2,
        reopen_critical_count=4,
    )

    sla = build_alert_sla_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        alert_id=alert_id,
        alert_version=3,
        alert_status=CapacityGovernanceAlertStatus.OPEN,
        first_seen_at=now - timedelta(hours=2),
        updated_at=now - timedelta(minutes=5),
        now=now,
        response_warning_seconds=900,
        response_critical_seconds=3_600,
    )
    reopened = build_alert_reopen_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        alert_id=alert_id,
        alert_version=4,
        alert_status=CapacityGovernanceAlertStatus.OPEN,
        reopened_count=4,
        updated_at=now,
        thresholds=thresholds,
    )
    drill = build_drill_incident_candidates(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        report=CapacityGovernanceDrillReport(
            passed=False,
            checks=(
                CapacityGovernanceDrillCheck(
                    name="audit_append_only",
                    passed=False,
                    detail="The required append-only control is absent.",
                ),
                CapacityGovernanceDrillCheck(
                    name="audit_query_indexes",
                    passed=True,
                    detail="Required indexes are present.",
                ),
            ),
            checked_at=now,
        ),
    )

    assert sla is not None
    assert sla.signal is CapacityGovernanceIncidentSignal.ALERT_SLA_BREACHED
    assert sla.severity is CapacityGovernanceIncidentSeverity.CRITICAL
    assert reopened is not None
    assert reopened.signal is CapacityGovernanceIncidentSignal.ALERT_REOPEN_REPEAT
    assert reopened.severity is CapacityGovernanceIncidentSeverity.CRITICAL
    assert len(drill) == 1
    assert drill[0].signal is CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED
    assert drill[0].details == {"check_name": "audit_append_only"}
    assert build_alert_reopen_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        alert_id=alert_id,
        alert_version=5,
        alert_status=CapacityGovernanceAlertStatus.RESOLVED,
        reopened_count=5,
        updated_at=now,
        thresholds=thresholds,
    ) is None


def test_incident_query_is_bounded_and_binds_signal_filter() -> None:
    query = CapacityGovernanceIncidentQuery(
        signal=CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED,
        limit=100,
    )

    assert query.limit == 100
    with pytest.raises(ValueError):
        CapacityGovernanceIncidentQuery(limit=101)


def _quality_snapshot(
    *,
    postmortem_id,
    assessment: CapacityGovernanceKnowledgeQualityAssessment,
    captured_at: datetime,
    evidence_fingerprint: str,
    postmortem_version: int = 3,
) -> CapacityGovernanceKnowledgeQualitySnapshotRecord:
    return CapacityGovernanceKnowledgeQualitySnapshotRecord(
        id=uuid4(),
        postmortem_id=postmortem_id,
        handler_version="reflection-v1",
        postmortem_version=postmortem_version,
        knowledge_version=f"postmortem-v{postmortem_version}",
        content_fingerprint="a" * 64,
        evidence_fingerprint=evidence_fingerprint,
        assessment=assessment,
        total_feedback=1,
        awaiting_review_count=0,
        confirmed_helpful_count=(
            1 if assessment is CapacityGovernanceKnowledgeQualityAssessment.HEALTHY else 0
        ),
        confirmed_not_helpful_count=(
            1 if assessment is CapacityGovernanceKnowledgeQualityAssessment.DEGRADED else 0
        ),
        confirmed_safety_count=(
            1 if assessment is CapacityGovernanceKnowledgeQualityAssessment.UNSAFE else 0
        ),
        dismissed_count=(
            1
            if assessment is CapacityGovernanceKnowledgeQualityAssessment.INSUFFICIENT
            else 0
        ),
        superseded_count=0,
        captured_by="quality-assessor",
        captured_at=captured_at,
        created_at=captured_at,
    )


def test_knowledge_quality_trend_contract_is_bounded_and_zero_safe() -> None:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    query = CapacityGovernanceKnowledgeQualityTrendQuery(
        bucket=CapacityGovernanceKnowledgeQualityTrendBucket.HOUR,
        captured_from=start,
        captured_to=start + timedelta(hours=24),
        assessment=CapacityGovernanceKnowledgeQualityAssessment.UNSAFE,
        limit=24,
    )
    empty = CapacityGovernanceKnowledgeQualityTrendPoint(
        bucket_started_at=start,
        total_snapshots=0,
        insufficient_count=0,
        healthy_count=0,
        degraded_count=0,
        unsafe_count=0,
        distinct_postmortems=0,
    )

    assert query.limit == 24
    assert empty.total_snapshots == 0
    with pytest.raises(ValueError, match="timezone-aware"):
        CapacityGovernanceKnowledgeQualityTrendQuery(
            captured_from=datetime(2026, 8, 24),
            captured_to=start,
        )
    with pytest.raises(ValueError, match="ordered"):
        CapacityGovernanceKnowledgeQualityTrendQuery(
            captured_from=start + timedelta(hours=1),
            captured_to=start,
        )
    with pytest.raises(ValueError, match="assessment counts"):
        CapacityGovernanceKnowledgeQualityTrendPoint(
            bucket_started_at=start,
            total_snapshots=1,
            insufficient_count=0,
            healthy_count=0,
            degraded_count=0,
            unsafe_count=0,
            distinct_postmortems=0,
        )


def test_knowledge_quality_risk_thresholds_are_ordered_and_bounded() -> None:
    thresholds = CapacityGovernanceKnowledgeQualityRiskThresholds()

    assert thresholds.window_seconds == 604_800
    assert thresholds.unsafe_warning_count == 2
    assert thresholds.degraded_warning_count == 2
    with pytest.raises(ValueError, match="unsafe thresholds"):
        CapacityGovernanceKnowledgeQualityRiskThresholds(
            unsafe_warning_count=4,
            unsafe_critical_count=3,
        )
    with pytest.raises(ValueError, match="maximum snapshots"):
        CapacityGovernanceKnowledgeQualityRiskThresholds(
            unsafe_critical_count=5,
            maximum_snapshots=4,
        )


def test_persistent_unsafe_rule_binds_latest_snapshot_and_new_evidence() -> None:
    tenant_id = uuid4()
    postmortem_id = uuid4()
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    first = _quality_snapshot(
        postmortem_id=postmortem_id,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.UNSAFE,
        captured_at=now - timedelta(hours=3),
        evidence_fingerprint="1" * 64,
    )
    second = _quality_snapshot(
        postmortem_id=postmortem_id,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.UNSAFE,
        captured_at=now - timedelta(hours=2),
        evidence_fingerprint="2" * 64,
    )
    third = _quality_snapshot(
        postmortem_id=postmortem_id,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.UNSAFE,
        captured_at=now - timedelta(hours=1),
        evidence_fingerprint="3" * 64,
    )
    thresholds = CapacityGovernanceKnowledgeQualityRiskThresholds(
        unsafe_warning_count=2,
        unsafe_critical_count=3,
    )

    warning = build_persistent_unsafe_knowledge_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        snapshots=(first, second),
        now=now,
        thresholds=thresholds,
    )
    critical = build_persistent_unsafe_knowledge_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        snapshots=(first, second, third),
        now=now,
        thresholds=thresholds,
    )

    assert warning is not None
    assert critical is not None
    assert warning.severity is CapacityGovernanceIncidentSeverity.WARNING
    assert critical.severity is CapacityGovernanceIncidentSeverity.CRITICAL
    assert critical.source_id == third.id
    assert warning.fingerprint == critical.fingerprint
    assert warning.evidence_fingerprint != critical.evidence_fingerprint
    assert critical.rule_version == "knowledge-unsafe-persistent/v1"
    assert build_persistent_unsafe_knowledge_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        snapshots=(first,),
        now=now,
        thresholds=thresholds,
    ) is None
    healthy = _quality_snapshot(
        postmortem_id=postmortem_id,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.HEALTHY,
        captured_at=now,
        evidence_fingerprint="6" * 64,
    )
    assert build_persistent_unsafe_knowledge_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        snapshots=(first, second, healthy),
        now=now,
        thresholds=thresholds,
    ) is None


def test_repeated_degraded_rule_requires_independent_evidence() -> None:
    tenant_id = uuid4()
    postmortem_id = uuid4()
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    first = _quality_snapshot(
        postmortem_id=postmortem_id,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.DEGRADED,
        captured_at=now - timedelta(hours=2),
        evidence_fingerprint="4" * 64,
    )
    duplicate = _quality_snapshot(
        postmortem_id=postmortem_id,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.DEGRADED,
        captured_at=now - timedelta(hours=1),
        evidence_fingerprint="4" * 64,
    )
    independent = _quality_snapshot(
        postmortem_id=postmortem_id,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.DEGRADED,
        captured_at=now,
        evidence_fingerprint="5" * 64,
    )
    thresholds = CapacityGovernanceKnowledgeQualityRiskThresholds(
        degraded_warning_count=2,
        degraded_critical_count=3,
    )

    assert build_repeated_degraded_knowledge_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        snapshots=(first, duplicate),
        now=now,
        thresholds=thresholds,
    ) is None
    candidate = build_repeated_degraded_knowledge_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        snapshots=(first, duplicate, independent),
        now=now,
        thresholds=thresholds,
    )

    assert candidate is not None
    assert candidate.signal is CapacityGovernanceIncidentSignal.KNOWLEDGE_DEGRADED_REPEAT
    assert candidate.severity is CapacityGovernanceIncidentSeverity.WARNING
    assert candidate.source_id == independent.id
    assert candidate.details["independent_evidence_count"] == 2
    healthy = _quality_snapshot(
        postmortem_id=postmortem_id,
        assessment=CapacityGovernanceKnowledgeQualityAssessment.HEALTHY,
        captured_at=now + timedelta(minutes=1),
        evidence_fingerprint="7" * 64,
    )
    assert build_repeated_degraded_knowledge_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        snapshots=(first, independent, healthy),
        now=now + timedelta(minutes=1),
        thresholds=thresholds,
    ) is None


def test_post_recovery_requarantine_rule_requires_new_quarantine_fact() -> None:
    tenant_id = uuid4()
    postmortem_id = uuid4()
    restored_at = datetime(2026, 8, 24, 12, tzinfo=UTC)
    requarantined_at = restored_at + timedelta(hours=30)

    assert build_post_recovery_requarantine_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        postmortem_status=CapacityGovernancePostmortemStatus.PUBLISHED,
        postmortem_version=4,
        knowledge_version="postmortem-v4",
        content_fingerprint="a" * 64,
        restore_count=1,
        last_restored_at=restored_at,
        last_quarantined_at=requarantined_at,
    ) is None
    assert build_post_recovery_requarantine_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        postmortem_status=CapacityGovernancePostmortemStatus.QUARANTINED,
        postmortem_version=4,
        knowledge_version="postmortem-v4",
        content_fingerprint="a" * 64,
        restore_count=1,
        last_restored_at=restored_at,
        last_quarantined_at=restored_at,
    ) is None
    first = build_post_recovery_requarantine_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        postmortem_status=CapacityGovernancePostmortemStatus.QUARANTINED,
        postmortem_version=4,
        knowledge_version="postmortem-v4",
        content_fingerprint="a" * 64,
        restore_count=1,
        last_restored_at=restored_at,
        last_quarantined_at=requarantined_at,
    )
    repeated = build_post_recovery_requarantine_incident_candidate(
        tenant_id=tenant_id,
        handler_version="reflection-v1",
        postmortem_id=postmortem_id,
        postmortem_status=CapacityGovernancePostmortemStatus.QUARANTINED,
        postmortem_version=5,
        knowledge_version="postmortem-v5",
        content_fingerprint="b" * 64,
        restore_count=2,
        last_restored_at=requarantined_at + timedelta(hours=1),
        last_quarantined_at=requarantined_at + timedelta(hours=2),
    )

    assert first is not None
    assert repeated is not None
    assert first.severity is CapacityGovernanceIncidentSeverity.CRITICAL
    assert first.rule_version == "knowledge-requarantined/v1"
    assert first.fingerprint == repeated.fingerprint
    assert first.evidence_fingerprint != repeated.evidence_fingerprint


def test_capacity_alert_sla_uses_persisted_lifecycle_facts() -> None:
    now = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    first_seen = now - timedelta(minutes=20)

    assert assess_capacity_alert_sla(
        status=CapacityGovernanceAlertStatus.OPEN,
        first_seen_at=now - timedelta(minutes=5),
        now=now,
        response_warning_seconds=900,
        response_critical_seconds=3600,
    ).state is CapacityGovernanceAlertSLAState.WITHIN_SLA
    assert assess_capacity_alert_sla(
        status=CapacityGovernanceAlertStatus.OPEN,
        first_seen_at=first_seen,
        now=now,
        response_warning_seconds=900,
        response_critical_seconds=3600,
    ).state is CapacityGovernanceAlertSLAState.DUE
    assert assess_capacity_alert_sla(
        status=CapacityGovernanceAlertStatus.OPEN,
        first_seen_at=now - timedelta(hours=2),
        now=now,
        response_warning_seconds=900,
        response_critical_seconds=3600,
    ).state is CapacityGovernanceAlertSLAState.BREACHED
    assert assess_capacity_alert_sla(
        status=CapacityGovernanceAlertStatus.ACKNOWLEDGED,
        first_seen_at=first_seen,
        now=now,
        response_warning_seconds=900,
        response_critical_seconds=3600,
    ).state is CapacityGovernanceAlertSLAState.ACKNOWLEDGED
    assert assess_capacity_alert_sla(
        status=CapacityGovernanceAlertStatus.RESOLVED,
        first_seen_at=first_seen,
        now=now,
        response_warning_seconds=900,
        response_critical_seconds=3600,
    ).state is CapacityGovernanceAlertSLAState.RESOLVED


def test_capacity_audit_query_requires_ordered_utc_window() -> None:
    start = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)

    query = CapacityGovernanceAuditQuery(
        actor_subject="capacity-auditor",
        action="capacity.alert.acknowledge",
        outcome=CapacityGovernanceAuditOutcome.SUCCESS,
        occurred_from=start,
        occurred_to=start + timedelta(hours=1),
    )

    assert query.actor_subject == "capacity-auditor"
    with pytest.raises(ValueError, match="ordered"):
        CapacityGovernanceAuditQuery(
            occurred_from=start + timedelta(hours=1),
            occurred_to=start,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        CapacityGovernanceAuditQuery(occurred_from=start.replace(tzinfo=None))


def test_capacity_cursor_binds_kind_filters_and_actor_scope() -> None:
    item_id = uuid4()
    updated_at = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    cursor = _encode_cursor(
        kind="alert",
        updated_at=updated_at,
        item_id=item_id,
        filters={"severity": "warning", "status": "open"},
        scope_hash="a" * 64,
    )

    assert _decode_cursor(
        cursor,
        kind="alert",
        filters={"severity": "warning", "status": "open"},
        scope_hash="a" * 64,
    ) == (updated_at, item_id)
    for kind, filters, scope_hash in (
        ("request", {"severity": "warning", "status": "open"}, "a" * 64),
        ("alert", {"severity": "critical", "status": "open"}, "a" * 64),
        ("alert", {"severity": "warning", "status": "open"}, "b" * 64),
    ):
        with pytest.raises(CapacityGovernanceCursorError):
            _decode_cursor(
                cursor,
                kind=kind,
                filters=filters,
                scope_hash=scope_hash,
            )


def test_capacity_cursor_rejects_noncanonical_tampering() -> None:
    cursor = _encode_cursor(
        kind="request",
        updated_at=datetime(2026, 8, 25, 8, 0, tzinfo=UTC),
        item_id=uuid4(),
        filters={"status": None},
        scope_hash="a" * 64,
    )

    with pytest.raises(CapacityGovernanceCursorError):
        _decode_cursor(
            cursor + "=",
            kind="request",
            filters={"status": None},
            scope_hash="a" * 64,
        )
