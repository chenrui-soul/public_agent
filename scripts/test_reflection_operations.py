from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    cases_path = root / "references" / "reflection_operations_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    case_ids = {case["id"] for case in cases["ground_truth"]}
    expected = {
        "append-only-safe-operations-audit",
        "concurrent-idempotent-retry",
        "current-actor-fact-revalidation",
        "dead-letter-versioned-retry",
        "safe-job-projection",
        "strict-filter-bound-keyset",
        "tenant-agent-handler-isolation",
    }
    if case_ids != expected:
        raise RuntimeError("reflection operations ground-truth inventory is incomplete")

    environment = dict(os.environ)
    environment["PUBLIC_AGENT_RUN_DB_TESTS"] = "1"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_postgres_operations.py",
        "tests/test_postgres_outbox_worker.py",
        "tests/test_worker_runner.py",
        "tests/test_storage_models.py",
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    log_path = root / "scripts" / "log" / "reflection_operations.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
