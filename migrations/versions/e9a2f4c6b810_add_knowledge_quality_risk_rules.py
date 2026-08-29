"""add governance knowledge quality trend and risk rules

Revision ID: e9a2f4c6b810
Revises: d8f1c2a4b730
Create Date: 2026-08-25 22:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e9a2f4c6b810"
down_revision: str | None = "d8f1c2a4b730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_reflection_capacity_governance_incidents_signal",
        "reflection_capacity_governance_incidents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_reflection_capacity_governance_incidents_signal",
        "reflection_capacity_governance_incidents",
        "signal IN ('audit_failure_spike','alert_sla_breached',"
        "'alert_reopen_repeat','drill_check_failed',"
        "'knowledge_unsafe_persistent','knowledge_degraded_repeat',"
        "'knowledge_requarantined')",
    )
    op.drop_constraint(
        "ck_capacity_remediations_playbook",
        "reflection_capacity_governance_remediations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_capacity_remediations_playbook",
        "reflection_capacity_governance_remediations",
        "playbook IN ('audit_failure_containment','alert_sla_recovery',"
        "'alert_reopen_stabilization','drill_control_repair',"
        "'knowledge_safety_containment','knowledge_quality_review',"
        "'knowledge_recurrence_review')",
    )
    op.drop_constraint(
        "ck_capacity_remediations_evidence",
        "reflection_capacity_governance_remediations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_capacity_remediations_evidence",
        "reflection_capacity_governance_remediations",
        "execution_evidence IS NULL OR execution_evidence IN "
        "('containment_applied','configuration_reviewed','monitoring_extended',"
        "'schema_control_restored','knowledge_quarantine_reviewed',"
        "'quality_evidence_reviewed','restoration_history_reviewed')",
    )
    op.create_index(
        "ix_capacity_knowledge_quality_tenant_captured",
        "reflection_capacity_governance_knowledge_quality_snapshots",
        ["tenant_id", "handler_version", "captured_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capacity_knowledge_quality_tenant_captured",
        table_name="reflection_capacity_governance_knowledge_quality_snapshots",
    )
    op.drop_constraint(
        "ck_capacity_remediations_evidence",
        "reflection_capacity_governance_remediations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_capacity_remediations_evidence",
        "reflection_capacity_governance_remediations",
        "execution_evidence IS NULL OR execution_evidence IN "
        "('containment_applied','configuration_reviewed','monitoring_extended',"
        "'schema_control_restored')",
    )
    op.drop_constraint(
        "ck_capacity_remediations_playbook",
        "reflection_capacity_governance_remediations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_capacity_remediations_playbook",
        "reflection_capacity_governance_remediations",
        "playbook IN ('audit_failure_containment','alert_sla_recovery',"
        "'alert_reopen_stabilization','drill_control_repair')",
    )
    op.drop_constraint(
        "ck_reflection_capacity_governance_incidents_signal",
        "reflection_capacity_governance_incidents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_reflection_capacity_governance_incidents_signal",
        "reflection_capacity_governance_incidents",
        "signal IN ('audit_failure_spike','alert_sla_breached',"
        "'alert_reopen_repeat','drill_check_failed')",
    )
