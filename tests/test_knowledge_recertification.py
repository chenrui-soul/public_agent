from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from public_agent.auth import DEFAULT_MANAGEABLE_PERMISSIONS
from public_agent.operations import (
    CAPACITY_GOVERNANCE_ROLES,
    CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
    CAPACITY_KNOWLEDGE_RECERTIFICATION_REQUEST,
    CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW,
    CAPACITY_KNOWLEDGE_RETIREMENT,
    CapacityGovernanceKnowledgeLifecycleStatus,
    CapacityGovernanceKnowledgeRecertificationDecision,
    CapacityGovernanceKnowledgeRecertificationInput,
    CapacityGovernanceKnowledgeRecertificationPolicy,
    CapacityGovernanceKnowledgeRecertificationReason,
    CapacityGovernancePostmortemStatus,
    project_governance_knowledge_lifecycle,
)

NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


def _input(**updates: object) -> CapacityGovernanceKnowledgeRecertificationInput:
    values: dict[str, object] = {
        "postmortem_id": uuid4(),
        "expected_postmortem_version": 2,
        "knowledge_version": " k-2 ",
        "content_fingerprint": "A" * 64,
        "quality_snapshot_id": uuid4(),
        "quality_evidence_fingerprint": "B" * 64,
        "decision": CapacityGovernanceKnowledgeRecertificationDecision.CERTIFY,
        "reason": CapacityGovernanceKnowledgeRecertificationReason.VALIDATION_PASSED,
    }
    values.update(updates)
    return CapacityGovernanceKnowledgeRecertificationInput.model_validate(values)


def test_recertification_policy_requires_bounded_notice_window() -> None:
    with pytest.raises(ValidationError, match="due notice"):
        CapacityGovernanceKnowledgeRecertificationPolicy(
            policy_version=1,
            window_seconds=86_400,
            due_notice_seconds=86_400,
        )


def test_recertification_input_normalizes_fingerprints_and_rejects_mismatched_reason() -> None:
    value = _input()
    assert value.knowledge_version == "k-2"
    assert value.content_fingerprint == "a" * 64
    assert value.quality_evidence_fingerprint == "b" * 64

    with pytest.raises(ValidationError, match="incompatible"):
        _input(
            decision=CapacityGovernanceKnowledgeRecertificationDecision.CERTIFY,
            reason=CapacityGovernanceKnowledgeRecertificationReason.QUALITY_RISK,
        )
    with pytest.raises(ValidationError, match="SHA-256"):
        _input(content_fingerprint="z" * 64)


def test_recertification_permissions_are_separate_and_manageable() -> None:
    by_name = {role.name: set(role.permissions) for role in CAPACITY_GOVERNANCE_ROLES}
    assert {
        CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
        CAPACITY_KNOWLEDGE_RECERTIFICATION_REQUEST,
        CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW,
        CAPACITY_KNOWLEDGE_RETIREMENT,
    } <= DEFAULT_MANAGEABLE_PERMISSIONS
    assert by_name["knowledge_recertification_viewer"] == {
        CAPACITY_KNOWLEDGE_RECERTIFICATION_READ
    }
    assert by_name["knowledge_recertification_requester"] == {
        CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
        CAPACITY_KNOWLEDGE_RECERTIFICATION_REQUEST,
    }
    assert by_name["knowledge_recertification_reviewer"] == {
        CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
        CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW,
    }
    assert by_name["knowledge_retirement_operator"] == {
        CAPACITY_KNOWLEDGE_RECERTIFICATION_READ,
        CAPACITY_KNOWLEDGE_RETIREMENT,
    }
    assert CAPACITY_KNOWLEDGE_RECERTIFICATION_REVIEW not in by_name[
        "knowledge_recertification_requester"
    ]
    assert CAPACITY_KNOWLEDGE_RETIREMENT not in by_name["knowledge_recertification_reviewer"]


def _project(**updates: object):
    values: dict[str, object] = {
        "postmortem_id": uuid4(),
        "handler_version": "reflection-v1",
        "postmortem_version": 1,
        "knowledge_version": "k-1",
        "content_fingerprint": "c" * 64,
        "postmortem_status": CapacityGovernancePostmortemStatus.PUBLISHED,
        "published_at": NOW - timedelta(days=20),
        "last_restored_at": None,
        "last_certified_at": None,
        "policy": CapacityGovernanceKnowledgeRecertificationPolicy(
            policy_version=1,
            window_seconds=30 * 86_400,
            due_notice_seconds=7 * 86_400,
        ),
        "now": NOW,
    }
    values.update(updates)
    return project_governance_knowledge_lifecycle(**values)


@pytest.mark.parametrize(
    ("anchor_age", "expected"),
    (
        (timedelta(days=10), CapacityGovernanceKnowledgeLifecycleStatus.CURRENT),
        (timedelta(days=25), CapacityGovernanceKnowledgeLifecycleStatus.DUE),
        (timedelta(days=31), CapacityGovernanceKnowledgeLifecycleStatus.OVERDUE),
    ),
)
def test_lifecycle_projection_is_deterministic(anchor_age, expected) -> None:
    record = _project(published_at=NOW - anchor_age)
    assert record.status is expected
    assert record.due_at == record.anchor_at + timedelta(days=30)
    assert record.generated_at == NOW


def test_quarantined_and_retired_override_due_projection_without_mutating_facts() -> None:
    quarantined = _project(
        postmortem_status=CapacityGovernancePostmortemStatus.QUARANTINED,
        published_at=NOW - timedelta(days=90),
    )
    assert quarantined.status is CapacityGovernanceKnowledgeLifecycleStatus.QUARANTINED
    assert quarantined.due_at is None

    retired = _project(
        retired=True,
        postmortem_status=CapacityGovernancePostmortemStatus.PUBLISHED,
        last_certified_at=NOW - timedelta(days=90),
    )
    assert retired.status is CapacityGovernanceKnowledgeLifecycleStatus.RETIRED
    assert retired.due_at is None


def test_lifecycle_projection_uses_latest_anchor_fact() -> None:
    restored_at = NOW - timedelta(days=5)
    certified_at = NOW - timedelta(days=20)
    record = _project(
        published_at=NOW - timedelta(days=25),
        last_restored_at=restored_at,
        last_certified_at=certified_at,
    )
    assert record.anchor_at == restored_at
    assert record.status is CapacityGovernanceKnowledgeLifecycleStatus.CURRENT


def test_lifecycle_projection_rejects_naive_or_unanchored_published_knowledge() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _project(now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="lifecycle anchor"):
        _project(published_at=None)
    with pytest.raises(ValueError, match="future"):
        _project(last_certified_at=NOW + timedelta(minutes=1))


def test_lifecycle_projection_rejects_non_published_non_quarantined_status() -> None:
    with pytest.raises(ValueError, match="published or quarantined"):
        _project(postmortem_status=CapacityGovernancePostmortemStatus.REJECTED)
