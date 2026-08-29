from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from public_agent.growth.conflicts import (
    CandidateConflictDetector,
    ConflictKind,
    RuleBasedCandidateConflictDetector,
)
from public_agent.growth.models import (
    CandidateRisk,
    CandidateStatus,
    EvaluationResult,
    LearningCandidate,
)
from public_agent.growth.service import LearningService


class GovernanceAction(StrEnum):
    EXPIRE = "expire"
    EVICT = "evict"
    COMPRESS = "compress"


class GovernanceReason(StrEnum):
    EXPLICIT_EXPIRY = "explicit_expiry"
    STALE_PENDING = "stale_pending"
    LOW_VALUE = "low_value"
    COMPATIBLE_COMPRESSION = "compatible_compression"


class CandidateGovernancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = Field(default="candidate-governance-v1", min_length=1, max_length=100)
    pending_ttl_days: int = Field(default=30, ge=1, le=3_650)
    low_value_min_age_days: int = Field(default=60, ge=1, le=3_650)
    idle_days: int = Field(default=90, ge=1, le=3_650)
    low_value_threshold: float = Field(default=0.35, ge=0, le=1)
    protected_importance: float = Field(default=0.85, ge=0, le=1)
    protected_confidence: float = Field(default=0.9, ge=0, le=1)
    recall_saturation_count: int = Field(default=10, ge=1, le=1_000_000)
    max_batch_size: int = Field(default=100, ge=1, le=500)
    compression_min_sources: int = Field(default=2, ge=2, le=20)
    compression_max_sources: int = Field(default=10, ge=2, le=20)
    compression_min_age_days: int = Field(default=30, ge=0, le=3_650)

    @model_validator(mode="after")
    def validate_compression_bounds(self) -> CandidateGovernancePolicy:
        if self.compression_min_sources > self.compression_max_sources:
            raise ValueError("Compression minimum cannot exceed its maximum")
        return self


class CandidateGovernanceCursor(BaseModel):
    model_config = ConfigDict(frozen=True)

    created_at: datetime
    candidate_id: UUID

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _as_utc(value)


class CandidateGovernanceQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    agent_id: str
    domain_id: str | None = None
    as_of: datetime
    limit: int = Field(default=100, ge=1, le=500)
    after: CandidateGovernanceCursor | None = None

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return _as_utc(value)


class CandidateGovernanceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: LearningCandidate
    latest_evaluation_score: float | None = Field(default=None, ge=0, le=1)
    memory_status: str | None = None
    memory_confidence: float | None = Field(default=None, ge=0, le=1)
    memory_importance: float | None = Field(default=None, ge=0, le=1)
    recall_count: int = Field(default=0, ge=0)
    last_recalled_at: datetime | None = None
    has_live_descendant: bool = False


class CandidateGovernancePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CandidateGovernanceSnapshot, ...]
    next_cursor: CandidateGovernanceCursor | None = None


class CandidateGovernanceDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: UUID
    tenant_id: str
    agent_id: str
    domain_id: str
    expected_version: int = Field(ge=1)
    expected_status: CandidateStatus
    expected_recall_count: int = Field(ge=0)
    action: GovernanceAction
    reason: GovernanceReason
    value_score: float = Field(ge=0, le=1)
    policy_version: str = Field(min_length=1, max_length=100)
    decided_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=500)

    @field_validator("decided_at")
    @classmethod
    def normalize_decided_at(cls, value: datetime) -> datetime:
        return _as_utc(value)


class GovernanceApplyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: LearningCandidate
    applied: bool


class CandidateGovernanceBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    processed: int
    expired: int
    evicted: int
    compression_candidates: tuple[LearningCandidate, ...] = ()
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    conflicts: int = 0
    next_cursor: CandidateGovernanceCursor | None = None


class CandidateEvaluator(Protocol):
    async def evaluate(self, candidate: LearningCandidate) -> EvaluationResult:
        """Evaluate one compression candidate before it can request approval."""


class CandidateCompressor(Protocol):
    version: str

    async def compress(
        self,
        sources: tuple[LearningCandidate, ...],
        *,
        policy_version: str,
    ) -> LearningCandidate:
        """Create a new immutable candidate that preserves source lineage."""


