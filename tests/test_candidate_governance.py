from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from public_agent.growth.governance import (
    CandidateGovernanceDecision,
    CandidateGovernancePage,
    CandidateGovernancePolicy,
    CandidateGovernanceQuery,
    CandidateGovernanceService,
    CandidateGovernanceSnapshot,
    DeterministicCandidateCompressor,
    GovernanceAction,
    GovernanceApplyResult,
    GovernanceReason,
    candidate_value_score,
    governance_decision,
    governance_protection_reason,
)
from public_agent.growth.models import (
    CandidateRisk,
    CandidateStatus,
    CandidateType,
    EvaluationResult,
    LearningCandidate,
)
from public_agent.growth.service import InMemoryLearningStore, LearningService


class PassingEvaluator:
    async def evaluate(self, candidate: LearningCandidate) -> EvaluationResult:
        del candidate
        return EvaluationResult(
            passed=True,
            score=0.91,
            summary="Compression preserves compatible evidence",
        )


class FakeGovernanceRepository:
    def __init__(
        self,
        *,
        learning_store: InMemoryLearningStore,
        snapshots: tuple[CandidateGovernanceSnapshot, ...],
    ) -> None:
        self._learning_store = learning_store
        self._snapshots = snapshots
        self.decisions: list[CandidateGovernanceDecision] = []

    async def scan(self, query: CandidateGovernanceQuery) -> CandidateGovernancePage:
        return CandidateGovernancePage(items=self._snapshots[: query.limit])

    async def apply(
        self,
        decision: CandidateGovernanceDecision,
        *,
        policy: CandidateGovernancePolicy,
    ) -> GovernanceApplyResult:
        del policy
        self.decisions.append(decision)
        candidate = await self._learning_store.get(decision.candidate_id)
        if (
            candidate.version != decision.expected_version
            or candidate.status is not decision.expected_status
        ):
            return GovernanceApplyResult(candidate=candidate, applied=False)
        updated = candidate.model_copy(
            update={
                "status": CandidateStatus.EXPIRED,
                "version": candidate.version + 1,
                "updated_at": decision.decided_at,
            }
        )
        await self._learning_store.save(updated)
        return GovernanceApplyResult(candidate=updated, applied=True)

    async def create_compression(
        self,
        candidate: LearningCandidate,
        *,
        source_versions: dict[UUID, int],
        policy_version: str,
        value_score: float,
    ) -> tuple[LearningCandidate, bool]:
        del source_versions, policy_version, value_score
        try:
            return await self._learning_store.get(candidate.id), False
        except KeyError:
            await self._learning_store.save(candidate)
            return candidate, True


def _candidate(
    *,
    now: datetime,
    content: str,
    status: CandidateStatus = CandidateStatus.ACTIVE,
    risk: CandidateRisk = CandidateRisk.LOW,
    importance: float = 0.2,
    confidence: float = 0.3,
    proposed_extra: dict[str, object] | None = None,
) -> LearningCandidate:
    proposed_change: dict[str, object] = {
        "content": content,
        "namespace": "tax",
        "memory_type": "procedural",
        "importance": importance,
        "confidence": confidence,
        "evidence_event_ids": [str(uuid4())],
        "tags": ["tax"],
    }
    proposed_change.update(proposed_extra or {})
    return LearningCandidate(
        tenant_id="tenant-a",
        agent_id="agent-a",
        domain_id="tax",
        candidate_type=CandidateType.MEMORY,
        risk=risk,
        title=content[:80],
        proposed_change=proposed_change,
        evidence_run_ids=(uuid4(),),
        status=status,
        created_at=now - timedelta(days=120),
        updated_at=now - timedelta(days=120),
    )


def test_governance_protects_approval_high_risk_explicit_and_live_lineage() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    policy = CandidateGovernancePolicy()
    scenarios = (
        (
            _candidate(
                now=now,
                content="Awaiting approval",
                status=CandidateStatus.AWAITING_APPROVAL,
            ),
            {},
            "approval_or_evaluation_in_progress",
        ),
        (
            _candidate(now=now, content="High-risk rule", risk=CandidateRisk.HIGH),
            {},
            "high_risk",
        ),
        (
            _candidate(
                now=now,
                content="Explicitly protected rule",
                proposed_extra={"governance": {"protected": True}},
            ),
            {},
            "explicitly_protected",
        ),
        (
            _candidate(now=now, content="Referenced source"),
            {"has_live_descendant": True},
            "referenced_by_live_candidate",
        ),
        (
            _candidate(now=now, content="High-value memory"),
            {
                "memory_status": "superseded",
                "memory_importance": 0.95,
                "memory_confidence": 0.4,
            },
            "high_value_memory",
        ),
    )

    for candidate, snapshot_updates, expected in scenarios:
        snapshot = CandidateGovernanceSnapshot(
            candidate=candidate,
            **snapshot_updates,
        )
        assert (
            governance_protection_reason(
                snapshot,
                policy=policy,
                as_of=now,
            )
            == expected
        )
        assert governance_decision(snapshot, policy=policy, as_of=now) == (None, expected)


