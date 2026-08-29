from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

from public_agent.auth import APITokenCodec, PrincipalCreateRequest
from public_agent.core.types import utc_now
from public_agent.knowledge import DeterministicHashEmbeddingProvider, KnowledgeQuery
from public_agent.operations import (
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
    GOVERNANCE_KNOWLEDGE_ACCESS_TAG,
    GOVERNANCE_KNOWLEDGE_DOMAIN,
    GOVERNANCE_KNOWLEDGE_NAMESPACE,
    CapacityGovernanceAuthorizationError,
    CapacityGovernanceCursorError,
    CapacityGovernanceIncidentQuery,
    CapacityGovernanceIncidentSignal,
    CapacityGovernanceIncidentStatus,
    CapacityGovernanceIncidentThresholds,
    CapacityGovernanceKnowledgeFeedbackInput,
    CapacityGovernanceKnowledgeFeedbackQuery,
    CapacityGovernanceKnowledgeFeedbackReason,
    CapacityGovernanceKnowledgeFeedbackSignal,
    CapacityGovernanceKnowledgeFeedbackStatus,
    CapacityGovernanceKnowledgeQualityAssessment,
    CapacityGovernanceKnowledgeQualityRiskThresholds,
    CapacityGovernanceKnowledgeQualitySnapshotQuery,
    CapacityGovernanceKnowledgeQualityTrendBucket,
    CapacityGovernanceKnowledgeQualityTrendQuery,
    CapacityGovernanceKnowledgeRecoveryQuery,
    CapacityGovernanceKnowledgeRecoveryReason,
    CapacityGovernanceKnowledgeRecoveryStatus,
    CapacityGovernancePostmortemImpact,
    CapacityGovernancePostmortemInput,
    CapacityGovernancePostmortemPrevention,
    CapacityGovernancePostmortemQuery,
    CapacityGovernancePostmortemRootCause,
    CapacityGovernancePostmortemStatus,
    CapacityGovernanceRemediationStatus,
)
from public_agent.operations.capacity import ReflectionCapacityThresholds
from public_agent.operations.capacity_governance import (
    ReflectionCapacityGovernanceConflictError,
)
from public_agent.storage.auth import PostgresAPIKeyService
from public_agent.storage.capacity_control import PostgresReflectionCapacityControl
from public_agent.storage.capacity_governance import PostgresReflectionCapacityGovernance
from public_agent.storage.database import Database
from public_agent.storage.governance_knowledge import PostgresGovernanceKnowledgeRetriever
from public_agent.storage.models import (
    APIPrincipalModel,
    APITokenModel,
    AuthenticationAuditEventModel,
    ReflectionCapacityGovernanceAuditEventModel,
    ReflectionCapacityGovernanceIncidentModel,
    ReflectionCapacityGovernanceKnowledgeFeedbackModel,
    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel,
    ReflectionCapacityGovernanceKnowledgeRecoveryModel,
    ReflectionCapacityGovernancePostmortemModel,
    ReflectionCapacityGovernanceRemediationModel,
    TenantModel,
)
from public_agent.storage.outbox import REFLECTION_JOB_TYPE

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


def _thresholds() -> ReflectionCapacityThresholds:
    return ReflectionCapacityThresholds(
        stale_after_seconds=180,
        minimum_workers=1,
        maximum_workers=10,
        target_jobs_per_worker=20,
        ready_warning=100,
        ready_critical=500,
        oldest_warning_seconds=300,
        oldest_critical_seconds=1_800,
        dead_letter_warning=1,
        dead_letter_critical=10,
    )


def _incident(*, tenant_id, handler_version: str, incident_id, now):
    return ReflectionCapacityGovernanceIncidentModel(
        id=incident_id,
        tenant_id=tenant_id,
        job_type=REFLECTION_JOB_TYPE,
        handler_version=handler_version,
        signal="drill_check_failed",
        rule_version="drill-check-failed/v1",
        severity="critical",
        status="resolved",
        version=3,
        source_id=None,
        fingerprint=uuid4().hex + uuid4().hex,
        evidence_fingerprint=uuid4().hex + uuid4().hex,
        first_seen_at=now - timedelta(minutes=5),
        last_seen_at=now,
        last_evidence_at=now,
        occurrence_count=1,
        reopened_count=0,
        evidence={"check_name": "postmortem_lifecycle_constraints"},
        acknowledged_by="incident-operator",
        acknowledged_principal_id=uuid4(),
        acknowledged_token_id=uuid4(),
        acknowledged_at=now - timedelta(minutes=4),
        resolved_at=now,
    )