class CandidateGovernanceRepository(Protocol):
    async def scan(self, query: CandidateGovernanceQuery) -> CandidateGovernancePage:
        """Return one bounded, keyset-ordered page of candidate governance snapshots."""

    async def apply(
        self,
        decision: CandidateGovernanceDecision,
        *,
        policy: CandidateGovernancePolicy,
    ) -> GovernanceApplyResult:
        """Atomically apply one guarded governance decision and its audit record."""

    async def create_compression(
        self,
        candidate: LearningCandidate,
        *,
        source_versions: dict[UUID, int],
        policy_version: str,
        value_score: float,
    ) -> tuple[LearningCandidate, bool]:
        """Atomically create one compression candidate, lineage, and audit records."""


class DeterministicCandidateCompressor:
    """No-model baseline that keeps the shortest compatible source and unions evidence."""

    version = "deterministic-compatible-v1"

    async def compress(
        self,
        sources: tuple[LearningCandidate, ...],
        *,
        policy_version: str,
    ) -> LearningCandidate:
        ordered = _validated_sources(sources)
        selected = min(
            ordered,
            key=lambda candidate: (
                -_number(candidate, "importance", 0.6),
                -_number(candidate, "confidence", 0.8),
                len(_normalized_content(candidate)),
                str(candidate.id),
            ),
        )
        source_ids = [str(candidate.id) for candidate in ordered]
        contents = [_content(candidate) for candidate in ordered]
        total_chars = sum(len(content) for content in contents)
        compressed_content = _content(selected)
        if not compressed_content:
            raise ValueError("Compression sources require non-empty content")

        event_ids = sorted(
            {
                str(event_id)
                for candidate in ordered
                for event_id in _collection(candidate, "evidence_event_ids")
            }
        )
        tags = sorted({str(tag) for candidate in ordered for tag in _collection(candidate, "tags")})
        run_ids = tuple(
            sorted(
                {run_id for candidate in ordered for run_id in candidate.evidence_run_ids},
                key=str,
            )
        )
        source_signature = "|".join(
            f"{candidate.id}:{candidate.version}:{candidate.fingerprint}" for candidate in ordered
        )
        compression_id = uuid5(
            NAMESPACE_URL,
            "public-agent:candidate-compression:"
            f"{self.version}:{policy_version}:{source_signature}",
        )
        proposed_change = dict(selected.proposed_change)
        proposed_change.pop("merge", None)
        proposed_change.pop("compression", None)
        proposed_change.update(
            {
                "content": compressed_content,
                "evidence_event_ids": event_ids,
                "tags": tags,
                "confidence": min(_number(candidate, "confidence", 0.8) for candidate in ordered),
                "importance": max(_number(candidate, "importance", 0.6) for candidate in ordered),
                "compression": {
                    "source_candidate_ids": source_ids,
                    "source_fingerprints": [candidate.fingerprint for candidate in ordered],
                    "source_versions": {
                        str(candidate.id): candidate.version for candidate in ordered
                    },
                    "source_statuses": {
                        str(candidate.id): candidate.status.value for candidate in ordered
                    },
                    "compressor_version": self.version,
                    "policy_version": policy_version,
                    "source_character_count": total_chars,
                    "compressed_character_count": len(compressed_content),
                    "compression_ratio": round(len(compressed_content) / max(total_chars, 1), 6),
                    "rationale": (
                        "Created from candidates whose every pair was classified as "
                        "compatible or duplicate; publication still requires evaluation "
                        "and human approval"
                    ),
                },
            }
        )
        risk_order = {
            CandidateRisk.LOW: 0,
            CandidateRisk.MEDIUM: 1,
            CandidateRisk.HIGH: 2,
        }
        expiries = [candidate.expires_at for candidate in ordered if candidate.expires_at]
        protections = [
            candidate.protected_until for candidate in ordered if candidate.protected_until
        ]
        return LearningCandidate(
            id=compression_id,
            tenant_id=selected.tenant_id,
            agent_id=selected.agent_id,
            domain_id=selected.domain_id,
            candidate_type=selected.candidate_type,
            risk=max((candidate.risk for candidate in ordered), key=risk_order.__getitem__),
            title=f"Compressed: {selected.title}"[:300],
            fingerprint=selected.fingerprint,
            proposed_change=proposed_change,
            evidence_run_ids=run_ids,
            expires_at=min((_as_utc(value) for value in expiries), default=None),
            protected_until=max(
                (_as_utc(value) for value in protections),
                default=None,
            ),
        )


