from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

from public_agent.auth import APITokenCodec, PrincipalCreateRequest
from public_agent.core.types import utc_now
from public_agent.operations import (
    CAPACITY_ALERTS_MANAGE,
    CAPACITY_ALERTS_READ,
    CAPACITY_AUDIT_READ,
    CAPACITY_GOVERNANCE_READ,
    CAPACITY_INCIDENTS_MANAGE,
    CAPACITY_INCIDENTS_READ,
    CAPACITY_REMEDIATIONS_APPROVE,
    CAPACITY_REMEDIATIONS_EXECUTE,
    CAPACITY_REMEDIATIONS_READ,
    CAPACITY_REMEDIATIONS_REQUEST,
    CAPACITY_REMEDIATIONS_VERIFY,
    CapacityGovernanceAlertSeverity,
    CapacityGovernanceAlertStatus,
    CapacityGovernanceAuditOutcome,
    CapacityGovernanceAuditQuery,
    CapacityGovernanceAuthorizationError,
    CapacityGovernanceCursorError,
    CapacityGovernanceDrillCheck,
    CapacityGovernanceDrillReport,
    CapacityGovernanceIncidentQuery,
    CapacityGovernanceIncidentSignal,
    CapacityGovernanceIncidentStatus,
    CapacityGovernanceIncidentThresholds,
    CapacityGovernanceRemediationEvidence,
    CapacityGovernanceRemediationExecutionResult,
    CapacityGovernanceRemediationPlaybook,
    CapacityGovernanceRemediationQuery,
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
from public_agent.storage.models import (
    AgentModel,
    APIPrincipalAgentGrantModel,
    APIPrincipalModel,
    APITokenModel,
    AuthenticationAuditEventModel,
    ReflectionCapacityChangeRequestModel,
    ReflectionCapacityGovernanceAlertModel,
    ReflectionCapacityGovernanceAuditEventModel,
    ReflectionCapacityGovernanceIncidentModel,
    ReflectionCapacityGovernanceRemediationModel,
    ReflectionCapacityObservationModel,
    ReflectionCapacityPolicyModel,
    TenantModel,
)
from public_agent.storage.outbox import REFLECTION_JOB_TYPE

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


def _thresholds(*, ready_warning: int = 100) -> ReflectionCapacityThresholds:
    return ReflectionCapacityThresholds(
        stale_after_seconds=180,
        minimum_workers=1,
        maximum_workers=10,
        target_jobs_per_worker=20,
        ready_warning=ready_warning,
        ready_critical=500,
        oldest_warning_seconds=300,
        oldest_critical_seconds=1_800,
        dead_letter_warning=1,
        dead_letter_critical=10,
    )


class _FailedDrillCapacityControl(PostgresReflectionCapacityControl):
    async def _incident_drill_report(self, _session, *, checked_at):
        return CapacityGovernanceDrillReport(
            passed=False,
            checks=(
                CapacityGovernanceDrillCheck(
                    name="audit_append_only",
                    passed=False,
                    detail="The required append-only control is absent.",
                ),
            ),
            checked_at=checked_at,
        )


@pytest.mark.asyncio
async def test_capacity_control_revalidates_revoked_token_and_global_scope() -> None:
    database = Database("postgresql+asyncpg://public_agent:public_agent@localhost:55432/public_agent")
    tenant_id = uuid4()
    tenant_slug = f"capacity-control-{tenant_id.hex[:10]}"
    handler_version = f"capacity-control-{uuid4().hex[:8]}"
    agent_id = uuid4()
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("capacity-control-test-pepper"),
    )
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
    )
    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Capacity Control"))
            await session.flush()
            session.add(
                AgentModel(
                    id=agent_id,
                    tenant_id=tenant_id,
                    agent_key=f"capacity-agent-{agent_id.hex[:8]}",
                    name="Capacity Agent",
                    domain_id="capacity",
                )
            )
        global_principal = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="capacity-global-reader",
                display_name="Capacity Global Reader",
                permissions=(CAPACITY_GOVERNANCE_READ,),
                all_agents=True,
            )
        )
        global_token = await auth.issue_token(
            principal_id=global_principal.id,
            tenant_id=tenant_slug,
            label="global-reader",
        )
        actor = await auth.authenticate(global_token.token.get_secret_value())
        assert (await control.summary(actor=actor)).handler_version == handler_version

        scoped_principal = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="capacity-scoped-reader",
                display_name="Capacity Scoped Reader",
                permissions=(CAPACITY_GOVERNANCE_READ,),
                agent_ids=(f"capacity-agent-{agent_id.hex[:8]}",),
            )
        )
        scoped_token = await auth.issue_token(
            principal_id=scoped_principal.id,
            tenant_id=tenant_slug,
            label="scoped-reader",
        )
        scoped_actor = await auth.authenticate(scoped_token.token.get_secret_value())
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.summary(actor=scoped_actor)

        await auth.revoke_token(token_id=global_token.id, tenant_id=tenant_slug)
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.summary(actor=actor)
    finally:
        await _cleanup(database, tenant_id=tenant_id, handler_version=handler_version)


