from __future__ import annotations

import re
from difflib import SequenceMatcher
from enum import StrEnum
from itertools import combinations
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from public_agent.growth.models import CandidateRisk, CandidateStatus, LearningCandidate


class ConflictKind(StrEnum):
    NONE = "none"
    DUPLICATE = "duplicate"
    COMPATIBLE = "compatible"
    CONTRADICTORY = "contradictory"


class ConflictAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: UUID
    kind: ConflictKind
    score: float = Field(ge=0, le=1)
    reason: str
    detector_version: str


class CandidateConflictDetector(Protocol):
    version: str

    async def assess(
        self,
        proposed: LearningCandidate,
        existing: LearningCandidate,
    ) -> ConflictAssessment:
        """Classify one scoped candidate pair conservatively."""


class RuleBasedCandidateConflictDetector:
    """Conservative baseline detector that fails open to NONE when evidence is weak."""

    version = "rules-v1"

    def __init__(
        self,
        *,
        compatibility_threshold: float = 0.72,
        contradiction_threshold: float = 0.86,
    ) -> None:
        if not 0 <= compatibility_threshold <= 1:
            raise ValueError("Compatibility threshold must be between 0 and 1")
        if not 0 <= contradiction_threshold <= 1:
            raise ValueError("Contradiction threshold must be between 0 and 1")
        self._compatibility_threshold = compatibility_threshold
        self._contradiction_threshold = contradiction_threshold

    async def assess(
        self,
        proposed: LearningCandidate,
        existing: LearningCandidate,
    ) -> ConflictAssessment:
        if not _same_merge_scope(proposed, existing):
            return self._assessment(existing, ConflictKind.NONE, 0, "Candidate scopes differ")
        if proposed.fingerprint == existing.fingerprint:
            return self._assessment(
                existing,
                ConflictKind.DUPLICATE,
                1,
                "Scoped candidate fingerprints are identical",
            )

        proposed_content = str(proposed.proposed_change.get("content", ""))
        existing_content = str(existing.proposed_change.get("content", ""))
        if not proposed_content.strip() or not existing_content.strip():
            return self._assessment(
                existing,
                ConflictKind.NONE,
                0,
                "Both candidates need textual content for semantic comparison",
            )

        proposed_negative, proposed_core = _polarity_and_core(proposed_content)
        existing_negative, existing_core = _polarity_and_core(existing_content)
        score = _similarity(proposed_core, existing_core)
        if proposed_negative != existing_negative and score >= self._contradiction_threshold:
            return self._assessment(
                existing,
                ConflictKind.CONTRADICTORY,
                score,
                "Candidates express opposite polarity for the same normalized proposition",
            )
        if proposed_negative == existing_negative and score >= self._compatibility_threshold:
            return self._assessment(
                existing,
                ConflictKind.COMPATIBLE,
                score,
                "Candidates express compatible wording for the same scoped knowledge",
            )
        return self._assessment(
            existing,
            ConflictKind.NONE,
            score,
            "Semantic overlap is below the conservative conflict threshold",
        )

    def _assessment(
        self,
        existing: LearningCandidate,
        kind: ConflictKind,
        score: float,
        reason: str,
    ) -> ConflictAssessment:
        return ConflictAssessment(
            candidate_id=existing.id,
            kind=kind,
            score=round(score, 4),
            reason=reason,
            detector_version=self.version,
        )


