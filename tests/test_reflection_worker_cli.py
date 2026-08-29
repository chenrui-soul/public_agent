from __future__ import annotations

import argparse
import asyncio
import json
import signal
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from public_agent.cli import (
    build_parser,
    install_shutdown_signal_handlers,
    main,
    run_reflection_worker_command,
)
from public_agent.config import Settings
from public_agent.workers import ReflectionWorkerRunner, ReflectionWorkerRunSummary
from public_agent.workers.application import (
    ReflectionWorkerApplication,
    ReflectionWorkerOptions,
    build_reflection_worker_application,
    default_reflection_worker_id,
)


class LifecycleRecorder:
    def __init__(self, events: list[str], *, fail_dispose: bool = False) -> None:
        self.events = events
        self.fail_dispose = fail_dispose
        self.sessions = object()

    async def ping(self) -> None:
        self.events.append("database.ping")

    async def dispose(self) -> None:
        self.events.append("database.dispose")
        if self.fail_dispose:
            raise RuntimeError("database-secret-must-not-leak")


class RunnerRecorder:
    def __init__(self, events: list[str], summary: ReflectionWorkerRunSummary) -> None:
        self.events = events
        self.summary = summary
        self.stop_event: asyncio.Event | None = None

    async def run(self, *, stop_event: asyncio.Event) -> ReflectionWorkerRunSummary:
        self.events.append("runner.run")
        self.stop_event = stop_event
        return self.summary


class CloserRecorder:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    async def aclose(self) -> None:
        self.events.append("provider.aclose")
        if self.fail:
            raise RuntimeError("provider-secret-must-not-leak")


class CommandApplication:
    def __init__(
        self,
        summary: ReflectionWorkerRunSummary | None = None,
        *,
        run_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.summary = summary or _summary()
        self.run_error = run_error
        self.close_error = close_error
        self.stop_event: asyncio.Event | None = None
        self.closed = False

    async def run(self, *, stop_event: asyncio.Event) -> ReflectionWorkerRunSummary:
        self.stop_event = stop_event
        if self.run_error is not None:
            raise self.run_error
        return self.summary

    async def aclose(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class BlockingCommandApplication:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def run(self, *, stop_event: asyncio.Event) -> ReflectionWorkerRunSummary:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError(stop_event)

    async def aclose(self) -> None:
        self.closed = True


class ModelStub:
    async def complete(self, request: object) -> object:
        raise AssertionError(f"model must not be called during assembly: {request!r}")


def _summary(
    *,
    worker_id: str = "worker-test",
    error_code: str | None = None,
) -> ReflectionWorkerRunSummary:
    return ReflectionWorkerRunSummary(
        worker_id=worker_id,
        processed_jobs=0,
        last_job_id=None,
        last_error_code=error_code,
    )


def _worker_args(*extra: str) -> argparse.Namespace:
    return build_parser().parse_args(["reflection-worker", *extra])


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "openai_api_key": SecretStr("test-key"),
        "reflection_worker_id": "worker-test",
    }
    values.update(overrides)
    return Settings(**values)


def test_worker_options_load_environment_and_accept_cli_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_AGENT_REFLECTION_WORKER_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("PUBLIC_AGENT_REFLECTION_WORKER_POLL_INTERVAL_SECONDS", "2.5")
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr("test-key"),
        reflection_worker_id="environment-worker",
    )

    options = ReflectionWorkerOptions.from_settings(
        settings,
        worker_id=" cli-worker ",
        poll_jitter_seconds=0.5,
    )

    assert options.worker_id == "cli-worker"
    assert options.max_attempts == 7
    assert options.poll_interval_seconds == 2.5
    assert options.poll_jitter_seconds == 0.5


def test_worker_options_reject_cross_field_boundaries() -> None:
    with pytest.raises(ValidationError, match="heartbeat_seconds"):
        ReflectionWorkerOptions.from_settings(
            _settings(),
            lease_seconds=10,
            heartbeat_seconds=10,
        )


def test_default_worker_id_is_bounded_and_process_specific() -> None:
    worker_id = default_reflection_worker_id()

    assert worker_id.startswith("reflection-")
    assert 1 <= len(worker_id) <= 200


@pytest.mark.asyncio
async def test_application_pings_runs_and_closes_owned_resources() -> None:
    events: list[str] = []
    summary = _summary()
    database = LifecycleRecorder(events)
    runner = RunnerRecorder(events, summary)
    provider = CloserRecorder(events)
    application = ReflectionWorkerApplication(
        database=database,
        runner=runner,
        owned_provider=provider,
    )
    stop_event = asyncio.Event()

    result = await application.run(stop_event=stop_event)
    await application.aclose()
    await application.aclose()

    assert result == summary
    assert runner.stop_event is stop_event
    assert events == [
        "database.ping",
        "runner.run",
        "provider.aclose",
        "database.dispose",
    ]


@pytest.mark.asyncio
async def test_application_disposes_database_when_provider_close_fails() -> None:
    events: list[str] = []
    application = ReflectionWorkerApplication(
        database=LifecycleRecorder(events),
        runner=RunnerRecorder(events, _summary()),
        owned_provider=CloserRecorder(events, fail=True),
    )

    with pytest.raises(RuntimeError, match="provider-secret"):
        await application.aclose()

    assert events == ["provider.aclose", "database.dispose"]


def test_production_factory_builds_runner_without_calling_paid_provider() -> None:
    database = LifecycleRecorder([])

    application = build_reflection_worker_application(
        _settings(),
        ReflectionWorkerOptions.from_settings(_settings()),
        model=ModelStub(),  # type: ignore[arg-type]
        database=database,  # type: ignore[arg-type]
    )

    assert isinstance(application.runner, ReflectionWorkerRunner)
    assert application.database is database
    assert application.owned_provider is None
    assert application.owns_database is False