def _remediation(
    *,
    tenant_id,
    handler_version: str,
    incident_id,
    remediation_id,
    now,
    verified: bool,
):
    return ReflectionCapacityGovernanceRemediationModel(
        id=remediation_id,
        tenant_id=tenant_id,
        incident_id=incident_id,
        job_type=REFLECTION_JOB_TYPE,
        handler_version=handler_version,
        incident_cycle=0,
        playbook="drill_control_repair",
        status=(
            CapacityGovernanceRemediationStatus.VERIFIED.value
            if verified
            else CapacityGovernanceRemediationStatus.VERIFICATION_PENDING.value
        ),
        version=4 if verified else 3,
        requested_by="remediation-requester",
        requested_principal_id=uuid4(),
        requested_token_id=uuid4(),
        requested_at=now - timedelta(minutes=4),
        approved_by="remediation-approver",
        approved_principal_id=uuid4(),
        approved_token_id=uuid4(),
        approved_at=now - timedelta(minutes=3),
        rejected_by=None,
        rejected_principal_id=None,
        rejected_token_id=None,
        rejected_at=None,
        executed_by="remediation-executor",
        executed_principal_id=uuid4(),
        executed_token_id=uuid4(),
        executed_at=now - timedelta(minutes=2),
        execution_result="completed",
        execution_evidence="schema_control_restored",
        incident_version_at_execution=2,
        verified_by="remediation-verifier" if verified else None,
        verified_principal_id=uuid4() if verified else None,
        verified_token_id=uuid4() if verified else None,
        verified_at=now - timedelta(minutes=1) if verified else None,
    )