async def merge_compatible_candidates(
    sources: tuple[LearningCandidate, ...],
    *,
    detector: CandidateConflictDetector,
) -> LearningCandidate:
    unique = {candidate.id: candidate for candidate in sources}
    ordered = tuple(unique[candidate_id] for candidate_id in sorted(unique, key=str))
    if len(ordered) < 2:
        raise ValueError("At least two distinct candidates are required for a merge")
    if len(ordered) > 20:
        raise ValueError("At most 20 candidates can participate in one merge")

    assessments: list[ConflictAssessment] = []
    for left, right in combinations(ordered, 2):
        assessment = await detector.assess(left, right)
        if assessment.kind is ConflictKind.CONTRADICTORY:
            raise ValueError(
                f"Cannot merge contradictory candidates: {left.id} and {right.id}"
            )
        if assessment.kind is ConflictKind.NONE:
            raise ValueError(f"Candidates are not compatible for merge: {left.id} and {right.id}")
        assessments.append(assessment)

    selected = max(
        ordered,
        key=lambda candidate: (
            len(_normalize(str(candidate.proposed_change.get("content", "")))),
            float(candidate.proposed_change.get("importance", 0.0)),
            float(candidate.proposed_change.get("confidence", 0.0)),
            str(candidate.id),
        ),
    )
    source_ids = [str(candidate.id) for candidate in ordered]
    event_ids = sorted(
        {
            str(event_id)
            for candidate in ordered
            for event_id in _string_collection(candidate, "evidence_event_ids")
        }
    )
    tags = sorted(
        {
            str(tag)
            for candidate in ordered
            for tag in _string_collection(candidate, "tags")
        }
    )
    run_ids = tuple(
        sorted(
            {run_id for candidate in ordered for run_id in candidate.evidence_run_ids},
            key=str,
        )
    )
    merge_id = uuid5(
        NAMESPACE_URL,
        "public-agent:learning-candidate-merge:v1:"
        + "|".join(f"{candidate.id}:{candidate.fingerprint}" for candidate in ordered),
    )
    decision = (
        ConflictKind.COMPATIBLE
        if any(item.kind is ConflictKind.COMPATIBLE for item in assessments)
        else ConflictKind.DUPLICATE
    )
    proposed_change = dict(selected.proposed_change)
    proposed_change.update(
        {
            "fingerprint": selected.fingerprint,
            "evidence_event_ids": event_ids,
            "tags": tags,
            "confidence": min(
                float(candidate.proposed_change.get("confidence", 0.8))
                for candidate in ordered
            ),
            "importance": max(
                float(candidate.proposed_change.get("importance", 0.6))
                for candidate in ordered
            ),
            "merge": {
                "source_candidate_ids": source_ids,
                "source_fingerprints": [candidate.fingerprint for candidate in ordered],
                "source_versions": {
                    str(candidate.id): candidate.version for candidate in ordered
                },
                "source_statuses": {
                    str(candidate.id): candidate.status.value for candidate in ordered
                },
                "conflict_decision": decision.value,
                "conflict_detector_version": detector.version,
                "rationale": "Merged only after every source pair was classified as compatible "
                "or duplicate",
                "pair_assessments": [
                    assessment.model_dump(mode="json") for assessment in assessments
                ],
            },
        }
    )
    risk_order = {
        CandidateRisk.LOW: 0,
        CandidateRisk.MEDIUM: 1,
        CandidateRisk.HIGH: 2,
    }
    return LearningCandidate(
        id=merge_id,
        tenant_id=selected.tenant_id,
        agent_id=selected.agent_id,
        domain_id=selected.domain_id,
        candidate_type=selected.candidate_type,
        risk=max((candidate.risk for candidate in ordered), key=risk_order.__getitem__),
        title=f"Merged: {selected.title}"[:300],
        fingerprint=selected.fingerprint,
        proposed_change=proposed_change,
        evidence_run_ids=run_ids,
    )


def assessment_payload(assessment: ConflictAssessment) -> dict[str, object]:
    return {
        "candidate_id": str(assessment.candidate_id),
        "kind": assessment.kind.value,
        "score": assessment.score,
        "reason": assessment.reason,
        "detector_version": assessment.detector_version,
    }


def merged_source_ids(candidate: LearningCandidate) -> tuple[UUID, ...]:
    merge = candidate.proposed_change.get("merge")
    if not isinstance(merge, dict):
        return ()
    raw_ids = merge.get("source_candidate_ids", [])
    if not isinstance(raw_ids, list):
        return ()
    return tuple(UUID(str(candidate_id)) for candidate_id in raw_ids)


def merged_source_versions(candidate: LearningCandidate) -> dict[UUID, int]:
    merge = candidate.proposed_change.get("merge")
    if not isinstance(merge, dict):
        return {}
    raw_versions = merge.get("source_versions")
    if not isinstance(raw_versions, dict):
        return {}
    return {UUID(str(source_id)): int(version) for source_id, version in raw_versions.items()}