@pytest.mark.asyncio
async def test_capacity_audit_query_is_filtered_redacted_and_drill_is_read_only() -> None:
    database = Database("postgresql+asyncpg://public_agent:public_agent@localhost:55432/public_agent")
    tenant_id = uuid4()
    tenant_slug = f"capacity-audit-{tenant_id.hex[:10]}"
    handler_version = f"capacity-audit-{uuid4().hex[:8]}"
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("capacity-audit-test-pepper"),
    )
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
    )
    now = utc_now()
    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Capacity Audit"))
        operator = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="capacity-audit-operator",
                display_name="Capacity Audit Operator",
                permissions=(CAPACITY_GOVERNANCE_READ,),
                all_agents=True,
            )
        )
        operator_token = await auth.issue_token(
            principal_id=operator.id,
            tenant_id=tenant_slug,
            label="audit-source",
        )
        auditor = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="capacity-auditor",
                display_name="Capacity Auditor",
                permissions=(CAPACITY_AUDIT_READ,),
                all_agents=True,
            )
        )
        auditor_token = await auth.issue_token(
            principal_id=auditor.id,
            tenant_id=tenant_slug,
            label="auditor",
        )
        actor = await auth.authenticate(auditor_token.token.get_secret_value())
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.summary(actor=actor)
        async with database.sessions() as session, session.begin():
            session.add_all(
                (
                    ReflectionCapacityGovernanceAuditEventModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        actor_principal_id=operator.id,
                        actor_token_id=operator_token.id,
                        handler_version=handler_version,
                        request_id=None,
                        alert_id=None,
                        action="capacity.request.publish",
                        outcome="success",
                        safe_metadata={
                            "opened": 1,
                            "authorization": "Bearer must-not-leak",
                            "token_id": str(operator_token.id),
                        },
                        created_at=now - timedelta(seconds=2),
                    ),
                    ReflectionCapacityGovernanceAuditEventModel(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        actor_principal_id=operator.id,
                        actor_token_id=operator_token.id,
                        handler_version=handler_version,
                        request_id=None,
                        alert_id=None,
                        action="capacity.request.rollback",
                        outcome="denied",
                        safe_metadata={"error_type": "CapacityGovernanceAuthorizationError"},
                        created_at=now - timedelta(seconds=1),
                    ),
                )
            )

        page = await control.list_audit_events(
            CapacityGovernanceAuditQuery(
                actor_subject="capacity-audit-operator",
                action="capacity.request.publish",
                outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                occurred_from=now - timedelta(minutes=1),
                occurred_to=now,
            ),
            actor=actor,
        )
        assert len(page.items) == 1
        assert page.items[0].actor_subject == "capacity-audit-operator"
        assert page.items[0].safe_metadata == {"opened": 1}
        payload = page.items[0].model_dump(mode="json")
        assert "actor_token_id" not in payload
        assert "authorization" not in str(payload).lower()
        assert str(operator_token.id) not in str(payload)

        first_page = await control.list_audit_events(
            CapacityGovernanceAuditQuery(limit=1),
            actor=actor,
        )
        assert first_page.next_cursor is not None
        with pytest.raises(CapacityGovernanceCursorError):
            await control.list_audit_events(
                CapacityGovernanceAuditQuery(
                    outcome=CapacityGovernanceAuditOutcome.SUCCESS,
                    cursor=first_page.next_cursor,
                ),
                actor=actor,
            )

        before = await _governance_row_counts(database, handler_version=handler_version)
        drill = await control.governance_drill(actor=actor)
        after = await _governance_row_counts(database, handler_version=handler_version)
        assert drill.passed is True
        assert all(check.passed for check in drill.checks)
        assert {check.name for check in drill.checks} >= {
            "knowledge_feedback_lifecycle_constraints",
            "knowledge_feedback_query_indexes",
            "knowledge_quality_snapshot_controls",
            "knowledge_quality_query_indexes",
            "knowledge_recovery_lifecycle_constraints",
            "knowledge_recovery_query_indexes",
            "knowledge_recertification_lifecycle_constraints",
            "knowledge_recertification_query_indexes",
        }
        quality_index_check = next(
            check
            for check in drill.checks
            if check.name == "knowledge_quality_query_indexes"
        )
        assert "trend" in quality_index_check.detail.lower()
        assert after == before

        await auth.revoke_token(token_id=auditor_token.id, tenant_id=tenant_slug)
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.list_audit_events(CapacityGovernanceAuditQuery(), actor=actor)
    finally:
        await _cleanup(database, tenant_id=tenant_id, handler_version=handler_version)


