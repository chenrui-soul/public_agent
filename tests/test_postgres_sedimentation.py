import asyncio
import json
import os
from collections import deque
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from public_agent.application import PersistentAgentService
from public_agent.config import Settings
from public_agent.core.runtime import AgentRuntime
from public_agent.core.types import (
    AgentSpec,
    ModelRequest,
    ModelResponse,
    RunContext,
    RunResult,
    RunStatus,
    ToolCall,
    ToolDefinition,
)
from public_agent.factory import Agent
from public_agent.growth.conflicts import RuleBasedCandidateConflictDetector
from public_agent.growth.models import (
    CandidateRisk,
    CandidateStatus,
    CandidateType,
    LearningCandidate,
)
from public_agent.growth.pipeline import (
    EvidenceBasedCandidateEvaluator,
    ExtractedKnowledge,
    KnowledgeSedimentationPipeline,
    ReflectionContext,
)
from public_agent.growth.reflection import ReflectionEngine
from public_agent.growth.service import LearningService
from public_agent.memory.base import MemoryQuery, MemoryType
from public_agent.providers.testing import ScriptedModelProvider
from public_agent.storage.database import Database
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    ApprovalModel,
    EvaluationModel,
    LearningCandidateModel,
    MemoryModel,
    RunEventModel,
    RunModel,
    TenantModel,
)
from public_agent.storage.repositories import (
    PostgresKnowledgeAssetPublisher,
    PostgresLearningStore,
    PostgresMemoryStore,
)
from public_agent.storage.runs import PostgresRunPersistence
from public_agent.tools.base import FunctionTool, ToolContext
from public_agent.tools.registry import ToolRegistry

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


class TraceAwareReflectionProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        trace = json.loads(request.messages[-1].content)
        evidence = next(
            event
            for event in reversed(trace["events"])
            if event["event_type"] == "model.responded" and event["payload"]["content"]
        )
        content = evidence["payload"]["content"]
        return ModelResponse(
            content=json.dumps(
                {
                    "items": [
                        {
                            "title": "Validate source documents first",
                            "content": content,
                            "memory_type": "procedural",
                            "risk": "low",
                            "confidence": 0.95,
                            "importance": 0.85,
                            "rationale": "The final model response passed runtime verification.",
                            "evidence_event_ids": [evidence["event_id"]],
                            "tags": ["tax", "validation"],
                            "applicability": "Tax workflows that depend on source documents.",
                        }
                    ]
                }
            )
        )


class QueueExtractor:
    def __init__(self, *items: ExtractedKnowledge) -> None:
        self._items = deque(items)

    async def extract(self, context: ReflectionContext) -> tuple[ExtractedKnowledge, ...]:
        del context
        return (self._items.popleft(),)


