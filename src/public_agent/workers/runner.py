from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from public_agent.workers.reflection import ReflectionWorkerResult


class ReflectionWorkerLifecycleState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ReflectionWorkerRegistration:
    worker_id: str
    instance_token: UUID


class ReflectionWorkerRegistrationLostError(RuntimeError):
    """A stale worker process attempted to update a replaced registration."""


@dataclass(frozen=True, slots=True)
class ReflectionBacklogSnapshot:
    pending: int
    processing: int
    retry_wait: int
    succeeded: int
    dead_letter: int
    oldest_available_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReflectionWorkerFleetSnapshot:
    registered: int
    active: int
    stale: int
    stopped: int
    errored: int
    processed_jobs: int
    oldest_last_seen_at: datetime | None
    newest_last_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReflectionCapacitySnapshot:
    observed_at: datetime
    backlog: ReflectionBacklogSnapshot
    workers: ReflectionWorkerFleetSnapshot


@dataclass(frozen=True, slots=True)
class ReflectionWorkerRunSummary:
    worker_id: str
    processed_jobs: int
    last_job_id: UUID | None
    last_error_code: str | None


class ReflectionWorkerLifecycleStore(Protocol):
    async def register_worker(self, *, worker_id: str) -> ReflectionWorkerRegistration: ...

    async def heartbeat_worker(
        self,
        registration: ReflectionWorkerRegistration,
        *,
        state: ReflectionWorkerLifecycleState,
        processed_jobs: int,
        last_result: ReflectionWorkerResult | None,
    ) -> None: ...

    async def stop_worker(
        self,
        registration: ReflectionWorkerRegistration,
        *,
        processed_jobs: int,
        last_result: ReflectionWorkerResult | None,
        error_code: str | None,
    ) -> None: ...


class ReflectionWorkerProcessor(Protocol):
    async def process_one(self, *, worker_id: str) -> ReflectionWorkerResult | None: ...


class ReflectionWorkerRunner:
    def __init__(
        self,
        *,
        worker: ReflectionWorkerProcessor,
        lifecycle: ReflectionWorkerLifecycleStore,
        worker_id: str,
        poll_interval_seconds: float = 1.0,
        poll_jitter_seconds: float = 0.25,
        drain_timeout_seconds: float = 30.0,
    ) -> None:
        normalized_worker = worker_id.strip()
        if not normalized_worker or len(normalized_worker) > 200:
            raise ValueError("worker_id must contain 1 to 200 characters")
        if not 0.05 <= poll_interval_seconds <= 60:
            raise ValueError("poll_interval_seconds must be between 0.05 and 60")
        if not 0 <= poll_jitter_seconds <= poll_interval_seconds:
            raise ValueError("poll_jitter_seconds must be between 0 and poll interval")
        if not 1 <= drain_timeout_seconds <= 3_600:
            raise ValueError("drain_timeout_seconds must be between 1 and 3600")
        self._worker = worker
        self._lifecycle = lifecycle
        self._worker_id = normalized_worker
        self._poll_interval_seconds = poll_interval_seconds
        self._poll_jitter_seconds = poll_jitter_seconds
        self._drain_timeout_seconds = drain_timeout_seconds

    async def run(self, *, stop_event: asyncio.Event) -> ReflectionWorkerRunSummary:
        registration = await self._lifecycle.register_worker(worker_id=self._worker_id)
        processed_jobs = 0
        last_result: ReflectionWorkerResult | None = None
        terminal_error: str | None = None
        try:
            await self._lifecycle.heartbeat_worker(
                registration,
                state=ReflectionWorkerLifecycleState.IDLE,
                processed_jobs=processed_jobs,
                last_result=last_result,
            )
            while not stop_event.is_set():
                await self._lifecycle.heartbeat_worker(
                    registration,
                    state=ReflectionWorkerLifecycleState.RUNNING,
                    processed_jobs=processed_jobs,
                    last_result=last_result,
                )
                result, stopping, drain_timed_out = await self._process_or_stop(
                    stop_event,
                    registration=registration,
                    processed_jobs=processed_jobs,
                    last_result=last_result,
                )
                if result is not None:
                    processed_jobs += 1
                    last_result = result
                if drain_timed_out:
                    terminal_error = "reflection_worker.drain_timeout"
                if stopping:
                    break
                if result is None:
                    await self._lifecycle.heartbeat_worker(
                        registration,
                        state=ReflectionWorkerLifecycleState.IDLE,
                        processed_jobs=processed_jobs,
                        last_result=last_result,
                    )
                    await self._wait_for_poll(stop_event)
        except asyncio.CancelledError:
            terminal_error = "reflection_worker.runner_cancelled"
            raise
        except Exception as exc:
            terminal_error = f"reflection_worker.runner_{type(exc).__name__.lower()}"
            raise
        finally:
            with suppress(Exception):
                await self._lifecycle.stop_worker(
                    registration,
                    processed_jobs=processed_jobs,
                    last_result=last_result,
                    error_code=terminal_error,
                )
        return ReflectionWorkerRunSummary(
            worker_id=self._worker_id,
            processed_jobs=processed_jobs,
            last_job_id=last_result.job_id if last_result is not None else None,
            last_error_code=(
                terminal_error
                or (last_result.error_code if last_result is not None else None)
            ),
        )

    async def _process_or_stop(
        self,
        stop_event: asyncio.Event,
        *,
        registration: ReflectionWorkerRegistration,
        processed_jobs: int,
        last_result: ReflectionWorkerResult | None,
    ) -> tuple[ReflectionWorkerResult | None, bool, bool]:
        processing = asyncio.create_task(self._worker.process_one(worker_id=self._worker_id))
        stopping = asyncio.create_task(stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {processing, stopping},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if processing in done:
                return await processing, stop_event.is_set(), False
            await self._lifecycle.heartbeat_worker(
                registration,
                state=ReflectionWorkerLifecycleState.STOPPING,
                processed_jobs=processed_jobs,
                last_result=last_result,
            )
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(processing),
                    timeout=self._drain_timeout_seconds,
                )
                return result, True, False
            except TimeoutError:
                processing.cancel()
                with suppress(asyncio.CancelledError):
                    await processing
                return None, True, True
        finally:
            stopping.cancel()
            with suppress(asyncio.CancelledError):
                await stopping

    async def _wait_for_poll(self, stop_event: asyncio.Event) -> None:
        delay = self._poll_interval_seconds + random.uniform(
            0,
            self._poll_jitter_seconds,
        )
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