@pytest.mark.asyncio
async def test_policy_drift_alert_is_deduplicated_acknowledged_resolved_and_reopened() -> None:
    database = Database("postgresql+asyncpg://public_agent:public_agent@localhost:55432/public_agent")
    tenant_id = uuid4()
    tenant_slug = f"capacity-alert-{tenant_id.hex[:10]}"
    handler_version = f"capacity-alert-{uuid4().hex[:8]}"
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("capacity-alert-test-pepper"),
    )
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
    )
    now = utc_now()
    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Capacity Alert"))
        principal = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="capacity-alert-operator",
                display_name="Capacity Alert Operator",
                permissions=(CAPACITY_ALERTS_READ, CAPACITY_ALERTS_MANAGE),
                all_agents=True,
            )
        )
        token = await auth.issue_token(
            principal_id=principal.id,
            tenant_id=tenant_slug,
            label="alert-operator",
        )
        replacement_token = await auth.issue_token(
            principal_id=principal.id,
            tenant_id=tenant_slug,
            label="alert-operator-replacement",
        )
        actor = await auth.authenticate(token.token.get_secret_value())
        await _add_observations(
            database,
            handler_version=handler_version,
            thresholds=_thresholds(ready_warning=101),
            observed_times=(now - timedelta(seconds=3), now - timedelta(seconds=2), now),
        )

        opened = await control.scan_drift()
        repeated = await control.scan_drift()
        assert opened.opened_alerts == 1
        assert repeated.opened_alerts == 0
        assert repeated.updated_alerts == 0
        async with database.sessions() as session:
            alert = await session.scalar(
                select(ReflectionCapacityGovernanceAlertModel).where(
                    ReflectionCapacityGovernanceAlertModel.handler_version
                    == handler_version
                )
            )
        assert alert is not None
        assert alert.status == CapacityGovernanceAlertStatus.OPEN.value
        assert alert.version == 1

        await auth.revoke_token(token_id=token.id, tenant_id=tenant_slug)
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.acknowledge_alert(
                alert_id=alert.id,
                expected_version=alert.version,
                actor=actor,
            )
        async with database.sessions() as session:
            unchanged = await session.scalar(
                select(ReflectionCapacityGovernanceAlertModel).where(
                    ReflectionCapacityGovernanceAlertModel.id == alert.id
                )
            )
        assert unchanged is not None
        assert unchanged.status == CapacityGovernanceAlertStatus.OPEN.value
        assert unchanged.version == 1

        actor = await auth.authenticate(replacement_token.token.get_secret_value())
        acknowledged = await control.acknowledge_alert(
            alert_id=alert.id,
            expected_version=alert.version,
            actor=actor,
        )
        assert acknowledged.status is CapacityGovernanceAlertStatus.ACKNOWLEDGED
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(ReflectionCapacityObservationModel).where(
                    ReflectionCapacityObservationModel.handler_version
                    == handler_version
                )
            )
        await _add_observations(
            database,
            handler_version=handler_version,
            thresholds=_thresholds(),
            observed_times=(utc_now(),),
        )
        resolved = await control.scan_drift()
        assert resolved.resolved_alerts == 1

        reopened_at = utc_now()
        await _add_observations(
            database,
            handler_version=handler_version,
            thresholds=_thresholds(ready_warning=101),
            observed_times=(
                reopened_at - timedelta(seconds=3),
                reopened_at - timedelta(seconds=2),
                reopened_at - timedelta(seconds=1),
                reopened_at,
            ),
        )
        reopened = await control.scan_drift()
        assert reopened.updated_alerts == 1
        async with database.sessions() as session:
            alert = await session.scalar(
                select(ReflectionCapacityGovernanceAlertModel).where(
                    ReflectionCapacityGovernanceAlertModel.handler_version
                    == handler_version
                )
            )
        assert alert is not None
        assert alert.status == CapacityGovernanceAlertStatus.OPEN.value
        assert alert.severity == CapacityGovernanceAlertSeverity.CRITICAL.value
        assert alert.reopened_count == 1

        async with database.sessions() as session:
            audit_id = await session.scalar(
                select(ReflectionCapacityGovernanceAuditEventModel.id).where(
                    ReflectionCapacityGovernanceAuditEventModel.handler_version
                    == handler_version
                )
            )
        assert audit_id is not None
        async with database.sessions() as session:
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(
                    update(ReflectionCapacityGovernanceAuditEventModel)
                    .where(ReflectionCapacityGovernanceAuditEventModel.id == audit_id)
                    .values(outcome="conflict")
                )
            await session.rollback()
    finally:
        await _cleanup(database, tenant_id=tenant_id, handler_version=handler_version)


