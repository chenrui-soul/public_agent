from __future__ import annotations

import hashlib
import re
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from public_agent.core.trace import RunTrace
from public_agent.core.types import AgentSpec, RunContext, RunResult, RunStatus, utc_now
from public_agent.growth.conflicts import (
    CandidateConflictDetector,
    ConflictKind,
    RuleBasedCandidateConflictDetector,
    assessment_payload,
    merge_compatible_candidates,
    superseding_source_ids,
    superseding_source_statuses,
    superseding_source_versions,
)
from public_agent.growth.models import (
    CandidateRisk,
    CandidateStatus,
    CandidateType,
    EvaluationResult,
    LearningCandidate,
)
from public_agent.growth.service import LearningService, LearningStore
from public_agent.memory.base import MemoryRecord, MemoryStore, MemoryType


class ReflectionContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent: AgentSpec
    run_context: RunContext
    task: str
    result: RunResult
    trace: RunTrace | None = None


class ExtractedKnowledge(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    content: str
    memory_type: MemoryType = MemoryType.SEMANTIC
    risk: CandidateRisk = CandidateRisk.LOW
    confidence: float = Field(default=0.8, ge=0, le=1)
    importance: float = Field(default=0.6, ge=0, le=1)
    evidence_event_ids: tuple[UUID, ...] = ()
    rationale: str | None = None
    tags: tuple[str, ...] = ()
    applicability: str | None = None
    reflection_engine: str | None = None
    reflection_prompt_version: str | None = None


class KnowledgeExtractor(Protocol):
    async def extract(self, context: ReflectionContext) -> tuple[ExtractedKnowledge, ...]:
        """Extract reusable, typed knowledge from one completed run."""


class CandidateEvaluator(Protocol):
    async def evaluate(self, candidate: LearningCandidate) -> EvaluationResult:
        """Evaluate whether a candidate is safe enough to request human approval."""


class KnowledgeAssetPublisher(Protocol):
    async def publish(
        self,
        candidate: LearningCandidate,
        *,
        decided_by: str,
        decision_note: str | None = None,
    ) -> tuple[LearningCandidate, MemoryRecord]:
        """Publish an approved candidate as a retrievable memory asset."""

    async def rollback(
        self,
        candidate: LearningCandidate,
        *,
        memory_id: UUID,
    ) -> LearningCandidate:
        """Rollback a published candidate and deactivate its memory asset."""


class SuccessfulRunKnowledgeExtractor:
    """Safe baseline extractor: proposes successful output, but never publishes it directly."""

    async def extract(self, context: ReflectionContext) -> tuple[ExtractedKnowledge, ...]:
        if context.result.status is not RunStatus.SUCCEEDED:
            return ()
        content = (context.result.output or "").strip()
        if not content:
            return ()
        task = re.sub(r"\s+", " ", context.task).strip()
        title = f"Reusable outcome: {task[:120]}"
        evidence_event_ids: tuple[UUID, ...] = ()
        if context.trace is not None:
            evidence = next(
                (
                    event
                    for event in reversed(context.trace.events)
                    if event.event_type in {"run.succeeded", "model.responded"}
                ),
                None,
            )
            if evidence is not None:
                evidence_event_ids = (evidence.id,)
        return (
            ExtractedKnowledge(
                title=title,
                content=content,
                evidence_event_ids=evidence_event_ids,
                rationale="Successful output proposed by the safe baseline extractor",
            ),
        )


class EvidenceBasedCandidateEvaluator:
    def __init__(self, *, minimum_content_length: int = 12, pass_score: float = 0.75) -> None:
        self._minimum_content_length = minimum_content_length
        self._pass_score = pass_score

    async def evaluate(self, candidate: LearningCandidate) -> EvaluationResult:
        content = str(candidate.proposed_change.get("content", "")).strip()
        has_evidence = bool(candidate.evidence_run_ids)
        has_fingerprint = bool(candidate.fingerprint)
        content_is_substantive = len(content) >= self._minimum_content_length
        score = (
            (0.4 if has_evidence else 0.0)
            + (0.35 if content_is_substantive else 0.0)
            + (0.25 if has_fingerprint else 0.0)
        )
        passed = score >= self._pass_score
        summary = (
            "Candidate has traceable evidence and substantive reusable content"
            if passed
            else "Candidate lacks sufficient evidence or reusable content"
        )
        return EvaluationResult(
            passed=passed,
            score=score,
            summary=summary,
            metrics={
                "has_evidence": float(has_evidence),
                "has_fingerprint": float(has_fingerprint),
                "content_is_substantive": float(content_is_substantive),
            },
        )


class InMemoryKnowledgeAssetPublisher:
    def __init__(self, *, learning: LearningStore, memory: MemoryStore) -> None:
        self._learning = learning
        self._memory = memory

    async def publish(
        self,
        candidate: LearningCandidate,
        *,
        decided_by: str,
        decision_note: str | None = None,
    ) -> tuple[LearningCandidate, MemoryRecord]:
        if candidate.status is not CandidateStatus.APPROVED:
            raise ValueError("Candidate must be approved before publication")
        source_list: list[LearningCandidate] = []
        for source_id in superseding_source_ids(candidate):
            source_list.append(await self._learning.get(source_id))
        sources = tuple(source_list)
        expected_versions = superseding_source_versions(candidate)
        expected_statuses = superseding_source_statuses(candidate)
        terminal = {
            CandidateStatus.REJECTED,
            CandidateStatus.ROLLED_BACK,
            CandidateStatus.EXPIRED,
        }
        if any(source.status in terminal for source in sources):
            raise ValueError("Merged candidate has a terminal source")
        if any(source.version != expected_versions.get(source.id) for source in sources):
            raise ValueError("Merge source changed before publication")
        if any(source.status is not expected_statuses.get(source.id) for source in sources):
            raise ValueError("Merge source status changed before publication")
        memory = memory_from_candidate(
            candidate,
            decided_by=decided_by,
            decision_note=decision_note,
        )
        await self._memory.save(memory)
        for source in sources:
            if source.status in {CandidateStatus.ACTIVE, CandidateStatus.DEPRECATED}:
                await self._memory.deactivate(memory_id_for_candidate(source.id))
            deprecated = source.model_copy(
                update={
                    "status": CandidateStatus.DEPRECATED,
                    "version": source.version + 1,
                    "updated_at": utc_now(),
                }
            )
            await self._learning.save(deprecated)
        active = candidate.model_copy(
            update={
                "status": CandidateStatus.ACTIVE,
                "version": candidate.version + 1,
                "updated_at": utc_now(),
            }
        )
        await self._learning.save(active)
        return active, memory

    async def rollback(
        self,
        candidate: LearningCandidate,
        *,
        memory_id: UUID,
    ) -> LearningCandidate:
        if candidate.status not in {CandidateStatus.ACTIVE, CandidateStatus.DEPRECATED}:
            raise ValueError("Only active or deprecated candidates can be rolled back")
        source_list: list[LearningCandidate] = []
        for source_id in superseding_source_ids(candidate):
            source_list.append(await self._learning.get(source_id))
        sources = tuple(source_list)
        source_versions = superseding_source_versions(candidate)
        source_statuses = superseding_source_statuses(candidate)
        for source in sources:
            original_status = source_statuses.get(source.id)
            if original_status is None:
                raise ValueError("Merged candidate source status metadata is incomplete")
            expected_version = source_versions[source.id]
            if original_status is not CandidateStatus.DEPRECATED:
                expected_version += 1
            if (
                source.status is not CandidateStatus.DEPRECATED
                or source.version != expected_version
            ):
                raise ValueError("Merge source changed before rollback")
        await self._memory.deactivate(memory_id)
        for source in sources:
            original_status = source_statuses[source.id]
            if original_status in {CandidateStatus.ACTIVE, CandidateStatus.DEPRECATED}:
                await self._memory.activate(memory_id_for_candidate(source.id))
            restored = source.model_copy(
                update={
                    "status": original_status,
                    "version": source.version + 1,
                    "updated_at": utc_now(),
                }
            )
            await self._learning.save(restored)
        rolled_back = candidate.model_copy(
            update={
                "status": CandidateStatus.ROLLED_BACK,
                "version": candidate.version + 1,
                "updated_at": utc_now(),
            }
        )
        await self._learning.save(rolled_back)
        return rolled_back


class KnowledgeSedimentationPipeline:
    def __init__(
        self,
        *,
        learning: LearningService,
        learning_store: LearningStore,
        extractor: KnowledgeExtractor,
        evaluator: CandidateEvaluator,
        publisher: KnowledgeAssetPublisher,
        conflict_detector: CandidateConflictDetector | None = None,
    ) -> None:
        self._learning = learning
        self._learning_store = learning_store
        self._extractor = extractor
        self._evaluator = evaluator
        self._publisher = publisher
        self._conflict_detector = conflict_detector or RuleBasedCandidateConflictDetector()

    async def process_run(
        self,
        *,
        agent: AgentSpec,
        context: RunContext,
        task: str,
        result: RunResult,
        trace: RunTrace | None = None,
    ) -> tuple[LearningCandidate, ...]:
        if trace is not None and trace.run_id != result.run_id:
            raise ValueError("Run trace does not match the result being sedimented")
        reflection = ReflectionContext(
            agent=agent,
            run_context=context,
            task=task,
            result=result,
            trace=trace,
        )
        extracted = await self._extractor.extract(reflection)
        processed: list[LearningCandidate] = []
        for item in extracted:
            fingerprint = _fingerprint(agent, context, item)
            candidate = LearningCandidate(
                tenant_id=context.tenant_id,
                agent_id=agent.id,
                domain_id=agent.id,
                candidate_type=CandidateType.MEMORY,
                risk=item.risk,
                title=item.title,
                fingerprint=fingerprint,
                proposed_change={
                    "content": item.content,
                    "memory_type": item.memory_type.value,
                    "namespace": agent.memory_namespace,
                    "confidence": item.confidence,
                    "importance": item.importance,
                    "fingerprint": fingerprint,
                    "evidence_event_ids": [str(event_id) for event_id in item.evidence_event_ids],
                    "rationale": item.rationale,
                    "tags": list(item.tags),
                    "applicability": item.applicability,
                    "reflection_engine": item.reflection_engine,
                    "reflection_prompt_version": item.reflection_prompt_version,
                },
                evidence_run_ids=(result.run_id,),
            )
            conflict_assessments = []
            for existing in await self._learning_store.list_for_conflict(candidate):
                assessment = await self._conflict_detector.assess(candidate, existing)
                if assessment.kind is not ConflictKind.NONE:
                    conflict_assessments.append(assessment)
            if conflict_assessments:
                conflict_assessments.sort(
                    key=lambda item: (item.kind.value, str(item.candidate_id))
                )
                proposed_change = dict(candidate.proposed_change)
                proposed_change["conflict_assessments"] = [
                    assessment_payload(assessment) for assessment in conflict_assessments
                ]
                candidate = candidate.model_copy(update={"proposed_change": proposed_change})
            if not await self._learning.propose_if_absent(candidate):
                continue
            evaluating = await self._learning.begin_evaluation(candidate.id)
            evaluation = await self._evaluator.evaluate(evaluating)
            processed.append(await self._learning.record_evaluation(candidate.id, evaluation))
        return tuple(processed)

    async def merge_candidates(
        self,
        candidate_ids: tuple[UUID, ...],
    ) -> LearningCandidate:
        source_list: list[LearningCandidate] = []
        for candidate_id in candidate_ids:
            source_list.append(await self._learning_store.get(candidate_id))
        sources = tuple(source_list)
        merged = await merge_compatible_candidates(
            sources,
            detector=self._conflict_detector,
        )
        stored, created = await self._learning_store.create_merged_candidate(
            merged,
            source_versions={candidate.id: candidate.version for candidate in sources},
        )
        if not created:
            return stored
        evaluating = await self._learning.begin_evaluation(stored.id)
        evaluation = await self._evaluator.evaluate(evaluating)
        return await self._learning.record_evaluation(stored.id, evaluation)

    async def approve_and_publish(
        self,
        candidate_id: UUID,
        *,
        decided_by: str,
        decision_note: str | None = None,
    ) -> tuple[LearningCandidate, MemoryRecord]:
        approved = await self._learning.approve(candidate_id)
        return await self._publisher.publish(
            approved,
            decided_by=decided_by,
            decision_note=decision_note,
        )

    async def rollback(self, candidate_id: UUID) -> LearningCandidate:
        candidate = await self._learning_store.get(candidate_id)
        return await self._publisher.rollback(
            candidate,
            memory_id=memory_id_for_candidate(candidate.id),
        )


def memory_id_for_candidate(candidate_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"public-agent:learning-candidate:{candidate_id}")


def _fingerprint(
    agent: AgentSpec,
    context: RunContext,
    knowledge: ExtractedKnowledge,
) -> str:
    normalized = re.sub(r"\s+", " ", knowledge.content).strip().casefold()
    scoped = "|".join(
        (
            context.tenant_id,
            agent.id,
            agent.memory_namespace,
            knowledge.memory_type.value,
            normalized,
        )
    )
    return hashlib.sha256(scoped.encode("utf-8")).hexdigest()


def memory_from_candidate(
    candidate: LearningCandidate,
    *,
    decided_by: str,
    decision_note: str | None,
) -> MemoryRecord:
    change = candidate.proposed_change
    source_run_id = candidate.evidence_run_ids[0] if candidate.evidence_run_ids else None
    return MemoryRecord(
        id=memory_id_for_candidate(candidate.id),
        tenant_id=candidate.tenant_id,
        agent_id=candidate.agent_id,
        namespace=str(change["namespace"]),
        memory_type=MemoryType(str(change["memory_type"])),
        content=str(change["content"]),
        confidence=float(change.get("confidence", 0.8)),
        importance=float(change.get("importance", 0.6)),
        metadata={
            "candidate_id": str(candidate.id),
            "domain_id": candidate.domain_id,
            "fingerprint": candidate.fingerprint,
            "source_run_id": str(source_run_id) if source_run_id else None,
            "approved_by": decided_by,
            "decision_note": decision_note,
            "evidence_event_ids": list(change.get("evidence_event_ids", [])),
            "evidence_run_ids": [str(run_id) for run_id in candidate.evidence_run_ids],
            "rationale": change.get("rationale"),
            "tags": list(change.get("tags", [])),
            "applicability": change.get("applicability"),
            "reflection_engine": change.get("reflection_engine"),
            "reflection_prompt_version": change.get("reflection_prompt_version"),
            "merge": change.get("merge"),
            "compression": change.get("compression"),
        },
        expires_at=candidate.expires_at,
    )
