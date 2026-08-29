from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from public_agent.core.trace import RunTrace
from public_agent.core.types import AgentSpec, RunContext, RunResult
from public_agent.growth.models import LearningCandidate
from public_agent.growth.pipeline import KnowledgeSedimentationPipeline


class ReflectionJobState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class ReflectionWorkItem:
    job_id: UUID
    run_id: UUID
    lease_token: UUID
    attempts: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class ReflectionJobInput:
    agent: AgentSpec
    context: RunContext
    task: str
    result: RunResult
    trace: RunTrace


@dataclass(frozen=True, slots=True)
class ReflectionWorkerResult:
    job_id: UUID
    run_id: UUID
    state: ReflectionJobState
    candidate_ids: tuple[UUID, ...] = ()
    error_code: str | None = None


class ReflectionJobLeaseLostError(RuntimeError):
    """A stale worker attempted to mutate a job it no longer owns."""


class ReflectionJobStore(Protocol):
    async def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ReflectionWorkItem | None: ...

    async def load_input(self, work: ReflectionWorkItem) -> ReflectionJobInput: ...

    async def heartbeat(
        self,
        work: ReflectionWorkItem,
        *,
        lease_seconds: int,
    ) -> None: ...

    async def complete(
        self,
        work: ReflectionWorkItem,
        *,
        candidates: tuple[LearningCandidate, ...],
    ) -> None: ...

    async def fail(
        self,
        work: ReflectionWorkItem,
        *,
        error_code: str,
    ) -> ReflectionJobState: ...


class ReflectionWorker:
    def __init__(
        self,
        *,
        jobs: ReflectionJobStore,
        sedimentation: KnowledgeSedimentationPipeline,
        lease_seconds: int = 300,
        heartbeat_seconds: int = 60,
    ) -> None:
        if not 5 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        if not 1 <= heartbeat_seconds < lease_seconds:
            raise ValueError("heartbeat_seconds must be positive and less than the lease")
        self._jobs = jobs
        self._sedimentation = sedimentation
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds

    async def process_one(self, *, worker_id: str) -> ReflectionWorkerResult | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker or len(normalized_worker) > 200:
            raise ValueError("worker_id must contain 1 to 200 characters")
        work = await self._jobs.claim(
            worker_id=normalized_worker,
            lease_seconds=self._lease_seconds,
        )
        if work is None:
            return None
        try:
            job_input = await self._jobs.load_input(work)
            candidates = await self._process_with_heartbeat(work, job_input)
            await self._jobs.complete(work, candidates=candidates)
        except ReflectionJobLeaseLostError:
            return ReflectionWorkerResult(
                job_id=work.job_id,
                run_id=work.run_id,
                state=ReflectionJobState.LEASE_LOST,
                error_code="reflection_worker.lease_lost",
            )
        except Exception as exc:
            error_code = _safe_error_code(exc)
            try:
                state = await self._jobs.fail(work, error_code=error_code)
            except ReflectionJobLeaseLostError:
                state = ReflectionJobState.LEASE_LOST
                error_code = "reflection_worker.lease_lost"
            return ReflectionWorkerResult(
                job_id=work.job_id,
                run_id=work.run_id,
                state=state,
                error_code=error_code,
            )
        return ReflectionWorkerResult(
            job_id=work.job_id,
            run_id=work.run_id,
            state=ReflectionJobState.SUCCEEDED,
            candidate_ids=tuple(candidate.id for candidate in candidates),
        )

    async def process_step(
        self,
        *,
        worker_id: str,
        max_jobs: int = 10,
    ) -> tuple[ReflectionWorkerResult, ...]:
        if not 1 <= max_jobs <= 100:
            raise ValueError("max_jobs must be between 1 and 100")
        results: list[ReflectionWorkerResult] = []
        for _ in range(max_jobs):
            result = await self.process_one(worker_id=worker_id)
            if result is None:
                break
            results.append(result)
        return tuple(results)

    async def _process_with_heartbeat(
        self,
        work: ReflectionWorkItem,
        job_input: ReflectionJobInput,
    ) -> tuple[LearningCandidate, ...]:
        processing = asyncio.create_task(
            self._sedimentation.process_run(
                agent=job_input.agent,
                context=job_input.context,
                task=job_input.task,
                result=job_input.result,
                trace=job_input.trace,
            )
        )
        try:
            while True:
                done, _ = await asyncio.wait(
                    {processing},
                    timeout=self._heartbeat_seconds,
                )
                if processing in done:
                    return await processing
                await self._jobs.heartbeat(work, lease_seconds=self._lease_seconds)
        except BaseException:
            processing.cancel()
            with suppress(asyncio.CancelledError):
                await processing
            raise


def _safe_error_code(exc: Exception) -> str:
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()
    normalized = re.sub(r"[^a-z0-9_.-]", "_", name)[:70]
    return f"reflection_worker.{normalized or 'error'}"