async def validate_documents(
    arguments: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    del context
    return {"validated": bool(arguments["document_count"])}


def validation_tool() -> FunctionTool:
    return FunctionTool(
        ToolDefinition(
            name="validate_documents",
            description="Validate source documents before tax calculation",
            input_schema={
                "type": "object",
                "properties": {"document_count": {"type": "integer", "minimum": 1}},
                "required": ["document_count"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"validated": {"type": "boolean"}},
                "required": ["validated"],
                "additionalProperties": False,
            },
        ),
        validate_documents,
    )


@pytest.mark.asyncio
async def test_postgres_run_to_approved_memory_to_recall_and_rollback() -> None:
    database = Database(Settings().database_url)
    tenant_id = uuid4()
    agent_id = uuid4()
    version_id = uuid4()
    tenant_slug = f"tenant-{tenant_id.hex[:12]}"
    agent_key = f"tax-agent-{agent_id.hex[:12]}"
    spec = AgentSpec(
        id=agent_key,
        name="Tax Agent",
        version="0.1.0",
        instructions="Answer with verified tax workflow guidance.",
        memory_namespace="tax-workflow",
        allowed_tools=("validate_documents",),
    )

    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Test Tenant"))
            await session.flush()
            session.add(
                AgentModel(
                    id=agent_id,
                    tenant_id=tenant_id,
                    agent_key=agent_key,
                    name=spec.name,
                    domain_id=agent_key,
                )
            )
            await session.flush()
            session.add(
                AgentVersionModel(
                    id=version_id,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    version=spec.version,
                    instructions=spec.instructions,
                    memory_namespace=spec.memory_namespace,
                    configuration={},
                )
            )

        memory_store = PostgresMemoryStore(database.sessions)
        learning_store = PostgresLearningStore(database.sessions)
        reflection_model = TraceAwareReflectionProvider()
        pipeline = KnowledgeSedimentationPipeline(
            learning=LearningService(learning_store),
            learning_store=learning_store,
            extractor=ReflectionEngine(model=reflection_model),
            evaluator=EvidenceBasedCandidateEvaluator(),
            publisher=PostgresKnowledgeAssetPublisher(database.sessions),
        )
        model = ScriptedModelProvider(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id="validate-1",
                            name="validate_documents",
                            arguments={"document_count": 2},
                        ),
                    )
                ),
                ModelResponse(
                    content="Validate source documents before calculating the tax amount."
                ),
                ModelResponse(content="The source documents should be validated first."),
            ]
        )
        tools = ToolRegistry()
        tools.register(validation_tool())
        agent = Agent(
            spec=spec,
            runtime=AgentRuntime(
                model=model,
                tools=tools,
                memory=memory_store,
            ),
        )
        runs = PostgresRunPersistence(database.sessions)
        service = PersistentAgentService(
            runs=runs,
            sedimentation=pipeline,
        )
        context = RunContext(tenant_id=tenant_slug, user_id="integration-reviewer")

        first = await service.run(
            agent=agent,
            task="How should tax be calculated?",
            context=context,
            idempotency_key="tax-calculation-1",
        )
        assert first.result.status is RunStatus.SUCCEEDED
        assert first.sedimentation_error is None
        assert len(first.learning_candidates) == 1
        candidate = first.learning_candidates[0]
        assert candidate.status is CandidateStatus.AWAITING_APPROVAL
        assert candidate.proposed_change["reflection_engine"] == "full_trajectory_reflection"
        assert len(candidate.proposed_change["evidence_event_ids"]) == 1
        assert len(reflection_model.requests) == 1

        async with database.sessions() as session, session.begin():
            candidate_row = await session.get(LearningCandidateModel, candidate.id)
            assert candidate_row is not None
            assert candidate_row.fingerprint == candidate.fingerprint
            candidate_row.proposed_change = {
                **candidate_row.proposed_change,
                "fingerprint": "0" * 64,
            }
        independently_indexed = await learning_store.find_by_fingerprint(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            fingerprint=candidate.fingerprint,
        )
        assert independently_indexed is not None
        assert independently_indexed.id == candidate.id

        trace = await runs.load_trace(
            run_id=first.result.run_id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        assert [event.sequence for event in trace.events] == list(
            range(1, len(trace.events) + 1)
        )
        reflected_event_id = candidate.proposed_change["evidence_event_ids"][0]
        model_event = next(
            event for event in trace.events if str(event.id) == reflected_event_id
        )
        tool_event = next(event for event in trace.events if event.event_type == "tool.completed")
        assert model_event.payload["content"] == first.result.output
        assert tool_event.payload["output"] == {"validated": True}
        event_types = [event.event_type for event in trace.events]
        model_indices = [
            index for index, event_type in enumerate(event_types) if event_type == "model.responded"
        ]
        assert model_indices[0] < event_types.index("tool.completed") < model_indices[1]
        assert model_indices[1] < event_types.index("run.verified")
        with pytest.raises(KeyError, match="requested tenant and agent scope"):
            await runs.load_trace(
                run_id=first.result.run_id,
                tenant_id="another-tenant",
                agent_id=agent_key,
            )

        replayed = await service.run(
            agent=agent,
            task="How should tax be calculated?",
            context=context,
            idempotency_key="tax-calculation-1",
        )
        assert replayed.result.run_id == first.result.run_id
        assert replayed.result.output == first.result.output
        assert len(model.requests) == 2
        with pytest.raises(ValueError, match="different run request"):
            await service.run(
                agent=agent,
                task="A different task must not reuse the same key",
                context=context,
                idempotency_key="tax-calculation-1",
            )

        active, published_memory = await pipeline.approve_and_publish(
            candidate.id,
            decided_by="integration-reviewer",
            decision_note="Verified for the integration test",
        )
        assert active.status is CandidateStatus.ACTIVE

        second = await service.run(
            agent=agent,
            task="What is the source document validation order for tax?",
            context=context,
        )
        assert second.result.status is RunStatus.SUCCEEDED
        assert published_memory.content in model.requests[2].messages[0].content

        async with database.sessions() as session:
            stored_runs = tuple(
                (
                    await session.scalars(
                        select(RunModel)
                        .where(RunModel.tenant_id == tenant_id)
                        .order_by(RunModel.created_at)
                    )
                ).all()
            )
            recalled_events = tuple(
                (
                    await session.scalars(
                        select(RunEventModel).where(
                            RunEventModel.run_id == second.result.run_id,
                            RunEventModel.event_type == "memory.recalled",
                        )
                    )
                ).all()
            )
            evaluations = tuple(
                (
                    await session.scalars(
                        select(EvaluationModel).where(
                            EvaluationModel.candidate_id == candidate.id
                        )
                    )
                ).all()
            )
            approvals = tuple(
                (
                    await session.scalars(
                        select(ApprovalModel).where(ApprovalModel.candidate_id == candidate.id)
                    )
                ).all()
            )

        assert len(stored_runs) == 2
        assert all(row.status == RunStatus.SUCCEEDED.value for row in stored_runs)
        assert recalled_events[0].payload["memory_ids"] == [str(published_memory.id)]
        assert len(evaluations) == 1
        assert evaluations[0].passed is True
        assert len(approvals) == 1
        assert approvals[0].decided_by == "integration-reviewer"

        rolled_back = await pipeline.rollback(active.id)
        assert rolled_back.status is CandidateStatus.ROLLED_BACK
        assert await memory_store.search(
            MemoryQuery(
                tenant_id=tenant_slug,
                agent_id=agent_key,
                namespace=spec.memory_namespace,
                text="validate source documents tax",
            )
        ) == ()
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_candidate_merge_is_idempotent_and_publishes_source_deprecation() -> None:
    database = Database(Settings().database_url)
    tenant_id = uuid4()
    agent_id = uuid4()
    version_id = uuid4()
    tenant_slug = f"tenant-{tenant_id.hex[:12]}"
    agent_key = f"merge-agent-{agent_id.hex[:12]}"
    spec = AgentSpec(
        id=agent_key,
        name="Merge Agent",
        version="0.1.0",
        instructions="Manage compatible tax knowledge.",
        memory_namespace="tax-merge",
    )
    first_event_id = uuid4()
    second_event_id = uuid4()
    first_run_id = uuid4()
    second_run_id = uuid4()

    try:
        async with database.sessions() as session, session.begin():
            session.add(TenantModel(id=tenant_id, slug=tenant_slug, name="Merge Tenant"))
            await session.flush()
            session.add(
                AgentModel(
                    id=agent_id,
                    tenant_id=tenant_id,
                    agent_key=agent_key,
                    name=spec.name,
                    domain_id=agent_key,
                )
            )
            await session.flush()
            session.add(
                AgentVersionModel(
                    id=version_id,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    version=spec.version,
                    instructions=spec.instructions,
                    memory_namespace=spec.memory_namespace,
                    configuration={},
                )
            )
            await session.flush()
            session.add_all(
                [
                    RunModel(
                        id=first_run_id,
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        agent_version_id=version_id,
                        status=RunStatus.SUCCEEDED.value,
                        task="First merge source",
                        output="first",
                        metadata_json={},
                    ),
                    RunModel(
                        id=second_run_id,
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        agent_version_id=version_id,
                        status=RunStatus.SUCCEEDED.value,
                        task="Second merge source",
                        output="second",
                        metadata_json={},
                    ),
                ]
            )

        learning_store = PostgresLearningStore(database.sessions)
        fingerprint_probe = LearningCandidate(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            candidate_type=CandidateType.STRATEGY,
            risk=CandidateRisk.LOW,
            title="Concurrent fingerprint probe",
            fingerprint="f" * 64,
            proposed_change={"strategy": "probe"},
        )
        fingerprint_results = await asyncio.gather(
            learning_store.create_if_fingerprint_absent(fingerprint_probe),
            learning_store.create_if_fingerprint_absent(
                fingerprint_probe.model_copy(update={"id": uuid4()})
            ),
        )
        assert sorted(fingerprint_results) == [False, True]
        stored_probe = await learning_store.find_by_fingerprint(
            tenant_id=tenant_slug,
            agent_id=agent_key,
            domain_id=agent_key,
            fingerprint=fingerprint_probe.fingerprint,
        )
        assert stored_probe is not None

        pipeline = KnowledgeSedimentationPipeline(
            learning=LearningService(learning_store),
            learning_store=learning_store,
            extractor=QueueExtractor(
                ExtractedKnowledge(
                    title="Validate documents first",
                    content="Validate source documents before calculating tax.",
                    memory_type=MemoryType.PROCEDURAL,
                    evidence_event_ids=(first_event_id,),
                ),
                ExtractedKnowledge(
                    title="Always validate documents first",
                    content="Always validate source documents before calculating tax.",
                    memory_type=MemoryType.PROCEDURAL,
                    evidence_event_ids=(second_event_id,),
                ),
            ),
            evaluator=EvidenceBasedCandidateEvaluator(),
            publisher=PostgresKnowledgeAssetPublisher(database.sessions),
            conflict_detector=RuleBasedCandidateConflictDetector(),
        )
        context = RunContext(tenant_id=tenant_slug)
        first = (
            await pipeline.process_run(
                agent=spec,
                context=context,
                task="First merge source",
                result=RunResult(
                    run_id=first_run_id,
                    status=RunStatus.SUCCEEDED,
                    output="first",
                ),
            )
        )[0]
        active_first, first_memory = await pipeline.approve_and_publish(
            first.id,
            decided_by="integration-reviewer",
        )
        second = (
            await pipeline.process_run(
                agent=spec,
                context=context,
                task="Second merge source",
                result=RunResult(
                    run_id=second_run_id,
                    status=RunStatus.SUCCEEDED,
                    output="second",
                ),
            )
        )[0]

        merge_results = await asyncio.gather(
            pipeline.merge_candidates((active_first.id, second.id)),
            pipeline.merge_candidates((second.id, active_first.id)),
        )
        assert merge_results[0].id == merge_results[1].id
        merged = await learning_store.get(merge_results[0].id)
        assert merged.status is CandidateStatus.AWAITING_APPROVAL
        assert merged.evidence_run_ids == tuple(
            sorted((first_run_id, second_run_id), key=str)
        )
        assert merged.proposed_change["evidence_event_ids"] == sorted(
            (str(first_event_id), str(second_event_id))
        )

        active_merged, merged_memory = await pipeline.approve_and_publish(
            merged.id,
            decided_by="integration-reviewer",
            decision_note="Compatible PostgreSQL merge",
        )
        assert active_merged.status is CandidateStatus.ACTIVE

        async with database.sessions() as session:
            source_rows = tuple(
                (
                    await session.scalars(
                        select(LearningCandidateModel)
                        .where(LearningCandidateModel.id.in_((first.id, second.id)))
                        .order_by(LearningCandidateModel.id)
                    )
                ).all()
            )
            first_memory_row = await session.get(MemoryModel, first_memory.id)
            merged_memory_row = await session.get(MemoryModel, merged_memory.id)

        assert {row.status for row in source_rows} == {CandidateStatus.DEPRECATED.value}
        assert first_memory_row is not None
        assert first_memory_row.status == "superseded"
        assert merged_memory_row is not None
        assert merged_memory_row.status == "active"

        rolled_back = await pipeline.rollback(active_merged.id)
        assert rolled_back.status is CandidateStatus.ROLLED_BACK
        async with database.sessions() as session:
            restored_sources = tuple(
                (
                    await session.scalars(
                        select(LearningCandidateModel)
                        .where(LearningCandidateModel.id.in_((first.id, second.id)))
                        .order_by(LearningCandidateModel.id)
                    )
                ).all()
            )
            restored_first_memory = await session.get(MemoryModel, first_memory.id)
            rolled_back_memory = await session.get(MemoryModel, merged_memory.id)

        restored_statuses = {row.id: row.status for row in restored_sources}
        assert restored_statuses[first.id] == CandidateStatus.ACTIVE.value
        assert restored_statuses[second.id] == CandidateStatus.AWAITING_APPROVAL.value
        assert restored_first_memory is not None
        assert restored_first_memory.status == "active"
        assert rolled_back_memory is not None
        assert rolled_back_memory.status == "superseded"
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        await database.dispose()