class CandidateGovernanceService:
    def __init__(
        self,
        *,
        repository: CandidateGovernanceRepository,
        learning: LearningService,
        evaluator: CandidateEvaluator,
        policy: CandidateGovernancePolicy | None = None,
        conflict_detector: CandidateConflictDetector | None = None,
        compressor: CandidateCompressor | None = None,
    ) -> None:
        self._repository = repository
        self._learning = learning
        self._evaluator = evaluator
        self._policy = policy or CandidateGovernancePolicy()
        self._conflict_detector = conflict_detector or RuleBasedCandidateConflictDetector()
        self._compressor = compressor or DeterministicCandidateCompressor()

    async def run_batch(
        self,
        query: CandidateGovernanceQuery,
    ) -> CandidateGovernanceBatchResult:
        if query.limit > self._policy.max_batch_size:
            raise ValueError("Governance query exceeds the configured batch size")
        page = await self._repository.scan(query)
        skipped: Counter[str] = Counter()
        excluded_from_compression: set[UUID] = set()
        expired = 0
        evicted = 0
        conflicts = 0

        for snapshot in page.items:
            decision, skipped_reason = governance_decision(
                snapshot,
                policy=self._policy,
                as_of=query.as_of,
            )
            if decision is None:
                if skipped_reason:
                    skipped[skipped_reason] += 1
                continue
            result = await self._repository.apply(decision, policy=self._policy)
            if not result.applied:
                conflicts += 1
                excluded_from_compression.add(snapshot.candidate.id)
                continue
            excluded_from_compression.add(snapshot.candidate.id)
            if decision.action is GovernanceAction.EXPIRE:
                expired += 1
            elif decision.action is GovernanceAction.EVICT:
                evicted += 1

        compression_candidates: list[LearningCandidate] = []
        eligible = tuple(
            snapshot
            for snapshot in page.items
            if snapshot.candidate.id not in excluded_from_compression
            and _compression_protection_reason(
                snapshot,
                policy=self._policy,
                as_of=query.as_of,
            )
            is None
        )
        for group in await self._compression_groups(eligible):
            compressed = await self._compressor.compress(
                tuple(snapshot.candidate for snapshot in group),
                policy_version=self._policy.version,
            )
            stored, created = await self._repository.create_compression(
                compressed,
                source_versions={
                    snapshot.candidate.id: snapshot.candidate.version for snapshot in group
                },
                policy_version=self._policy.version,
                value_score=round(
                    sum(
                        candidate_value_score(
                            snapshot,
                            self._policy,
                            as_of=query.as_of,
                        )
                        for snapshot in group
                    )
                    / len(group),
                    6,
                ),
            )
            if not created:
                continue
            evaluating = await self._learning.begin_evaluation(stored.id)
            evaluation = await self._evaluator.evaluate(evaluating)
            compression_candidates.append(
                await self._learning.record_evaluation(stored.id, evaluation)
            )

        return CandidateGovernanceBatchResult(
            processed=len(page.items),
            expired=expired,
            evicted=evicted,
            compression_candidates=tuple(compression_candidates),
            skipped_reasons=dict(sorted(skipped.items())),
            conflicts=conflicts,
            next_cursor=page.next_cursor,
        )

    async def _compression_groups(
        self,
        snapshots: tuple[CandidateGovernanceSnapshot, ...],
    ) -> tuple[tuple[CandidateGovernanceSnapshot, ...], ...]:
        by_scope: dict[tuple[str, ...], list[CandidateGovernanceSnapshot]] = defaultdict(list)
        for snapshot in snapshots:
            candidate = snapshot.candidate
            by_scope[
                (
                    candidate.tenant_id,
                    candidate.agent_id,
                    candidate.domain_id,
                    candidate.candidate_type.value,
                    str(candidate.proposed_change.get("namespace", "")),
                    str(candidate.proposed_change.get("memory_type", "")),
                )
            ].append(snapshot)

        groups: list[tuple[CandidateGovernanceSnapshot, ...]] = []
        for candidates in by_scope.values():
            remaining = sorted(
                candidates,
                key=lambda item: (item.candidate.created_at, str(item.candidate.id)),
            )
            while remaining:
                cluster = [remaining.pop(0)]
                index = 0
                while (
                    index < len(remaining) and len(cluster) < self._policy.compression_max_sources
                ):
                    proposed = remaining[index]
                    assessments = [
                        await self._conflict_detector.assess(
                            proposed.candidate,
                            existing.candidate,
                        )
                        for existing in cluster
                    ]
                    if all(
                        assessment.kind in {ConflictKind.DUPLICATE, ConflictKind.COMPATIBLE}
                        for assessment in assessments
                    ):
                        cluster.append(remaining.pop(index))
                    else:
                        index += 1
                if len(cluster) >= self._policy.compression_min_sources:
                    groups.append(tuple(cluster))
        return tuple(groups)


