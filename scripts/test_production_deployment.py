from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

EXPECTED_CASES = {
    "capacity-audit-sla-drill",
    "capacity-governance-incident-loop",
    "capacity-governance-knowledge-feedback-isolation-loop",
    "capacity-governance-knowledge-quality-recovery-loop",
    "capacity-governance-knowledge-quality-trend-risk-loop",
    "capacity-governance-knowledge-recertification-loop",
    "capacity-governance-retired-rag-exclusion",
    "capacity-governance-wave4-migration-roundtrip",
    "capacity-governance-postmortem-knowledge-loop",
    "capacity-governance-remediation-loop",
    "capacity-approval-console-security",
    "capacity-cli-fail-closed",
    "capacity-cli-provider-independent",
    "capacity-rbac-control-plane",
    "capacity-recommendation-bounds",
    "capacity-policy-approval-state-machine",
    "capacity-policy-cooldown-review-rollback",
    "capacity-policy-runtime-resolution",
    "capacity-three-level-status",
    "guarded-outbox-prune",
    "compose-capacity-guardrails",
    "compose-scalable-worker",
    "compose-secret-boundary",
    "non-root-production-image",
    "postgres-handler-isolation",
    "partitioned-outbox-archive",
    "policy-drift-alert-loop",
    "persistent-capacity-trends",
    "real-history-capacity-calibration",
    "reversible-capacity-governance-schema",
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    _validate_ground_truth(root)
    _validate_static_deployment_contract(root)

    environment = dict(os.environ)
    environment["PUBLIC_AGENT_RUN_DB_TESTS"] = "1"
    python_image = environment.get(
        "PUBLIC_AGENT_PYTHON_IMAGE",
        "python:3.12-slim-bookworm",
    )
    image = environment.get(
        "PUBLIC_AGENT_PRODUCTION_GATE_IMAGE",
        "public-agent:production-gate",
    )
    commands = [
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_capacity.py",
            "tests/test_capacity_cli.py",
            "tests/test_capacity_governance.py",
            "tests/test_capacity_control.py",
            "tests/test_capacity_governance_api.py",
            "tests/test_capacity_governance_cli.py",
            "tests/test_capacity_policy_governance.py",
            "tests/test_config.py",
            "tests/test_storage_models.py",
            "tests/test_postgres_capacity_governance.py",
            "tests/test_postgres_capacity_control.py",
            "tests/test_postgres_governance_postmortems.py",
            "tests/test_postgres_outbox_worker.py",
        ],
        ["docker", "compose", "-f", "docker-compose.production.yml", "config", "--quiet"],
        [
            "docker",
            "build",
            "--build-arg",
            f"PYTHON_IMAGE={python_image}",
            "--tag",
            image,
            ".",
        ],
        ["docker", "run", "--rm", "--entrypoint", "id", image],
        ["docker", "run", "--rm", image, "python", "-m", "pip", "check"],
        ["docker", "run", "--rm", image, "public-agent", "--help"],
        ["docker", "run", "--rm", image, "alembic", "heads"],
    ]
    output: list[str] = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output.append(f"$ {' '.join(command)}\n")
        output.append(completed.stdout)
        output.append(completed.stderr)
        if completed.returncode != 0:
            _write_log(root, output)
            print("".join(output), end="")
            return completed.returncode

    combined = "".join(output)
    for expected in (
        "uid=10001(public_agent) gid=10001(public_agent)",
        "No broken requirements found.",
        "capacity-check",
        "capacity-monitor",
        "capacity-calibrate",
        "capacity-policy",
        "capacity-trend",
        "outbox-maintain",
        "f1b3c7d9e2a4 (head)",
    ):
        if expected not in combined:
            raise RuntimeError(f"production gate output is missing: {expected}")
    _write_log(root, output)
    print(combined, end="")
    return 0


def _validate_ground_truth(root: Path) -> None:
    cases = json.loads(
        (root / "references" / "deployment_capacity_cases.json").read_text(
            encoding="utf-8"
        )
    )
    case_ids = {case["id"] for case in cases["ground_truth"]}
    if case_ids != EXPECTED_CASES:
        raise RuntimeError("deployment capacity ground-truth inventory is incomplete")


def _validate_static_deployment_contract(root: Path) -> None:
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "docker-compose.production.yml").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    lock_lines = [
        line.strip()
        for line in (root / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    required_dockerfile = (
        "FROM ${PYTHON_IMAGE} AS builder",
        "--constraint requirements.lock",
        "USER 10001:10001",
        "COPY --chown=public_agent:public_agent migrations ./migrations",
    )
    required_compose = (
        "PUBLIC_AGENT_SECRETS_DIR: /run/secrets",
        "PUBLIC_AGENT_API_TOKEN_PEPPER",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_GOVERNANCE_TENANT_ID",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_ALERT_RESPONSE_WARNING_SECONDS",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_ALERT_RESPONSE_CRITICAL_SECONDS",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_INCIDENT_AUDIT_WINDOW_SECONDS",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_INCIDENT_REOPEN_CRITICAL_COUNT",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_RISK_WINDOW_SECONDS",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_UNSAFE_CRITICAL_COUNT",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_DEGRADED_CRITICAL_COUNT",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_MAXIMUM_SNAPSHOTS",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_MAXIMUM_TREND_BUCKETS",
        "read_only: true",
        "cap_drop:",
        "pids_limit:",
        "mem_limit:",
        "cpus:",
        "max-size:",
        "condition: service_completed_successfully",
        "stop_grace_period: 45s",
        "capacity-monitor:",
        "capacity-trend:",
        "capacity-calibrate:",
        "capacity-policy:",
        "outbox-maintain:",
    )
    if any(value not in dockerfile for value in required_dockerfile):
        raise RuntimeError("Dockerfile production contract is incomplete")
    if any(value not in compose for value in required_compose):
        raise RuntimeError("Compose production contract is incomplete")
    required_env_example = (
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_RISK_WINDOW_SECONDS=604800",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_UNSAFE_WARNING_COUNT=2",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_UNSAFE_CRITICAL_COUNT=3",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_DEGRADED_WARNING_COUNT=2",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_DEGRADED_CRITICAL_COUNT=4",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_MAXIMUM_SNAPSHOTS=1000",
        "PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_MAXIMUM_TREND_BUCKETS=366",
    )
    if any(value not in env_example for value in required_env_example):
        raise RuntimeError(".env.example knowledge quality contract is incomplete")
    if "PUBLIC_AGENT_REFLECTION_WORKER_ID:" in compose:
        raise RuntimeError("Compose must not fix a shared reflection worker id")
    if not lock_lines or any("==" not in line for line in lock_lines):
        raise RuntimeError("requirements.lock must contain exact dependency versions")


def _write_log(root: Path, output: list[str]) -> None:
    log_path = root / "scripts" / "log" / "production_deployment.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("".join(output), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