@pytest.mark.asyncio
async def test_verified_postmortem_review_publishes_advisory_rag_with_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(
        "postgresql+asyncpg://public_agent:public_agent@localhost:55432/public_agent"
    )
    tenant_id = uuid4()
    tenant_slug = f"capacity-postmortem-{tenant_id.hex[:10]}"
    handler_version = f"capacity-postmortem-{uuid4().hex[:8]}"
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("capacity-postmortem-test-pepper"),
    )
    embeddings = DeterministicHashEmbeddingProvider()
    control = PostgresReflectionCapacityControl(
        database.sessions,
        governance=PostgresReflectionCapacityGovernance(
            database.sessions,
            handler_version=handler_version,
        ),
        governance_tenant=tenant_slug,
        fallback_thresholds=_thresholds(),
        drift_window_seconds=900,
        drift_minimum_observations=3,
        drift_critical_observations=4,
        incident_thresholds=CapacityGovernanceIncidentThresholds(
            audit_warning_count=1_000,
            audit_critical_count=1_000,
            audit_maximum_events=1_000,
            reopen_warning_count=1_000,
            reopen_critical_count=1_000,
        ),
        knowledge_quality_risk_thresholds=(
            CapacityGovernanceKnowledgeQualityRiskThresholds(
                unsafe_warning_count=2,
                unsafe_critical_count=3,
                degraded_warning_count=2,
                degraded_critical_count=3,
            )
        ),
        knowledge_quality_maximum_trend_buckets=48,
        governance_embeddings=embeddings,
    )
    retriever = PostgresGovernanceKnowledgeRetriever(database.sessions, embeddings)

    async def actor(subject: str, permissions: tuple[str, ...]):
        principal = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject=subject,
                display_name=subject,
                permissions=permissions,
                all_agents=True,
            )
        )
        issued = await auth.issue_token(
            principal_id=principal.id,
            tenant_id=tenant_slug,
            label=subject,
        )
        return await auth.authenticate(issued.token.get_secret_value())

    now = utc_now()
    incident_id = uuid4()
    remediation_id = uuid4()
    stale_incident_id = uuid4()
    stale_remediation_id = uuid4()
    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Postmortem"))
            await session.flush()
            session.add_all(
                (
                    _incident(
                        tenant_id=tenant_id,
                        handler_version=handler_version,
                        incident_id=incident_id,
                        now=now,
                    ),
                    _incident(
                        tenant_id=tenant_id,
                        handler_version=handler_version,
                        incident_id=stale_incident_id,
                        now=now,
                    ),
                )
            )
            session.add_all(
                (
                    _remediation(
                        tenant_id=tenant_id,
                        handler_version=handler_version,
                        incident_id=incident_id,
                        remediation_id=remediation_id,
                        now=now,
                        verified=False,
                    ),
                    _remediation(
                        tenant_id=tenant_id,
                        handler_version=handler_version,
                        incident_id=stale_incident_id,
                        remediation_id=stale_remediation_id,
                        now=now,
                        verified=True,
                    ),
                )
            )
        requester = await actor(
            "postmortem-requester",
            (
                CAPACITY_POSTMORTEMS_READ,
                CAPACITY_POSTMORTEMS_REQUEST,
                CAPACITY_POSTMORTEMS_REVIEW,
            ),
        )
        reviewer = await actor(
            "postmortem-reviewer",
            (CAPACITY_POSTMORTEMS_READ, CAPACITY_POSTMORTEMS_REVIEW),
        )
        feedback_reporter = await actor(
            "knowledge-feedback-reporter",
            (
                CAPACITY_KNOWLEDGE_FEEDBACK_READ,
                CAPACITY_KNOWLEDGE_FEEDBACK_REPORT,
                CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
                CAPACITY_KNOWLEDGE_RECOVERY_READ,
                CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
            ),
        )
        feedback_reviewer = await actor(
            "knowledge-feedback-reviewer",
            (
                CAPACITY_KNOWLEDGE_FEEDBACK_READ,
                CAPACITY_KNOWLEDGE_FEEDBACK_REVIEW,
                CAPACITY_KNOWLEDGE_RECOVERY_READ,
                CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
            ),
        )
        secondary_feedback_reporter = await actor(
            "knowledge-feedback-secondary-reporter",
            (CAPACITY_KNOWLEDGE_FEEDBACK_READ, CAPACITY_KNOWLEDGE_FEEDBACK_REPORT),
        )
        quality_assessor = await actor(
            "knowledge-quality-assessor",
            (CAPACITY_KNOWLEDGE_QUALITY_READ, CAPACITY_KNOWLEDGE_QUALITY_ASSESS),
        )
        incident_operator = await actor(
            "knowledge-risk-incident-operator",
            (CAPACITY_INCIDENTS_READ, CAPACITY_INCIDENTS_MANAGE),
        )
        recovery_requester = await actor(
            "knowledge-recovery-requester",
            (
                CAPACITY_KNOWLEDGE_RECOVERY_READ,
                CAPACITY_KNOWLEDGE_RECOVERY_REQUEST,
                CAPACITY_KNOWLEDGE_RECOVERY_REVIEW,
            ),
        )
        recovery_reviewer = await actor(
            "knowledge-recovery-reviewer",
            (CAPACITY_KNOWLEDGE_RECOVERY_READ, CAPACITY_KNOWLEDGE_RECOVERY_REVIEW),
        )
        content = CapacityGovernancePostmortemInput(
            root_cause=CapacityGovernancePostmortemRootCause.SCHEMA_CONTROL_GAP,
            impact=CapacityGovernancePostmortemImpact.NO_EXTERNAL_IMPACT,
            prevention=CapacityGovernancePostmortemPrevention.SCHEMA_VERIFICATION,
            summary="只读演练发现治理约束缺口, 修复后由更新的恢复事实验证通过。",
        )

        with pytest.raises(ReflectionCapacityGovernanceConflictError, match="verified"):
            await control.create_postmortem(
                remediation_id=remediation_id,
                expected_remediation_version=3,
                content=content,
                actor=requester,
            )
        async with database.sessions() as session, session.begin():
            row = await session.get(ReflectionCapacityGovernanceRemediationModel, remediation_id)
            assert row is not None
            row.status = CapacityGovernanceRemediationStatus.VERIFIED.value
            row.version = 4
            row.verified_by = "remediation-verifier"
            row.verified_principal_id = uuid4()
            row.verified_token_id = uuid4()
            row.verified_at = now - timedelta(minutes=1)

        requested = await control.create_postmortem(
            remediation_id=remediation_id,
            expected_remediation_version=4,
            content=content,
            actor=requester,
        )
        assert requested.status is CapacityGovernancePostmortemStatus.AWAITING_REVIEW
        with pytest.raises(ReflectionCapacityGovernanceConflictError, match="already exists"):
            await control.create_postmortem(
                remediation_id=remediation_id,
                expected_remediation_version=4,
                content=content,
                actor=requester,
            )
        with pytest.raises(CapacityGovernanceAuthorizationError, match="requester"):
            await control.approve_postmortem(
                postmortem_id=requested.id,
                expected_version=requested.version,
                actor=requester,
            )

        published = await control.approve_postmortem(
            postmortem_id=requested.id,
            expected_version=requested.version,
            actor=reviewer,
        )
        assert published.status is CapacityGovernancePostmortemStatus.PUBLISHED
        assert published.knowledge_namespace == GOVERNANCE_KNOWLEDGE_NAMESPACE
        assert published.published_at is not None
        page = await control.list_postmortems(
            CapacityGovernancePostmortemQuery(limit=1),
            actor=requester,
        )
        assert page.items == (published,)

        no_tag = await retriever.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_slug,
                agent_id="operations-agent",
                domain_id=GOVERNANCE_KNOWLEDGE_DOMAIN,
                namespace=GOVERNANCE_KNOWLEDGE_NAMESPACE,
                text="治理约束缺口如何预防",
                limit=5,
            )
        )
        assert no_tag == ()
        hits = await retriever.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_slug,
                agent_id="operations-agent",
                domain_id=GOVERNANCE_KNOWLEDGE_DOMAIN,
                namespace=GOVERNANCE_KNOWLEDGE_NAMESPACE,
                text="治理约束缺口如何预防",
                limit=5,
                access_tags=(GOVERNANCE_KNOWLEDGE_ACCESS_TAG,),
            )
        )
        assert len(hits) == 1
        assert hits[0].metadata["incident_id"] == str(incident_id)
        assert hits[0].metadata["remediation_id"] == str(remediation_id)
        assert hits[0].metadata["content_fingerprint"] == published.content_fingerprint
        assert hits[0].metadata["advisory_only"] is True
        assert hits[0].metadata["authorization_source"] is False
        assert hits[0].metadata["recovery_evidence"] is False
        assert hits[0].metadata["execution_instruction"] is False

        assert published.knowledge_version is not None
        feedback = await control.report_knowledge_feedback(
            postmortem_id=published.id,
            expected_postmortem_version=published.version,
            expected_knowledge_version=published.knowledge_version,
            expected_content_fingerprint=published.content_fingerprint,
            content=CapacityGovernanceKnowledgeFeedbackInput(
                signal=CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN,
                reason=CapacityGovernanceKnowledgeFeedbackReason.UNSAFE_CONTENT,
            ),
            actor=feedback_reporter,
        )
        assert feedback.status is CapacityGovernanceKnowledgeFeedbackStatus.AWAITING_REVIEW
        with pytest.raises(ReflectionCapacityGovernanceConflictError, match="already submitted"):
            await control.report_knowledge_feedback(
                postmortem_id=published.id,
                expected_postmortem_version=published.version,
                expected_knowledge_version=published.knowledge_version,
                expected_content_fingerprint=published.content_fingerprint,
                content=CapacityGovernanceKnowledgeFeedbackInput(
                    signal=CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN,
                    reason=CapacityGovernanceKnowledgeFeedbackReason.UNSAFE_CONTENT,
                ),
                actor=feedback_reporter,
            )
        feedback_page = await control.list_knowledge_feedback(
            CapacityGovernanceKnowledgeFeedbackQuery(limit=1),
            actor=feedback_reporter,
        )
        assert feedback_page.items == (feedback,)
        assert "reported_token_id" not in feedback.model_dump(mode="json")
        with pytest.raises(CapacityGovernanceAuthorizationError, match="reporter"):
            await control.confirm_knowledge_feedback(
                feedback_id=feedback.id,
                expected_version=feedback.version,
                actor=feedback_reporter,
            )
        secondary_feedback = await control.report_knowledge_feedback(
            postmortem_id=published.id,
            expected_postmortem_version=published.version,
            expected_knowledge_version=published.knowledge_version,
            expected_content_fingerprint=published.content_fingerprint,
            content=CapacityGovernanceKnowledgeFeedbackInput(
                signal=CapacityGovernanceKnowledgeFeedbackSignal.HELPFUL,
                reason=CapacityGovernanceKnowledgeFeedbackReason.RELEVANCE,
            ),
            actor=secondary_feedback_reporter,
        )
        append_audit = control._append_audit

        def fail_confirm_audit(*args, action, **kwargs):
            if action == "capacity.knowledge_feedback.confirm":
                raise RuntimeError("forced feedback audit failure")
            return append_audit(*args, action=action, **kwargs)

        monkeypatch.setattr(control, "_append_audit", fail_confirm_audit)
        with pytest.raises(RuntimeError, match="forced feedback audit failure"):
            await control.confirm_knowledge_feedback(
                feedback_id=feedback.id,
                expected_version=feedback.version,
                actor=feedback_reviewer,
            )
        async with database.sessions() as session:
            rolled_back_feedback = await session.get(
                ReflectionCapacityGovernanceKnowledgeFeedbackModel,
                feedback.id,
            )
            rolled_back_postmortem = await session.get(
                ReflectionCapacityGovernancePostmortemModel,
                published.id,
            )
        assert rolled_back_feedback is not None
        assert rolled_back_feedback.status == feedback.status.value
        assert rolled_back_postmortem is not None
        assert rolled_back_postmortem.status == published.status.value
        assert len(
            await retriever.retrieve(
                KnowledgeQuery(
                    tenant_id=tenant_slug,
                    agent_id="operations-agent",
                    domain_id=GOVERNANCE_KNOWLEDGE_DOMAIN,
                    namespace=GOVERNANCE_KNOWLEDGE_NAMESPACE,
                    text="治理约束缺口如何预防",
                    limit=5,
                    access_tags=(GOVERNANCE_KNOWLEDGE_ACCESS_TAG,),
                )
            )
        ) == 1
        monkeypatch.setattr(control, "_append_audit", append_audit)
        confirmed = await control.confirm_knowledge_feedback(
            feedback_id=feedback.id,
            expected_version=feedback.version,
            actor=feedback_reviewer,
        )
        assert confirmed.status is CapacityGovernanceKnowledgeFeedbackStatus.CONFIRMED
        superseded_page = await control.list_knowledge_feedback(
            CapacityGovernanceKnowledgeFeedbackQuery(
                status=CapacityGovernanceKnowledgeFeedbackStatus.SUPERSEDED,
                postmortem_id=published.id,
            ),
            actor=feedback_reviewer,
        )
        assert len(superseded_page.items) == 1
        assert superseded_page.items[0].id == secondary_feedback.id
        assert superseded_page.items[0].version == secondary_feedback.version + 1
        assert superseded_page.items[0].reviewed_by is None
        with pytest.raises(ReflectionCapacityGovernanceConflictError, match="changed"):
            await control.dismiss_knowledge_feedback(
                feedback_id=secondary_feedback.id,
                expected_version=secondary_feedback.version + 1,
                actor=feedback_reviewer,
            )
        quarantined = await control.get_postmortem(published.id, actor=requester)
        assert quarantined.status is CapacityGovernancePostmortemStatus.QUARANTINED
        assert quarantined.last_quarantined_at is not None
        assert quarantined.quarantine_feedback_id == feedback.id
        assert await retriever.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_slug,
                agent_id="operations-agent",
                domain_id=GOVERNANCE_KNOWLEDGE_DOMAIN,
                namespace=GOVERNANCE_KNOWLEDGE_NAMESPACE,
                text="治理约束缺口如何预防",
                limit=5,
                access_tags=(GOVERNANCE_KNOWLEDGE_ACCESS_TAG,),
            )
        ) == ()

        snapshot = await control.capture_knowledge_quality_snapshot(
            postmortem_id=quarantined.id,
            expected_postmortem_version=quarantined.version,
            actor=quality_assessor,
        )
        assert snapshot.assessment is CapacityGovernanceKnowledgeQualityAssessment.UNSAFE
        assert snapshot.confirmed_safety_count == 1
        assert snapshot.superseded_count == 1
        assert (
            await control.capture_knowledge_quality_snapshot(
                postmortem_id=quarantined.id,
                expected_postmortem_version=quarantined.version,
                actor=quality_assessor,
            )
        ).id == snapshot.id
        snapshot_page = await control.list_knowledge_quality_snapshots(
            CapacityGovernanceKnowledgeQualitySnapshotQuery(postmortem_id=quarantined.id),
            actor=quality_assessor,
        )
        assert snapshot_page.items == (snapshot,)
        with pytest.raises(DBAPIError, match="append-only"):
            async with database.sessions() as session, session.begin():
                await session.execute(
                    update(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel)
                    .where(
                        ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.id
                        == snapshot.id
                    )
                    .values(
                        assessment=CapacityGovernanceKnowledgeQualityAssessment.DEGRADED.value
                    )
                )
        async with database.sessions() as session:
            immutable_snapshot = await session.get(
                ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel,
                snapshot.id,
            )
        assert immutable_snapshot is not None
        assert (
            immutable_snapshot.assessment
            == CapacityGovernanceKnowledgeQualityAssessment.UNSAFE.value
        )

        with pytest.raises(ReflectionCapacityGovernanceConflictError, match="retention"):
            await control.request_knowledge_recovery(
                postmortem_id=quarantined.id,
                expected_postmortem_version=quarantined.version,
                snapshot_id=snapshot.id,
                reason=CapacityGovernanceKnowledgeRecoveryReason.FALSE_POSITIVE,
                actor=recovery_requester,
            )
        async with database.sessions() as session, session.begin():
            quarantined_row = await session.get(
                ReflectionCapacityGovernancePostmortemModel,
                quarantined.id,
            )
            assert quarantined_row is not None
            quarantined_row.last_quarantined_at = utc_now() - timedelta(hours=25)

        with pytest.raises(
            ReflectionCapacityGovernanceConflictError,
            match="current quarantined version",
        ):
            await control.request_knowledge_recovery(
                postmortem_id=quarantined.id,
                expected_postmortem_version=quarantined.version - 1,
                snapshot_id=snapshot.id,
                reason=CapacityGovernanceKnowledgeRecoveryReason.FALSE_POSITIVE,
                actor=recovery_requester,
            )
        async with database.sessions() as session, session.begin():
            await session.execute(
                update(ReflectionCapacityGovernanceKnowledgeFeedbackModel)
                .where(
                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.id
                    == secondary_feedback.id
                )
                .values(version=secondary_feedback.version + 2)
            )
        with pytest.raises(
            ReflectionCapacityGovernanceConflictError,
            match="changed after the recovery snapshot",
        ):
            await control.request_knowledge_recovery(
                postmortem_id=quarantined.id,
                expected_postmortem_version=quarantined.version,
                snapshot_id=snapshot.id,
                reason=CapacityGovernanceKnowledgeRecoveryReason.FALSE_POSITIVE,
                actor=recovery_requester,
            )
        fresh_snapshot = await control.capture_knowledge_quality_snapshot(
            postmortem_id=quarantined.id,
            expected_postmortem_version=quarantined.version,
            actor=quality_assessor,
        )
        assert fresh_snapshot.id != snapshot.id

        trend_to = utc_now().replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
        trend_query = CapacityGovernanceKnowledgeQualityTrendQuery(
            bucket=CapacityGovernanceKnowledgeQualityTrendBucket.HOUR,
            captured_from=trend_to - timedelta(hours=4),
            captured_to=trend_to,
            limit=2,
        )
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.knowledge_quality_trend(trend_query, actor=requester)
        first_trend = await control.knowledge_quality_trend(
            trend_query,
            actor=quality_assessor,
        )
        assert len(first_trend.points) == 2
        assert first_trend.next_cursor is not None
        assert sum(point.unsafe_count for point in first_trend.points) == 2
        second_trend = await control.knowledge_quality_trend(
            trend_query.model_copy(update={"cursor": first_trend.next_cursor}),
            actor=quality_assessor,
        )
        assert len(second_trend.points) == 2
        assert sum(point.total_snapshots for point in second_trend.points) == 0
        with pytest.raises(CapacityGovernanceCursorError):
            await control.knowledge_quality_trend(
                trend_query.model_copy(
                    update={
                        "assessment": CapacityGovernanceKnowledgeQualityAssessment.UNSAFE,
                        "cursor": first_trend.next_cursor,
                    }
                ),
                actor=quality_assessor,
            )

        def fail_risk_scan_audit(*args, action, **kwargs):
            if action == "capacity.incident.scan":
                raise RuntimeError("forced quality risk scan audit failure")
            return append_audit(*args, action=action, **kwargs)

        monkeypatch.setattr(control, "_append_audit", fail_risk_scan_audit)
        with pytest.raises(RuntimeError, match="quality risk scan audit failure"):
            await control.scan_incidents(actor=incident_operator)
        monkeypatch.setattr(control, "_append_audit", append_audit)
        rolled_back_risks = await control.list_incidents(
            CapacityGovernanceIncidentQuery(
                signal=(
                    CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT
                )
            ),
            actor=incident_operator,
        )
        assert rolled_back_risks.items == ()
        assert (
            await control.list_knowledge_quality_snapshots(
                CapacityGovernanceKnowledgeQualitySnapshotQuery(
                    postmortem_id=quarantined.id
                ),
                actor=quality_assessor,
            )
        ).items == (fresh_snapshot, snapshot)

        opened_risk = await control.scan_incidents(actor=incident_operator)
        assert opened_risk.opened_incidents == 1
        assert opened_risk.scanned_quality_snapshots == 2
        repeated_risk = await control.scan_incidents(actor=incident_operator)
        assert repeated_risk.opened_incidents == 0
        assert repeated_risk.updated_incidents == 0
        unsafe_incidents = await control.list_incidents(
            CapacityGovernanceIncidentQuery(
                signal=(
                    CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT
                ),
                status=CapacityGovernanceIncidentStatus.OPEN,
            ),
            actor=incident_operator,
        )
        assert len(unsafe_incidents.items) == 1
        unsafe_incident = unsafe_incidents.items[0]
        assert unsafe_incident.source_id == fresh_snapshot.id

        recovery = await control.request_knowledge_recovery(
            postmortem_id=quarantined.id,
            expected_postmortem_version=quarantined.version,
            snapshot_id=fresh_snapshot.id,
            reason=CapacityGovernanceKnowledgeRecoveryReason.FALSE_POSITIVE,
            actor=recovery_requester,
        )
        assert recovery.status is CapacityGovernanceKnowledgeRecoveryStatus.AWAITING_REVIEW
        with pytest.raises(ReflectionCapacityGovernanceConflictError, match="already awaits"):
            await control.request_knowledge_recovery(
                postmortem_id=quarantined.id,
                expected_postmortem_version=quarantined.version,
                snapshot_id=fresh_snapshot.id,
                reason=CapacityGovernanceKnowledgeRecoveryReason.FALSE_POSITIVE,
                actor=recovery_requester,
            )
        recovery_page = await control.list_knowledge_recoveries(
            CapacityGovernanceKnowledgeRecoveryQuery(postmortem_id=quarantined.id),
            actor=recovery_requester,
        )
        assert recovery_page.items == (recovery,)
        with pytest.raises(CapacityGovernanceAuthorizationError, match="requester"):
            await control.approve_knowledge_recovery(
                recovery_id=recovery.id,
                expected_version=recovery.version,
                actor=recovery_requester,
            )
        with pytest.raises(CapacityGovernanceAuthorizationError, match="feedback reviewer"):
            await control.approve_knowledge_recovery(
                recovery_id=recovery.id,
                expected_version=recovery.version,
                actor=feedback_reviewer,
            )
        with pytest.raises(CapacityGovernanceAuthorizationError, match="feedback reporter"):
            await control.approve_knowledge_recovery(
                recovery_id=recovery.id,
                expected_version=recovery.version,
                actor=feedback_reporter,
            )

        def fail_recovery_audit(*args, action, **kwargs):
            if action == "capacity.knowledge_recovery.approve":
                raise RuntimeError("forced recovery audit failure")
            return append_audit(*args, action=action, **kwargs)

        monkeypatch.setattr(control, "_append_audit", fail_recovery_audit)
        with pytest.raises(RuntimeError, match="forced recovery audit failure"):
            await control.approve_knowledge_recovery(
                recovery_id=recovery.id,
                expected_version=recovery.version,
                actor=recovery_reviewer,
            )
        async with database.sessions() as session:
            rolled_back_recovery = await session.get(
                ReflectionCapacityGovernanceKnowledgeRecoveryModel,
                recovery.id,
            )
            rolled_back_quarantine = await session.get(
                ReflectionCapacityGovernancePostmortemModel,
                quarantined.id,
            )
        assert rolled_back_recovery is not None
        assert (
            rolled_back_recovery.status
            == CapacityGovernanceKnowledgeRecoveryStatus.AWAITING_REVIEW.value
        )
        assert rolled_back_recovery.version == recovery.version
        assert rolled_back_recovery.restored_knowledge_version is None
        assert rolled_back_quarantine is not None
        assert rolled_back_quarantine.status == quarantined.status.value
        assert rolled_back_quarantine.version == quarantined.version
        assert rolled_back_quarantine.restore_count == 0
        monkeypatch.setattr(control, "_append_audit", append_audit)

        approved_recovery = await control.approve_knowledge_recovery(
            recovery_id=recovery.id,
            expected_version=recovery.version,
            actor=recovery_reviewer,
        )
        assert approved_recovery.status is CapacityGovernanceKnowledgeRecoveryStatus.APPROVED
        restored = await control.get_postmortem(published.id, actor=requester)
        assert restored.status is CapacityGovernancePostmortemStatus.PUBLISHED
        assert restored.version == quarantined.version + 1
        assert restored.restore_count == 1
        assert restored.last_restored_at is not None
        assert restored.knowledge_version != quarantined.knowledge_version
        restored_hits = await retriever.retrieve(
            KnowledgeQuery(
                tenant_id=tenant_slug,
                agent_id="operations-agent",
                domain_id=GOVERNANCE_KNOWLEDGE_DOMAIN,
                namespace=GOVERNANCE_KNOWLEDGE_NAMESPACE,
                text="治理约束缺口如何预防",
                limit=5,
                access_tags=(GOVERNANCE_KNOWLEDGE_ACCESS_TAG,),
            )
        )
        assert len(restored_hits) == 1
        assert restored_hits[0].version == restored.knowledge_version
        assert restored.knowledge_version is not None
        restored_feedback = await control.report_knowledge_feedback(
            postmortem_id=restored.id,
            expected_postmortem_version=restored.version,
            expected_knowledge_version=restored.knowledge_version,
            expected_content_fingerprint=restored.content_fingerprint,
            content=CapacityGovernanceKnowledgeFeedbackInput(
                signal=CapacityGovernanceKnowledgeFeedbackSignal.HELPFUL,
                reason=CapacityGovernanceKnowledgeFeedbackReason.RELEVANCE,
            ),
            actor=secondary_feedback_reporter,
        )
        assert restored_feedback.postmortem_version == restored.version
        confirmed_restored_feedback = await control.confirm_knowledge_feedback(
            feedback_id=restored_feedback.id,
            expected_version=restored_feedback.version,
            actor=feedback_reviewer,
        )
        assert confirmed_restored_feedback.status is (
            CapacityGovernanceKnowledgeFeedbackStatus.CONFIRMED
        )
        healthy_snapshot = await control.capture_knowledge_quality_snapshot(
            postmortem_id=restored.id,
            expected_postmortem_version=restored.version,
            actor=quality_assessor,
        )
        assert healthy_snapshot.assessment is (
            CapacityGovernanceKnowledgeQualityAssessment.HEALTHY
        )
        resolved_risk = await control.scan_incidents(actor=incident_operator)
        assert resolved_risk.resolved_incidents >= 1
        resolved_unsafe = await control.list_incidents(
            CapacityGovernanceIncidentQuery(
                signal=(
                    CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT
                ),
                status=CapacityGovernanceIncidentStatus.RESOLVED,
            ),
            actor=incident_operator,
        )
        assert len(resolved_unsafe.items) == 1

        recurrent_feedback = await control.report_knowledge_feedback(
            postmortem_id=restored.id,
            expected_postmortem_version=restored.version,
            expected_knowledge_version=restored.knowledge_version,
            expected_content_fingerprint=restored.content_fingerprint,
            content=CapacityGovernanceKnowledgeFeedbackInput(
                signal=CapacityGovernanceKnowledgeFeedbackSignal.SAFETY_CONCERN,
                reason=CapacityGovernanceKnowledgeFeedbackReason.UNSAFE_CONTENT,
            ),
            actor=feedback_reporter,
        )
        await control.confirm_knowledge_feedback(
            feedback_id=recurrent_feedback.id,
            expected_version=recurrent_feedback.version,
            actor=feedback_reviewer,
        )
        requarantined = await control.get_postmortem(restored.id, actor=requester)
        assert requarantined.status is CapacityGovernancePostmortemStatus.QUARANTINED
        assert requarantined.restore_count == 1
        recurrent_unsafe_snapshot = await control.capture_knowledge_quality_snapshot(
            postmortem_id=requarantined.id,
            expected_postmortem_version=requarantined.version,
            actor=quality_assessor,
        )
        assert recurrent_unsafe_snapshot.assessment is (
            CapacityGovernanceKnowledgeQualityAssessment.UNSAFE
        )
        recurrent_scan = await control.scan_incidents(actor=incident_operator)
        assert recurrent_scan.opened_incidents == 1
        assert recurrent_scan.updated_incidents >= 1
        requarantine_incidents = await control.list_incidents(
            CapacityGovernanceIncidentQuery(
                signal=CapacityGovernanceIncidentSignal.KNOWLEDGE_REQUARANTINED,
                status=CapacityGovernanceIncidentStatus.OPEN,
            ),
            actor=incident_operator,
        )
        assert len(requarantine_incidents.items) == 1
        reopened_unsafe = await control.list_incidents(
            CapacityGovernanceIncidentQuery(
                signal=(
                    CapacityGovernanceIncidentSignal.KNOWLEDGE_UNSAFE_PERSISTENT
                ),
                status=CapacityGovernanceIncidentStatus.OPEN,
            ),
            actor=incident_operator,
        )
        assert len(reopened_unsafe.items) == 1
        assert reopened_unsafe.items[0].reopened_count == 1

        degraded_at = utc_now()
        async with database.sessions() as session, session.begin():
            session.add_all(
                (
                    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        postmortem_id=requarantined.id,
                        job_type=REFLECTION_JOB_TYPE,
                        handler_version=handler_version,
                        postmortem_version=requarantined.version,
                        knowledge_version=requarantined.knowledge_version,
                        content_fingerprint=requarantined.content_fingerprint,
                        evidence_fingerprint="d" * 64,
                        assessment=(
                            CapacityGovernanceKnowledgeQualityAssessment.DEGRADED.value
                        ),
                        total_feedback=1,
                        awaiting_review_count=0,
                        confirmed_helpful_count=0,
                        confirmed_not_helpful_count=1,
                        confirmed_safety_count=0,
                        dismissed_count=0,
                        superseded_count=0,
                        captured_by="quality-assessor",
                        captured_principal_id=uuid4(),
                        captured_token_id=uuid4(),
                        captured_at=degraded_at,
                    ),
                    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        postmortem_id=requarantined.id,
                        job_type=REFLECTION_JOB_TYPE,
                        handler_version=handler_version,
                        postmortem_version=requarantined.version,
                        knowledge_version=requarantined.knowledge_version,
                        content_fingerprint=requarantined.content_fingerprint,
                        evidence_fingerprint="e" * 64,
                        assessment=(
                            CapacityGovernanceKnowledgeQualityAssessment.DEGRADED.value
                        ),
                        total_feedback=1,
                        awaiting_review_count=0,
                        confirmed_helpful_count=0,
                        confirmed_not_helpful_count=1,
                        confirmed_safety_count=0,
                        dismissed_count=0,
                        superseded_count=0,
                        captured_by="quality-assessor",
                        captured_principal_id=uuid4(),
                        captured_token_id=uuid4(),
                        captured_at=degraded_at + timedelta(microseconds=1),
                    ),
                )
            )
        degraded_scan = await control.scan_incidents(actor=incident_operator)
        assert degraded_scan.opened_incidents >= 1
        assert degraded_scan.resolved_incidents >= 1
        degraded_incidents = await control.list_incidents(
            CapacityGovernanceIncidentQuery(
                signal=CapacityGovernanceIncidentSignal.KNOWLEDGE_DEGRADED_REPEAT,
                status=CapacityGovernanceIncidentStatus.OPEN,
            ),
            actor=incident_operator,
        )
        assert len(degraded_incidents.items) == 1

        stale = await control.create_postmortem(
            remediation_id=stale_remediation_id,
            expected_remediation_version=4,
            content=content,
            actor=requester,
        )
        async with database.sessions() as session, session.begin():
            await session.execute(
                update(ReflectionCapacityGovernanceIncidentModel)
                .where(ReflectionCapacityGovernanceIncidentModel.id == stale_incident_id)
                .values(version=4)
            )
        with pytest.raises(ReflectionCapacityGovernanceConflictError, match="source facts"):
            await control.approve_postmortem(
                postmortem_id=stale.id,
                expected_version=stale.version,
                actor=reviewer,
            )
        async with database.sessions() as session:
            stale_row = await session.get(ReflectionCapacityGovernancePostmortemModel, stale.id)
            assert stale_row is not None
            assert stale_row.status == CapacityGovernancePostmortemStatus.AWAITING_REVIEW.value
            assert stale_row.embedding is None
            audit_actions = set(
                await session.scalars(
                    select(ReflectionCapacityGovernanceAuditEventModel.action).where(
                        ReflectionCapacityGovernanceAuditEventModel.handler_version
                        == handler_version
                    )
                )
            )
        assert "capacity.postmortem.request" in audit_actions
        assert "capacity.postmortem.publish" in audit_actions
        assert "capacity.knowledge_feedback.report" in audit_actions
        assert "capacity.knowledge_feedback.confirm" in audit_actions
        assert "capacity.knowledge_quality.capture" in audit_actions
        assert "capacity.knowledge_recovery.request" in audit_actions
        assert "capacity.knowledge_recovery.approve" in audit_actions
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(ReflectionCapacityGovernanceAuditEventModel).where(
                    ReflectionCapacityGovernanceAuditEventModel.handler_version == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityGovernanceKnowledgeRecoveryModel).where(
                    ReflectionCapacityGovernanceKnowledgeRecoveryModel.handler_version
                    == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel).where(
                    ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel.handler_version
                    == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityGovernanceKnowledgeFeedbackModel).where(
                    ReflectionCapacityGovernanceKnowledgeFeedbackModel.handler_version
                    == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityGovernancePostmortemModel).where(
                    ReflectionCapacityGovernancePostmortemModel.handler_version == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityGovernanceRemediationModel).where(
                    ReflectionCapacityGovernanceRemediationModel.handler_version == handler_version
                )
            )
            await session.execute(
                delete(ReflectionCapacityGovernanceIncidentModel).where(
                    ReflectionCapacityGovernanceIncidentModel.handler_version == handler_version
                )
            )
            await session.execute(
                delete(AuthenticationAuditEventModel).where(
                    AuthenticationAuditEventModel.tenant_id == tenant_id
                )
            )
            await session.execute(delete(APITokenModel).where(APITokenModel.tenant_id == tenant_id))
            await session.execute(
                delete(APIPrincipalModel).where(APIPrincipalModel.tenant_id == tenant_id)
            )
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        await database.dispose()