def governance_decision(
    snapshot: CandidateGovernanceSnapshot,
    *,
    policy: CandidateGovernancePolicy,
    as_of: datetime,
) -> tuple[CandidateGovernanceDecision | None, str | None]:
    protection = governance_protection_reason(snapshot, policy=policy, as_of=as_of)
    if protection is not None:
        return None, protection

    candidate = snapshot.candidate
    action: GovernanceAction | None = None
    reason: GovernanceReason | None = None
    if candidate.expires_at is not None and _as_utc(candidate.expires_at) <= _as_utc(as_of):
        action = GovernanceAction.EXPIRE
        reason = GovernanceReason.EXPLICIT_EXPIRY
    elif candidate.status is CandidateStatus.PENDING and _age(candidate, as_of) >= timedelta(
        days=policy.pending_ttl_days
    ):
        action = GovernanceAction.EXPIRE
        reason = GovernanceReason.STALE_PENDING
    elif (
        candidate.status in {CandidateStatus.ACTIVE, CandidateStatus.DEPRECATED}
        and _age(candidate, as_of) >= timedelta(days=policy.low_value_min_age_days)
        and _is_idle(snapshot, as_of=as_of, idle_days=policy.idle_days)
        and candidate_value_score(snapshot, policy, as_of=as_of) <= policy.low_value_threshold
    ):
        action = GovernanceAction.EVICT
        reason = GovernanceReason.LOW_VALUE

    if action is None or reason is None:
        return None, "no_action"
    score = candidate_value_score(snapshot, policy, as_of=as_of)
    idempotency_key = f"{candidate.id}:{candidate.version}:{action.value}:{policy.version}"
    return (
        CandidateGovernanceDecision(
            candidate_id=candidate.id,
            tenant_id=candidate.tenant_id,
            agent_id=candidate.agent_id,
            domain_id=candidate.domain_id,
            expected_version=candidate.version,
            expected_status=candidate.status,
            expected_recall_count=snapshot.recall_count,
            action=action,
            reason=reason,
            value_score=score,
            policy_version=policy.version,
            decided_at=as_of,
            idempotency_key=idempotency_key,
        ),
        None,
    )


def governance_protection_reason(
    snapshot: CandidateGovernanceSnapshot,
    *,
    policy: CandidateGovernancePolicy,
    as_of: datetime,
) -> str | None:
    candidate = snapshot.candidate
    if candidate.status in {
        CandidateStatus.EVALUATING,
        CandidateStatus.AWAITING_APPROVAL,
        CandidateStatus.APPROVED,
    }:
        return "approval_or_evaluation_in_progress"
    if candidate.risk is CandidateRisk.HIGH:
        return "high_risk"
    governance = candidate.proposed_change.get("governance")
    if isinstance(governance, dict) and governance.get("protected") is True:
        return "explicitly_protected"
    if candidate.protected_until is not None and _as_utc(candidate.protected_until) > _as_utc(
        as_of
    ):
        return "protected_until"
    if snapshot.has_live_descendant:
        return "referenced_by_live_candidate"
    if snapshot.memory_status is not None and (
        (snapshot.memory_importance or 0) >= policy.protected_importance
        or (snapshot.memory_confidence or 0) >= policy.protected_confidence
    ):
        return "high_value_memory"
    return None