def merged_source_statuses(candidate: LearningCandidate) -> dict[UUID, CandidateStatus]:
    merge = candidate.proposed_change.get("merge")
    if not isinstance(merge, dict):
        return {}
    raw_statuses = merge.get("source_statuses")
    if not isinstance(raw_statuses, dict):
        return {}
    return {
        UUID(str(source_id)): CandidateStatus(str(status))
        for source_id, status in raw_statuses.items()
    }


def superseding_source_ids(candidate: LearningCandidate) -> tuple[UUID, ...]:
    return _derived_source_ids(candidate, _derivation_key(candidate))


def superseding_source_versions(candidate: LearningCandidate) -> dict[UUID, int]:
    return _derived_source_versions(candidate, _derivation_key(candidate))


def superseding_source_statuses(candidate: LearningCandidate) -> dict[UUID, CandidateStatus]:
    return _derived_source_statuses(candidate, _derivation_key(candidate))


def _derivation_key(candidate: LearningCandidate) -> str | None:
    keys = [
        key
        for key in ("merge", "compression")
        if isinstance(candidate.proposed_change.get(key), dict)
    ]
    if len(keys) > 1:
        raise ValueError("Candidate cannot be both merged and compressed")
    return keys[0] if keys else None


def _derived_source_ids(
    candidate: LearningCandidate,
    key: str | None,
) -> tuple[UUID, ...]:
    if key is None:
        return ()
    derivation = candidate.proposed_change.get(key)
    if not isinstance(derivation, dict):
        return ()
    raw_ids = derivation.get("source_candidate_ids", [])
    if not isinstance(raw_ids, list):
        return ()
    return tuple(UUID(str(candidate_id)) for candidate_id in raw_ids)


def _derived_source_versions(
    candidate: LearningCandidate,
    key: str | None,
) -> dict[UUID, int]:
    if key is None:
        return {}
    derivation = candidate.proposed_change.get(key)
    if not isinstance(derivation, dict):
        return {}
    raw_versions = derivation.get("source_versions")
    if not isinstance(raw_versions, dict):
        return {}
    return {UUID(str(source_id)): int(version) for source_id, version in raw_versions.items()}


def _derived_source_statuses(
    candidate: LearningCandidate,
    key: str | None,
) -> dict[UUID, CandidateStatus]:
    if key is None:
        return {}
    derivation = candidate.proposed_change.get(key)
    if not isinstance(derivation, dict):
        return {}
    raw_statuses = derivation.get("source_statuses")
    if not isinstance(raw_statuses, dict):
        return {}
    return {
        UUID(str(source_id)): CandidateStatus(str(status))
        for source_id, status in raw_statuses.items()
    }


def _same_merge_scope(left: LearningCandidate, right: LearningCandidate) -> bool:
    if (
        left.tenant_id != right.tenant_id
        or left.agent_id != right.agent_id
        or left.domain_id != right.domain_id
        or left.candidate_type != right.candidate_type
    ):
        return False
    return all(
        left.proposed_change.get(key) == right.proposed_change.get(key)
        for key in ("namespace", "memory_type")
    )


_NEGATION_PATTERN = re.compile(
    r"\b(?:do\s+not|does\s+not|did\s+not|must\s+not|should\s+not|cannot|can\s+not|"
    r"never|not|forbidden|prohibited)\b|(?:不要|不得|禁止|不能|不应|不可|勿)",
    re.IGNORECASE,
)


def _polarity_and_core(content: str) -> tuple[bool, str]:
    negative = _NEGATION_PATTERN.search(content) is not None
    return negative, _normalize(_NEGATION_PATTERN.sub(" ", content))


def _normalize(content: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", content.casefold()).strip()


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0
    left_terms = set(left.split())
    right_terms = set(right.split())
    token_score = len(left_terms & right_terms) / max(len(left_terms | right_terms), 1)
    sequence_score = SequenceMatcher(a=left, b=right, autojunk=False).ratio()
    return max(token_score, sequence_score)


def _string_collection(candidate: LearningCandidate, key: str) -> tuple[object, ...]:
    value = candidate.proposed_change.get(key, [])
    if not isinstance(value, (list, tuple, set)):
        raise ValueError(f"Candidate {candidate.id} has invalid {key} metadata")
    return tuple(value)