@pytest.mark.asyncio
async def test_missing_openai_key_fails_closed_without_database_or_network(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await run_reflection_worker_command(
        _worker_args(),
        settings=_settings(openai_api_key=None),
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 2
    assert captured.out == ""
    assert payload == {
        "error_code": "reflection_worker.openai_api_key_missing",
        "event": "reflection_worker.configuration_failed",
    }


@pytest.mark.asyncio
async def test_invalid_cli_configuration_returns_exit_code_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory_called = False

    def factory(
        settings: Settings,
        options: ReflectionWorkerOptions,
    ) -> CommandApplication:
        nonlocal factory_called
        factory_called = True
        raise AssertionError((settings, options))

    code = await run_reflection_worker_command(
        _worker_args("--lease-seconds", "10", "--heartbeat-seconds", "10"),
        settings=_settings(),
        application_factory=factory,
    )

    payload = json.loads(capsys.readouterr().err)
    assert code == 2
    assert factory_called is False
    assert payload["error_code"] == "reflection_worker.configuration_invalid"


@pytest.mark.asyncio
async def test_assembly_failure_returns_two_without_leaking_exception_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "postgresql://admin:assembly-secret@database/agent"

    def factory(
        settings: Settings,
        options: ReflectionWorkerOptions,
    ) -> CommandApplication:
        raise RuntimeError(f"{secret}:{settings.environment}:{options.worker_id}")

    code = await run_reflection_worker_command(
        _worker_args(),
        settings=_settings(),
        application_factory=factory,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 2
    assert payload["event"] == "reflection_worker.assembly_failed"
    assert payload["error_code"] == "reflection_worker.assembly_runtime_error"
    assert secret not in captured.err


@pytest.mark.asyncio
async def test_successful_worker_stop_returns_zero_and_closes_application(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = CommandApplication(_summary(worker_id="configured-worker"))
    captured_options: ReflectionWorkerOptions | None = None

    def factory(
        settings: Settings,
        options: ReflectionWorkerOptions,
    ) -> CommandApplication:
        nonlocal captured_options
        assert settings.openai_api_key is not None
        captured_options = options
        return application

    code = await run_reflection_worker_command(
        _worker_args("--worker-id", "configured-worker", "--max-attempts", "9"),
        settings=_settings(),
        application_factory=factory,
    )

    stdout = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == 0
    assert application.closed is True
    assert application.stop_event is not None
    assert captured_options is not None
    assert captured_options.worker_id == "configured-worker"
    assert captured_options.max_attempts == 9
    assert [event["event"] for event in stdout] == [
        "reflection_worker.starting",
        "reflection_worker.stopped",
    ]


@pytest.mark.asyncio
async def test_drain_timeout_returns_exit_code_three(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = CommandApplication(
        _summary(error_code="reflection_worker.drain_timeout")
    )

    code = await run_reflection_worker_command(
        _worker_args(),
        settings=_settings(),
        application_factory=lambda _settings, _options: application,
    )

    stopped = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == 3
    assert application.closed is True
    assert stopped["last_error_code"] == "reflection_worker.drain_timeout"


@pytest.mark.asyncio
async def test_runtime_failure_returns_one_without_leaking_exception_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "postgresql://admin:super-secret@database/agent"
    application = CommandApplication(run_error=RuntimeError(secret))

    code = await run_reflection_worker_command(
        _worker_args(),
        settings=_settings(),
        application_factory=lambda _settings, _options: application,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 1
    assert application.closed is True
    assert payload["error_code"] == "reflection_worker.runtime_runtime_error"
    assert secret not in captured.out
    assert secret not in captured.err


@pytest.mark.asyncio
async def test_cleanup_failure_returns_one_and_still_hides_exception_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = CommandApplication(
        close_error=RuntimeError("cleanup-token-must-not-leak")
    )

    code = await run_reflection_worker_command(
        _worker_args(),
        settings=_settings(),
        application_factory=lambda _settings, _options: application,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 1
    assert payload["error_code"] == "reflection_worker.cleanup_runtime_error"
    assert "cleanup-token-must-not-leak" not in captured.err


@pytest.mark.asyncio
async def test_external_cancellation_still_closes_application() -> None:
    application = BlockingCommandApplication()
    running = asyncio.create_task(
        run_reflection_worker_command(
            _worker_args(),
            settings=_settings(),
            application_factory=lambda _settings, _options: application,
        )
    )
    await application.started.wait()

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert application.closed is True


@pytest.mark.asyncio
async def test_signal_handlers_set_stop_event_and_restore_previous_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: dict[signal.Signals, Callable[[int, Any], None]] = {}
    restored: dict[signal.Signals, object] = {}
    previous = object()

    def fake_getsignal(signum: signal.Signals) -> object:
        assert isinstance(signum, signal.Signals)
        return previous

    def fake_signal(signum: signal.Signals, handler: object) -> object:
        if callable(handler):
            installed[signum] = handler
        else:
            restored[signum] = handler
        return previous

    monkeypatch.setattr(signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(signal, "signal", fake_signal)
    stop_event = asyncio.Event()

    with install_shutdown_signal_handlers(stop_event):
        installed[signal.SIGINT](int(signal.SIGINT), None)
        await asyncio.sleep(0)
        assert stop_event.is_set()
        expected = {signal.SIGINT, signal.SIGTERM}
        if hasattr(signal, "SIGBREAK"):
            expected.add(signal.SIGBREAK)
        assert set(installed) == expected

    assert restored == {signum: previous for signum in installed}


def test_reflection_worker_help_is_available_without_api_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["reflection-worker", "--help"])

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "--worker-id" in output
    assert "--drain-timeout-seconds" in output
    assert "api-key" not in output.lower()