def candidate_value_score(
    snapshot: CandidateGovernanceSnapshot,
    policy: CandidateGovernancePolicy,
    *,
    as_of: datetime,
) -> float:
    evaluation = snapshot.latest_evaluation_score or 0.0
    importance = snapshot.memory_importance
    if importance is None:
        importance = _number(snapshot.candidate, "importance", 0.0)
    confidence = snapshot.memory_confidence
    if confidence is None:
        confidence = _number(snapshot.candidate, "confidence", 0.0)
    usage = min(snapshot.recall_count / policy.recall_saturation_count, 1.0)
    recency = 0.0
    if snapshot.last_recalled_at is not None:
        elapsed = max(
            (_as_utc(as_of) - _as_utc(snapshot.last_recalled_at)).total_seconds(),
            0.0,
        )
        window = timedelta(days=policy.idle_days).total_seconds()
        recency = max(0.0, 1.0 - elapsed / max(window, 1.0))
    return round(
        evaluation * 0.3 + importance * 0.25 + confidence * 0.2 + usage * 0.15 + recency * 0.1,
        6,
    )


def _compression_protection_reason(
    snapshot: CandidateGovernanceSnapshot,
    *,
    policy: CandidateGovernancePolicy,
    as_of: datetime,
) -> str | None:
    candidate = snapshot.candidate
    if candidate.status is not CandidateStatus.ACTIVE:
        return "not_active"
    if _age(candidate, as_of) < timedelta(days=policy.compression_min_age_days):
        return "too_new_for_compression"
    if candidate.risk is CandidateRisk.HIGH:
        return "high_risk"
    governance = candidate.proposed_change.get("governance")
    if isinstance(governance, dict) and governance.get("protected") is True:
        return "explicitly_protected"
    if candidate.protected_until is not None and _as_utc(candidate.protected_until) > _as_utc(
        as_of
    ):
        return "protected_until"
    if snapshot.has_live_descendant:
        return "referenced_by_live_candidate"
    return None


def _validated_sources(
    sources: tuple[LearningCandidate, ...],
) -> tuple[LearningCandidate, ...]:
    unique = {candidate.id: candidate for candidate in sources}
    ordered = tuple(unique[candidate_id] for candidate_id in sorted(unique, key=str))
    if len(ordered) < 2:
        raise ValueError("At least two distinct candidates are required for compression")
    if len(ordered) > 20:
        raise ValueError("At most 20 candidates can participate in one compression")
    selected = ordered[0]
    for candidate in ordered:
        if candidate.status is not CandidateStatus.ACTIVE:
            raise ValueError("Compression sources must be active")
        if (
            candidate.tenant_id != selected.tenant_id
            or candidate.agent_id != selected.agent_id
            or candidate.domain_id != selected.domain_id
            or candidate.candidate_type is not selected.candidate_type
        ):
            raise ValueError("Compression sources must share the same candidate scope")
        if any(
            candidate.proposed_change.get(key) != selected.proposed_change.get(key)
            for key in ("namespace", "memory_type")
        ):
            raise ValueError("Compression sources must share memory scope and type")
    return ordered


def _content(candidate: LearningCandidate) -> str:
    return str(candidate.proposed_change.get("content", "")).strip()


def _normalized_content(candidate: LearningCandidate) -> str:
    return re.sub(r"\s+", " ", _content(candidate)).casefold()


def _number(candidate: LearningCandidate, key: str, default: float) -> float:
    value = candidate.proposed_change.get(key, default)
    if not isinstance(value, (int, float)):
        return default
    return min(max(float(value), 0.0), 1.0)


def _collection(candidate: LearningCandidate, key: str) -> tuple[object, ...]:
    value = candidate.proposed_change.get(key, [])
    if not isinstance(value, (list, tuple, set)):
        raise ValueError(f"Candidate {candidate.id} has invalid {key} metadata")
    return tuple(value)


def _age(candidate: LearningCandidate, as_of: datetime) -> timedelta:
    return _as_utc(as_of) - _as_utc(candidate.created_at)


def _is_idle(
    snapshot: CandidateGovernanceSnapshot,
    *,
    as_of: datetime,
    idle_days: int,
) -> bool:
    if snapshot.last_recalled_at is None:
        return True
    return _as_utc(snapshot.last_recalled_at) <= _as_utc(as_of) - timedelta(days=idle_days)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