@pytest.mark.asyncio
async def test_policy_change_resolves_stale_expected_fingerprint_alert() -> None:
    database = Database("postgresql+asyncpg://public_agent:public_agent@localhost:55432/public_agent")
    tenant_id = uuid4()
    tenant_slug = f"capacity-policy-change-{tenant_id.hex[:10]}"
    handler_version = f"capacity-policy-change-{uuid4().hex[:8]}"
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
    )
    observed = _thresholds(ready_warning=101)
    now = utc_now()
    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Policy Change"))
        await _add_observations(
            database,
            handler_version=handler_version,
            thresholds=observed,
            observed_times=(
                now - timedelta(seconds=4),
                now - timedelta(seconds=3),
                now - timedelta(seconds=2),
            ),
        )
        assert (await control.scan_drift()).opened_alerts == 1

        active_policy_id = uuid4()
        async with database.sessions() as session, session.begin():
            session.add(
                ReflectionCapacityPolicyModel(
                    id=active_policy_id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=handler_version,
                    policy_version=1,
                    status="active",
                    thresholds=_thresholds(ready_warning=102).model_dump(mode="json"),
                    source_type="settings_baseline",
                    source_calibration_id=None,
                    previous_policy_id=None,
                    created_by="policy-change-test",
                    activated_at=now,
                    deactivated_at=None,
                )
            )
        await _add_observations(
            database,
            handler_version=handler_version,
            thresholds=observed,
            observed_times=(now,),
        )

        report = await control.scan_drift()
        assert report.opened_alerts == 1
        assert report.resolved_alerts == 1
        async with database.sessions() as session:
            alerts = tuple(
                await session.scalars(
                    select(ReflectionCapacityGovernanceAlertModel)
                    .where(
                        ReflectionCapacityGovernanceAlertModel.handler_version
                        == handler_version
                    )
                    .order_by(ReflectionCapacityGovernanceAlertModel.created_at)
                )
            )
        assert len(alerts) == 2
        assert alerts[0].status == CapacityGovernanceAlertStatus.RESOLVED.value
        assert alerts[1].status == CapacityGovernanceAlertStatus.OPEN.value
        assert alerts[1].expected_policy_id == active_policy_id
    finally:
        await _cleanup(database, tenant_id=tenant_id, handler_version=handler_version)