def test_stale_pending_and_low_value_active_candidates_receive_guarded_decisions() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    policy = CandidateGovernancePolicy(low_value_threshold=0.4)
    pending = _candidate(
        now=now,
        content="Never evaluated",
        status=CandidateStatus.PENDING,
    )
    pending_snapshot = CandidateGovernanceSnapshot(candidate=pending)
    pending_decision, _ = governance_decision(
        pending_snapshot,
        policy=policy,
        as_of=now,
    )

    assert pending_decision is not None
    assert pending_decision.action is GovernanceAction.EXPIRE
    assert pending_decision.reason is GovernanceReason.STALE_PENDING

    active = _candidate(now=now, content="Old low-value workflow")
    active_snapshot = CandidateGovernanceSnapshot(
        candidate=active,
        latest_evaluation_score=0.1,
        memory_status="active",
        memory_importance=0.2,
        memory_confidence=0.3,
        recall_count=0,
    )
    active_decision, _ = governance_decision(
        active_snapshot,
        policy=policy,
        as_of=now,
    )

    assert candidate_value_score(active_snapshot, policy, as_of=now) == 0.14
    assert active_decision is not None
    assert active_decision.action is GovernanceAction.EVICT
    assert active_decision.reason is GovernanceReason.LOW_VALUE
    assert active_decision.expected_recall_count == 0


@pytest.mark.asyncio
async def test_governance_service_expires_stale_candidate_and_proposes_compression_once() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    learning_store = InMemoryLearningStore()
    stale = _candidate(
        now=now,
        content="Stale pending candidate",
        status=CandidateStatus.PENDING,
    )
    first = _candidate(
        now=now,
        content="Validate source documents before calculating tax.",
        importance=0.7,
        confidence=0.8,
    )
    second = _candidate(
        now=now,
        content="Always validate source documents before calculating tax.",
        importance=0.75,
        confidence=0.82,
    )
    for candidate in (stale, first, second):
        await learning_store.save(candidate)
    repository = FakeGovernanceRepository(
        learning_store=learning_store,
        snapshots=tuple(
            CandidateGovernanceSnapshot(
                candidate=candidate,
                latest_evaluation_score=0.9,
                memory_status="active" if candidate.status is CandidateStatus.ACTIVE else None,
                memory_importance=float(candidate.proposed_change["importance"]),
                memory_confidence=float(candidate.proposed_change["confidence"]),
            )
            for candidate in (stale, first, second)
        ),
    )
    service = CandidateGovernanceService(
        repository=repository,
        learning=LearningService(learning_store),
        evaluator=PassingEvaluator(),
        policy=CandidateGovernancePolicy(
            low_value_threshold=0,
            compression_min_age_days=0,
        ),
    )
    query = CandidateGovernanceQuery(
        tenant_id="tenant-a",
        agent_id="agent-a",
        domain_id="tax",
        as_of=now,
    )

    first_result = await service.run_batch(query)
    second_result = await service.run_batch(query)

    assert first_result.expired == 1
    assert first_result.evicted == 0
    assert len(first_result.compression_candidates) == 1
    compressed = first_result.compression_candidates[0]
    assert compressed.status is CandidateStatus.AWAITING_APPROVAL
    assert compressed.proposed_change["compression"]["source_candidate_ids"] == sorted(
        [str(first.id), str(second.id)]
    )
    assert compressed.proposed_change["compression"]["compression_ratio"] < 1
    assert (await learning_store.get(first.id)).status is CandidateStatus.ACTIVE
    assert (await learning_store.get(second.id)).status is CandidateStatus.ACTIVE
    assert second_result.compression_candidates == ()


@pytest.mark.asyncio
async def test_compression_replaces_prior_derivation_metadata_instead_of_nesting_it() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    prior_merge = _candidate(
        now=now,
        content="Validate source documents before calculating tax.",
        proposed_extra={
            "merge": {
                "source_candidate_ids": [str(uuid4()), str(uuid4())],
                "source_versions": {},
                "source_statuses": {},
            }
        },
    )
    compatible = _candidate(
        now=now,
        content="Always validate source documents before calculating tax.",
    )

    compressed = await DeterministicCandidateCompressor().compress(
        (prior_merge, compatible),
        policy_version="test-policy",
    )

    assert "merge" not in compressed.proposed_change
    assert "compression" in compressed.proposed_change
