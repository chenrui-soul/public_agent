from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from public_agent.workers import (
    ReflectionJobState,
    ReflectionWorkerLifecycleState,
    ReflectionWorkerRegistration,
    ReflectionWorkerResult,
    ReflectionWorkerRunner,
)


class ScriptedWorker:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.calls = 0

    async def process_one(self, *, worker_id: str) -> ReflectionWorkerResult | None:
        assert worker_id == "runner-test"
        self.calls += 1
        self.stop_event.set()
        return ReflectionWorkerResult(
            job_id=uuid4(),
            run_id=uuid4(),
            state=ReflectionJobState.SUCCEEDED,
        )


class BlockingWorker:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def process_one(self, *, worker_id: str) -> ReflectionWorkerResult | None:
        assert worker_id == "runner-test"
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return None


class LifecycleRecorder:
    def __init__(self) -> None:
        self.states: list[ReflectionWorkerLifecycleState] = []
        self.stopped = False
        self.processed_jobs = -1
        self.stop_error_code: str | None = None
        self.stop_last_result: ReflectionWorkerResult | None = None

    async def register_worker(self, *, worker_id: str) -> ReflectionWorkerRegistration:
        return ReflectionWorkerRegistration(worker_id=worker_id, instance_token=uuid4())

    async def heartbeat_worker(
        self,
        registration: ReflectionWorkerRegistration,
        *,
        state: ReflectionWorkerLifecycleState,
        processed_jobs: int,
        last_result: ReflectionWorkerResult | None,
    ) -> None:
        assert registration.worker_id == "runner-test"
        self.states.append(state)
        self.processed_jobs = processed_jobs
        if last_result is not None:
            assert last_result.state is ReflectionJobState.SUCCEEDED

    async def stop_worker(
        self,
        registration: ReflectionWorkerRegistration,
        *,
        processed_jobs: int,
        last_result: ReflectionWorkerResult | None,
        error_code: str | None,
    ) -> None:
        assert registration.worker_id == "runner-test"
        self.stopped = True
        self.processed_jobs = processed_jobs
        self.stop_error_code = error_code
        self.stop_last_result = last_result


@pytest.mark.asyncio
async def test_runner_stops_before_claiming_another_job_after_signal() -> None:
    stop_event = asyncio.Event()
    worker = ScriptedWorker(stop_event)
    lifecycle = LifecycleRecorder()
    runner = ReflectionWorkerRunner(
        worker=worker,
        lifecycle=lifecycle,
        worker_id="runner-test",
        poll_interval_seconds=0.05,
        poll_jitter_seconds=0,
        drain_timeout_seconds=1,
    )

    summary = await runner.run(stop_event=stop_event)

    assert worker.calls == 1
    assert summary.processed_jobs == 1
    assert summary.last_job_id is not None
    assert lifecycle.states == [
        ReflectionWorkerLifecycleState.IDLE,
        ReflectionWorkerLifecycleState.RUNNING,
    ]
    assert lifecycle.stopped is True
    assert lifecycle.processed_jobs == 1
    assert lifecycle.stop_error_code is None
    assert lifecycle.stop_last_result is not None


@pytest.mark.asyncio
async def test_runner_cancels_local_processing_after_drain_timeout() -> None:
    stop_event = asyncio.Event()
    worker = BlockingWorker()
    lifecycle = LifecycleRecorder()
    runner = ReflectionWorkerRunner(
        worker=worker,
        lifecycle=lifecycle,
        worker_id="runner-test",
        poll_interval_seconds=0.05,
        poll_jitter_seconds=0,
        drain_timeout_seconds=1,
    )

    running = asyncio.create_task(runner.run(stop_event=stop_event))
    await worker.started.wait()
    stop_event.set()
    summary = await running

    assert worker.cancelled is True
    assert summary.processed_jobs == 0
    assert summary.last_error_code == "reflection_worker.drain_timeout"
    assert ReflectionWorkerLifecycleState.STOPPING in lifecycle.states
    assert lifecycle.stop_error_code == "reflection_worker.drain_timeout"
    assert lifecycle.stop_last_result is None