@pytest.mark.asyncio
async def test_governance_incidents_scan_dedupe_ack_resolve_reopen_and_reauthorize() -> None:
    database = Database("postgresql+asyncpg://public_agent:public_agent@localhost:55432/public_agent")
    tenant_id = uuid4()
    tenant_slug = f"capacity-incident-{tenant_id.hex[:10]}"
    handler_version = f"capacity-incident-{uuid4().hex[:8]}"
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("capacity-incident-test-pepper"),
    )
    control = _FailedDrillCapacityControl(
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
            audit_warning_count=2,
            audit_critical_count=3,
            reopen_warning_count=2,
            reopen_critical_count=4,
        ),
    )
    now = utc_now()
    sla_alert_id = uuid4()
    reopen_alert_id = uuid4()
    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Capacity Incident"))
        auditor = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="capacity-incident-auditor",
                display_name="Capacity Incident Auditor",
                permissions=(CAPACITY_AUDIT_READ,),
                all_agents=True,
            )
        )
        auditor_token = await auth.issue_token(
            principal_id=auditor.id,
            tenant_id=tenant_slug,
            label="incident-auditor",
        )
        incident_operator = await auth.create_principal(
            PrincipalCreateRequest(
                tenant_id=tenant_slug,
                subject="capacity-incident-operator",
                display_name="Capacity Incident Operator",
                permissions=(CAPACITY_INCIDENTS_READ, CAPACITY_INCIDENTS_MANAGE),
                all_agents=True,
            )
        )
        incident_token = await auth.issue_token(
            principal_id=incident_operator.id,
            tenant_id=tenant_slug,
            label="incident-operator",
        )
        incident_actor = await auth.authenticate(incident_token.token.get_secret_value())
        auditor_actor = await auth.authenticate(auditor_token.token.get_secret_value())
        async with database.sessions() as session, session.begin():
            session.add_all(
                ReflectionCapacityGovernanceAuditEventModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    actor_principal_id=auditor.id,
                    actor_token_id=auditor_token.id,
                    handler_version=handler_version,
                    request_id=None,
                    alert_id=None,
                    incident_id=None,
                    action=f"capacity.denied.{index}",
                    outcome="denied" if index < 2 else "conflict",
                    safe_metadata={},
                    created_at=now,
                )
                for index in range(3)
            )
            session.add_all(
                (
                    _governance_alert(
                        alert_id=sla_alert_id,
                        handler_version=handler_version,
                        now=now,
                        first_seen_at=now - timedelta(hours=2),
                    ),
                    _governance_alert(
                        alert_id=reopen_alert_id,
                        handler_version=handler_version,
                        now=now,
                        first_seen_at=now - timedelta(minutes=5),
                        reopened_count=2,
                    ),
                )
            )

        with patch("public_agent.storage.capacity_control.utc_now", return_value=now):
            opened = await control.scan_incidents()
            repeated = await control.scan_incidents()
        assert opened.opened_incidents == 4
        assert opened.matched_signals == 4
        assert repeated.opened_incidents == 0
        assert repeated.updated_incidents == 0
        async with database.sessions() as session:
            assert len(
                tuple(
                    await session.scalars(
                        select(ReflectionCapacityGovernanceIncidentModel).where(
                            ReflectionCapacityGovernanceIncidentModel.handler_version
                            == handler_version
                        )
                    )
                )
            ) == 4

        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.list_incidents(CapacityGovernanceIncidentQuery(), actor=auditor_actor)
        page = await control.list_incidents(
            CapacityGovernanceIncidentQuery(limit=1),
            actor=incident_actor,
        )
        assert page.next_cursor is not None
        with pytest.raises(CapacityGovernanceCursorError):
            await control.list_incidents(
                CapacityGovernanceIncidentQuery(
                    signal=CapacityGovernanceIncidentSignal.ALERT_SLA_BREACHED,
                    cursor=page.next_cursor,
                ),
                actor=incident_actor,
            )
        incidents = await control.list_incidents(
            CapacityGovernanceIncidentQuery(limit=100),
            actor=incident_actor,
        )
        sla_incident = next(
            item
            for item in incidents.items
            if item.signal is CapacityGovernanceIncidentSignal.ALERT_SLA_BREACHED
        )
        safe_payload = sla_incident.model_dump(mode="json")
        assert "acknowledged_token_id" not in safe_payload
        assert "acknowledged_principal_id" not in safe_payload

        acknowledged = await control.acknowledge_incident(
            incident_id=sla_incident.id,
            expected_version=sla_incident.version,
            actor=incident_actor,
        )
        assert acknowledged.status is CapacityGovernanceIncidentStatus.ACKNOWLEDGED
        unchanged = await control.scan_incidents()
        assert unchanged.resolved_incidents == 0

        resolved_at = utc_now() + timedelta(seconds=1)
        async with database.sessions() as session, session.begin():
            alert = await session.get(ReflectionCapacityGovernanceAlertModel, sla_alert_id)
            assert alert is not None
            alert.status = CapacityGovernanceAlertStatus.RESOLVED.value
            alert.resolved_at = resolved_at
            alert.version += 1
            alert.updated_at = resolved_at
        resolved = await control.scan_incidents()
        assert resolved.resolved_incidents >= 1

        reopened_at = resolved_at + timedelta(seconds=1)
        async with database.sessions() as session, session.begin():
            alert = await session.get(ReflectionCapacityGovernanceAlertModel, sla_alert_id)
            assert alert is not None
            alert.status = CapacityGovernanceAlertStatus.OPEN.value
            alert.resolved_at = None
            alert.version += 1
            alert.updated_at = reopened_at
        reopened = await control.scan_incidents()
        assert reopened.updated_incidents >= 1
        refreshed = await control.get_incident(sla_incident.id, actor=incident_actor)
        assert refreshed.status is CapacityGovernanceIncidentStatus.OPEN
        assert refreshed.reopened_count == 1

        await auth.revoke_token(token_id=incident_token.id, tenant_id=tenant_slug)
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.list_incidents(
                CapacityGovernanceIncidentQuery(),
                actor=incident_actor,
            )
    finally:
        await _cleanup(database, tenant_id=tenant_id, handler_version=handler_version)


