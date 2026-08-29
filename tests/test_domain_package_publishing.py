from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from public_agent.config import Settings
from public_agent.domains.loader import DomainPackageLoader
from public_agent.domains.models import (
    DomainPackageEvaluationResult,
    DomainPackageStatus,
    PreparedDomainPackage,
)
from public_agent.storage.database import Database
from public_agent.storage.domain_packages import PostgresDomainPackagePublisher
from public_agent.storage.models import (
    AgentModel,
    AgentVersionModel,
    DomainPackageApprovalModel,
    DomainPackageAssetModel,
    DomainPackageEvaluationModel,
    DomainPackageReleaseModel,
    DomainPackageVersionModel,
    TenantModel,
)

pytestmark = pytest.mark.skipif(
    os.getenv("PUBLIC_AGENT_RUN_DB_TESTS") != "1",
    reason="set PUBLIC_AGENT_RUN_DB_TESTS=1 to run PostgreSQL integration tests",
)


async def _create_scope(
    database: Database,
    *,
    prefix: str,
) -> tuple[UUID, str, str, UUID]:
    tenant_uuid = uuid4()
    agent_uuid = uuid4()
    tenant_slug = f"{prefix}-tenant-{tenant_uuid.hex[:10]}"
    agent_key = f"{prefix}-agent-{agent_uuid.hex[:10]}"
    async with database.sessions() as session, session.begin():
        tenant = TenantModel(id=tenant_uuid, slug=tenant_slug, name=f"{prefix} Tenant")
        agent = AgentModel(
            id=agent_uuid,
            tenant_id=tenant_uuid,
            agent_key=agent_key,
            name=f"{prefix} Agent",
            domain_id="review_domain",
        )
        session.add(tenant)
        await session.flush()
        session.add(agent)
        await session.flush()
        baseline = AgentVersionModel(
            tenant_id=tenant_uuid,
            agent_id=agent_uuid,
            version="0.0.1",
            instructions="Baseline instructions.",
            memory_namespace="baseline",
            configuration={"baseline": True},
        )
        session.add(baseline)
        await session.flush()
        agent.active_version_id = baseline.id
    return tenant_uuid, tenant_slug, agent_key, baseline.id


def _build_package(root: Path, *, version: str, extra_step: str = "") -> PreparedDomainPackage:
    root.mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "workflows").mkdir()
    (root / "instructions.md").write_text(
        f"Review evidence before answering. {extra_step}".strip() + "\n",
        encoding="utf-8",
    )
    (root / "skills" / "review.yaml").write_text(
        f"steps:\n  - validate\n  - {extra_step or 'answer'}\n",
        encoding="utf-8",
    )
    (root / "workflows" / "review.yaml").write_text(
        "start: validate\nfinish: answer\n",
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        "\n".join(
            [
                "id: review_domain",
                "name: Review Domain Agent",
                f"version: {version}",
                "description: Production review package.",
                "instructions_file: instructions.md",
                "memory_namespace: review-memory",
                "knowledge_namespace: review-knowledge",
                "knowledge_top_k: 8",
                "allowed_tools:",
                "  - validate_review",
                "max_steps: 10",
                "evaluation_suite: review_v1",
                "policies:",
                "  require_citations: true",
                "  high_risk_requires_human_review: true",
                "assets:",
                "  - asset_type: skill",
                "    key: review",
                "    path: skills/review.yaml",
                "    media_type: application/yaml",
                "  - asset_type: workflow",
                "    key: review_flow",
                "    path: workflows/review.yaml",
                "    media_type: application/yaml",
            ]
        ),
        encoding="utf-8",
    )
    return DomainPackageLoader().build(root)


def _evaluation(version: str, *, passed: bool = True) -> DomainPackageEvaluationResult:
    return DomainPackageEvaluationResult(
        suite="review_v1",
        dataset_version="2026.08",
        passed=passed,
        score=0.96 if passed else 0.42,
        summary="Regression gates passed" if passed else "Regression gate failed",
        metrics={
            "pass_rate": 1.0 if passed else 0.5,
            "case_count": 12,
            "package_version": version,
        },
    )