@pytest.mark.asyncio
async def test_remediation_requires_fixed_playbook_separation_and_new_recovery_fact() -> None:
    database = Database(
        "postgresql+asyncpg://public_agent:public_agent@localhost:55432/public_agent"
    )
    tenant_id = uuid4()
    tenant_slug = f"capacity-remediation-{tenant_id.hex[:10]}"
    handler_version = f"capacity-remediation-{uuid4().hex[:8]}"
    auth = PostgresAPIKeyService(
        database.sessions,
        codec=APITokenCodec("capacity-remediation-test-pepper"),
    )
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
    )

    async def create_actor(subject: str, permissions: tuple[str, ...]):
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
    try:
        async with database.sessions() as session, session.begin():
            session.add(
                TenantModel(id=tenant_id, slug=tenant_slug, name="Remediation")
            )
        async with database.sessions() as session, session.begin():
            session.add(
                ReflectionCapacityGovernanceIncidentModel(
                    id=incident_id,
                    tenant_id=tenant_id,
                    job_type=REFLECTION_JOB_TYPE,
                    handler_version=handler_version,
                    signal=CapacityGovernanceIncidentSignal.DRILL_CHECK_FAILED.value,
                    rule_version="drill-check-failed/v1",
                    severity="critical",
                    status="open",
                    version=1,
                    source_id=None,
                    fingerprint=uuid4().hex + uuid4().hex,
                    evidence_fingerprint=uuid4().hex + uuid4().hex,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_evidence_at=now,
                    occurrence_count=1,
                    reopened_count=0,
                    evidence={"check_name": "incident_query_indexes"},
                    acknowledged_by=None,
                    acknowledged_principal_id=None,
                    acknowledged_token_id=None,
                    acknowledged_at=None,
                    resolved_at=None,
                )
            )
        requester = await create_actor(
            "remediation-requester",
            (
                CAPACITY_INCIDENTS_MANAGE,
                CAPACITY_REMEDIATIONS_READ,
                CAPACITY_REMEDIATIONS_REQUEST,
                CAPACITY_REMEDIATIONS_APPROVE,
            ),
        )
        approver = await create_actor(
            "remediation-approver",
            (CAPACITY_REMEDIATIONS_APPROVE,),
        )
        executor = await create_actor(
            "remediation-executor",
            (CAPACITY_REMEDIATIONS_EXECUTE, CAPACITY_REMEDIATIONS_VERIFY),
        )
        verifier = await create_actor(
            "remediation-verifier",
            (CAPACITY_REMEDIATIONS_VERIFY,),
        )

        acknowledged = await control.acknowledge_incident(
            incident_id=incident_id,
            expected_version=1,
            actor=requester,
        )
        with pytest.raises(ValueError, match="playbook"):
            await control.create_remediation(
                incident_id=incident_id,
                expected_incident_version=acknowledged.version,
                playbook=(
                    CapacityGovernanceRemediationPlaybook.AUDIT_FAILURE_CONTAINMENT
                ),
                actor=requester,
            )
        requested = await control.create_remediation(
            incident_id=incident_id,
            expected_incident_version=acknowledged.version,
            playbook=CapacityGovernanceRemediationPlaybook.DRILL_CONTROL_REPAIR,
            actor=requester,
        )
        with pytest.raises(ReflectionCapacityGovernanceConflictError):
            await control.create_remediation(
                incident_id=incident_id,
                expected_incident_version=acknowledged.version,
                playbook=CapacityGovernanceRemediationPlaybook.DRILL_CONTROL_REPAIR,
                actor=requester,
            )
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.approve_remediation(
                remediation_id=requested.id,
                expected_version=requested.version,
                actor=requester,
            )
        approved = await control.approve_remediation(
            remediation_id=requested.id,
            expected_version=requested.version,
            actor=approver,
        )
        with pytest.raises(ValueError, match="evidence"):
            await control.record_remediation_execution(
                remediation_id=requested.id,
                expected_version=approved.version,
                result=CapacityGovernanceRemediationExecutionResult.COMPLETED,
                evidence=CapacityGovernanceRemediationEvidence.MONITORING_EXTENDED,
                actor=executor,
            )
        executed = await control.record_remediation_execution(
            remediation_id=requested.id,
            expected_version=approved.version,
            result=CapacityGovernanceRemediationExecutionResult.COMPLETED,
            evidence=CapacityGovernanceRemediationEvidence.SCHEMA_CONTROL_RESTORED,
            actor=executor,
        )
        assert executed.status is CapacityGovernanceRemediationStatus.VERIFICATION_PENDING
        with pytest.raises(CapacityGovernanceAuthorizationError):
            await control.verify_remediation(
                remediation_id=requested.id,
                expected_version=executed.version,
                actor=executor,
            )
        with pytest.raises(ReflectionCapacityGovernanceConflictError):
            await control.verify_remediation(
                remediation_id=requested.id,
                expected_version=executed.version,
                actor=verifier,
            )

        resolved_at = utc_now() + timedelta(milliseconds=10)
        async with database.sessions() as session, session.begin():
            incident = await session.get(
                ReflectionCapacityGovernanceIncidentModel,
                incident_id,
            )
            assert incident is not None
            incident.status = CapacityGovernanceIncidentStatus.RESOLVED.value
            incident.resolved_at = resolved_at
            incident.version += 1
            incident.updated_at = resolved_at
        verified = await control.verify_remediation(
            remediation_id=requested.id,
            expected_version=executed.version,
            actor=verifier,
        )
        assert verified.status is CapacityGovernanceRemediationStatus.VERIFIED
        assert verified.verified_by == "remediation-verifier"
        assert "requested_token_id" not in verified.model_dump(mode="json")
        page = await control.list_remediations(
            CapacityGovernanceRemediationQuery(limit=1),
            actor=requester,
        )
        assert page.items == (verified,)
    finally:
        await _cleanup(database, tenant_id=tenant_id, handler_version=handler_version)


async def _add_observations(
    database: Database,
    *,
    handler_version: str,
    thresholds: ReflectionCapacityThresholds,
    observed_times: tuple,
) -> None:
    async with database.sessions() as session, session.begin():
        session.add_all(
            ReflectionCapacityObservationModel(
                id=uuid4(),
                job_type=REFLECTION_JOB_TYPE,
                handler_version=handler_version,
                observed_at=observed_at,
                status="healthy",
                ready=0,
                processing=0,
                succeeded=0,
                dead_letter=0,
                oldest_ready_age_seconds=0,
                active_workers=1,
                stale_workers=0,
                errored_workers=0,
                processed_jobs=0,
                recommended_workers=1,
                scale_delta=0,
                reasons=[],
                thresholds=thresholds.model_dump(mode="json"),
            )
            for observed_at in observed_times
        )


def _governance_alert(
    *,
    alert_id,
    handler_version: str,
    now,
    first_seen_at,
    reopened_count: int = 0,
) -> ReflectionCapacityGovernanceAlertModel:
    return ReflectionCapacityGovernanceAlertModel(
        id=alert_id,
        job_type=REFLECTION_JOB_TYPE,
        handler_version=handler_version,
        alert_type="policy_drift",
        severity="warning",
        status="open",
        version=1,
        dedupe_key=uuid4().hex + uuid4().hex,
        expected_policy_id=None,
        expected_policy_version=None,
        expected_fingerprint="a" * 64,
        observed_fingerprint=uuid4().hex + uuid4().hex,
        first_seen_at=first_seen_at,
        last_seen_at=now,
        last_observation_at=now,
        sample_count=3,
        details={},
        acknowledged_by=None,
        acknowledged_principal_id=None,
        acknowledged_token_id=None,
        acknowledged_at=None,
        resolved_at=None,
        reopened_count=reopened_count,
        created_at=first_seen_at,
        updated_at=now,
    )