async def _approve_version(
    publisher: PostgresDomainPackagePublisher,
    package_version_id: UUID,
    *,
    tenant_slug: str,
    agent_key: str,
    version: str,
) -> None:
    await publisher.begin_evaluation(
        package_version_id,
        tenant_id=tenant_slug,
        agent_id=agent_key,
    )
    awaiting = await publisher.record_evaluation(
        package_version_id,
        _evaluation(version),
        tenant_id=tenant_slug,
        agent_id=agent_key,
    )
    assert awaiting.status is DomainPackageStatus.AWAITING_APPROVAL
    approved = await publisher.approve(
        package_version_id,
        tenant_id=tenant_slug,
        agent_id=agent_key,
        decided_by="domain-reviewer",
        decision_note="All release gates passed.",
    )
    assert approved.status is DomainPackageStatus.APPROVED


@pytest.mark.asyncio
async def test_domain_package_release_is_idempotent_atomic_and_rollbackable(
    tmp_path: Path,
) -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key, baseline_version_id = await _create_scope(
        database,
        prefix="release",
    )
    publisher = PostgresDomainPackagePublisher(database.sessions)
    first = _build_package(tmp_path / "v1", version="1.0.0")
    second = _build_package(tmp_path / "v2", version="1.1.0", extra_step="cross_check")

    try:
        first_draft = await publisher.create_draft(
            first,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            created_by="package-builder",
        )
        repeated_draft = await publisher.create_draft(
            first,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            created_by="package-builder",
        )
        assert repeated_draft.id == first_draft.id
        assert first_draft.status is DomainPackageStatus.DRAFT
        await _approve_version(
            publisher,
            first_draft.id,
            tenant_slug=tenant_slug,
            agent_key=agent_key,
            version="1.0.0",
        )

        first_release, concurrent_replay = await asyncio.gather(
            publisher.publish(
                first_draft.id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
                idempotency_key="release-review-v1",
                performed_by="release-manager",
                note="Activate review package 1.0.0",
            ),
            publisher.publish(
                first_draft.id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
                idempotency_key="release-review-v1",
                performed_by="release-manager",
                note="Activate review package 1.0.0",
            ),
        )
        assert concurrent_replay.id == first_release.id
        assert first_release.from_agent_version_id == baseline_version_id
        first_spec = await publisher.load_active_spec(
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        assert first_spec.version == "1.0.0"
        assert first_spec.allowed_tools == ("validate_review",)
        assert first_spec.metadata["policies"]["require_citations"] is True

        second_draft = await publisher.create_draft(
            second,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            created_by="package-builder",
        )
        await _approve_version(
            publisher,
            second_draft.id,
            tenant_slug=tenant_slug,
            agent_key=agent_key,
            version="1.1.0",
        )
        second_release = await publisher.publish(
            second_draft.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            idempotency_key="release-review-v2",
            performed_by="release-manager",
            note="Activate review package 1.1.0",
        )
        assert second_release.from_agent_version_id == first_release.to_agent_version_id
        assert (
            await publisher.load_active_spec(tenant_id=tenant_slug, agent_id=agent_key)
        ).version == "1.1.0"

        rollback = await publisher.rollback(
            second_draft.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            idempotency_key="rollback-review-v2",
            performed_by="release-manager",
            note="Regression detected after activation.",
        )
        assert rollback.from_agent_version_id == second_release.to_agent_version_id
        assert rollback.to_agent_version_id == first_release.to_agent_version_id
        assert (
            await publisher.load_active_spec(tenant_id=tenant_slug, agent_id=agent_key)
        ).version == "1.0.0"

        async with database.sessions() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(DomainPackageVersionModel)
                        .where(DomainPackageVersionModel.tenant_id == tenant_uuid)
                        .order_by(DomainPackageVersionModel.version)
                    )
                ).all()
            )
            assert [row.status for row in rows] == ["active", "rolled_back"]
            assert await session.scalar(
                select(func.count()).select_from(DomainPackageAssetModel).where(
                    DomainPackageAssetModel.tenant_id == tenant_uuid
                )
            ) == 8
            assert await session.scalar(
                select(func.count()).select_from(DomainPackageEvaluationModel).where(
                    DomainPackageEvaluationModel.tenant_id == tenant_uuid
                )
            ) == 2
            assert await session.scalar(
                select(func.count()).select_from(DomainPackageApprovalModel).where(
                    DomainPackageApprovalModel.tenant_id == tenant_uuid
                )
            ) == 2
            assert await session.scalar(
                select(func.count()).select_from(DomainPackageReleaseModel).where(
                    DomainPackageReleaseModel.tenant_id == tenant_uuid
                )
            ) == 3
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_uuid))
        await database.dispose()