async def _cleanup(
    database: Database,
    *,
    tenant_id,
    handler_version: str,
) -> None:
    async with database.sessions() as session, session.begin():
        await session.execute(
            delete(ReflectionCapacityGovernanceAuditEventModel).where(
                ReflectionCapacityGovernanceAuditEventModel.handler_version
                == handler_version
            )
        )
        await session.execute(
            delete(ReflectionCapacityGovernanceRemediationModel).where(
                ReflectionCapacityGovernanceRemediationModel.handler_version
                == handler_version
            )
        )
        await session.execute(
            delete(ReflectionCapacityGovernanceIncidentModel).where(
                ReflectionCapacityGovernanceIncidentModel.handler_version
                == handler_version
            )
        )
        await session.execute(
            delete(ReflectionCapacityGovernanceAlertModel).where(
                ReflectionCapacityGovernanceAlertModel.handler_version == handler_version
            )
        )
        await session.execute(
            delete(ReflectionCapacityChangeRequestModel).where(
                ReflectionCapacityChangeRequestModel.handler_version == handler_version
            )
        )
        await session.execute(
            delete(ReflectionCapacityPolicyModel).where(
                ReflectionCapacityPolicyModel.handler_version == handler_version
            )
        )
        await session.execute(
            delete(ReflectionCapacityObservationModel).where(
                ReflectionCapacityObservationModel.handler_version == handler_version
            )
        )
        await session.execute(
            delete(AuthenticationAuditEventModel).where(
                AuthenticationAuditEventModel.tenant_id == tenant_id
            )
        )
        await session.execute(
            delete(APIPrincipalAgentGrantModel).where(
                APIPrincipalAgentGrantModel.tenant_id == tenant_id
            )
        )
        await session.execute(delete(APITokenModel).where(APITokenModel.tenant_id == tenant_id))
        await session.execute(
            delete(APIPrincipalModel).where(APIPrincipalModel.tenant_id == tenant_id)
        )
        await session.execute(delete(AgentModel).where(AgentModel.tenant_id == tenant_id))
        await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
    await database.dispose()


async def _governance_row_counts(
    database: Database,
    *,
    handler_version: str,
) -> tuple[int, int, int]:
    async with database.sessions() as session:
        return (
            len(
                tuple(
                    await session.scalars(
                        select(ReflectionCapacityChangeRequestModel.id).where(
                            ReflectionCapacityChangeRequestModel.handler_version
                            == handler_version
                        )
                    )
                )
            ),
            len(
                tuple(
                    await session.scalars(
                        select(ReflectionCapacityGovernanceAlertModel.id).where(
                            ReflectionCapacityGovernanceAlertModel.handler_version
                            == handler_version
                        )
                    )
                )
            ),
            len(
                tuple(
                    await session.scalars(
                        select(ReflectionCapacityGovernanceAuditEventModel.id).where(
                            ReflectionCapacityGovernanceAuditEventModel.handler_version
                            == handler_version
                        )
                    )
                )
            ),
        )