@pytest.mark.asyncio
async def test_domain_package_release_gates_immutability_and_scope_isolation(
    tmp_path: Path,
) -> None:
    database = Database(Settings().database_url)
    tenant_uuid, tenant_slug, agent_key, baseline_version_id = await _create_scope(
        database,
        prefix="gates",
    )
    other_tenant_uuid, other_tenant_slug, other_agent_key, _ = await _create_scope(
        database,
        prefix="other",
    )
    publisher = PostgresDomainPackagePublisher(database.sessions)
    first = _build_package(tmp_path / "first", version="2.0.0")
    changed_same_version = _build_package(
        tmp_path / "changed",
        version="2.0.0",
        extra_step="changed",
    )
    passed = _build_package(tmp_path / "passed", version="2.1.0")

    try:
        draft = await publisher.create_draft(
            first,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            created_by="builder",
        )
        with pytest.raises(ValueError, match="immutable"):
            await publisher.create_draft(
                changed_same_version,
                tenant_id=tenant_slug,
                agent_id=agent_key,
                created_by="builder",
            )
        with pytest.raises(KeyError, match="requested scope"):
            await publisher.begin_evaluation(
                draft.id,
                tenant_id=other_tenant_slug,
                agent_id=other_agent_key,
            )
        with pytest.raises(ValueError, match="approved before publication"):
            await publisher.publish(
                draft.id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
                idempotency_key="premature",
                performed_by="release-manager",
            )

        await publisher.begin_evaluation(
            draft.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        rejected = await publisher.record_evaluation(
            draft.id,
            _evaluation("2.0.0", passed=False),
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        assert rejected.status is DomainPackageStatus.REJECTED
        with pytest.raises(ValueError, match="awaiting human approval"):
            await publisher.approve(
                draft.id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
                decided_by="reviewer",
            )

        passed_draft = await publisher.create_draft(
            passed,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            created_by="builder",
        )
        await publisher.begin_evaluation(
            passed_draft.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        await publisher.record_evaluation(
            passed_draft.id,
            _evaluation("2.1.0"),
            tenant_id=tenant_slug,
            agent_id=agent_key,
        )
        with pytest.raises(ValueError, match="approved before publication"):
            await publisher.publish(
                passed_draft.id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
                idempotency_key="missing-approval",
                performed_by="release-manager",
            )
        await publisher.approve(
            passed_draft.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            decided_by="reviewer",
        )
        release = await publisher.publish(
            passed_draft.id,
            tenant_id=tenant_slug,
            agent_id=agent_key,
            idempotency_key="approved-release",
            performed_by="release-manager",
        )
        assert release.from_agent_version_id == baseline_version_id
        with pytest.raises(ValueError, match="different release request"):
            await publisher.rollback(
                passed_draft.id,
                tenant_id=tenant_slug,
                agent_id=agent_key,
                idempotency_key="approved-release",
                performed_by="release-manager",
            )
    finally:
        async with database.sessions() as session, session.begin():
            await session.execute(
                delete(TenantModel).where(
                    TenantModel.id.in_((tenant_uuid, other_tenant_uuid))
                )
            )
        await database.dispose()
