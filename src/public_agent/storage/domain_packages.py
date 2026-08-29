from __future__ import annotations

import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.core.types import AgentSpec, utc_now
from public_agent.domains.models import (
    DomainPackage,
    DomainPackageEvaluationResult,
    DomainPackageReleaseRecord,
    DomainPackageStatus,
    DomainPackageVersionRecord,
    PreparedDomainPackage,
)
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


class PostgresDomainPackagePublisher:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_draft(
        self,
        prepared: PreparedDomainPackage,
        *,
        tenant_id: str,
        agent_id: str,
        created_by: str,
    ) -> DomainPackageVersionRecord:
        actor = _required_text(created_by, field_name="created_by")
        async with self._sessions() as session, session.begin():
            tenant, agent = await _resolve_scope(session, tenant_id, agent_id)
            package = prepared.package
            if package.id != agent.domain_id:
                raise ValueError("Domain package does not match the agent domain")
            package_version_id = uuid4()
            inserted_id = await session.scalar(
                postgres_insert(DomainPackageVersionModel)
                .values(
                    id=package_version_id,
                    tenant_id=tenant.id,
                    agent_id=agent.id,
                    domain_id=package.id,
                    version=package.version,
                    content_hash=prepared.content_hash,
                    status=DomainPackageStatus.DRAFT.value,
                    revision=1,
                    manifest=prepared.manifest,
                    total_size_bytes=prepared.total_size_bytes,
                    created_by=actor,
                )
                .on_conflict_do_nothing(
                    constraint="uq_domain_package_versions_scope_version"
                )
                .returning(DomainPackageVersionModel.id)
            )
            if inserted_id is None:
                row = await session.scalar(
                    select(DomainPackageVersionModel).where(
                        DomainPackageVersionModel.tenant_id == tenant.id,
                        DomainPackageVersionModel.agent_id == agent.id,
                        DomainPackageVersionModel.domain_id == package.id,
                        DomainPackageVersionModel.version == package.version,
                    )
                )
                if row is None:
                    raise RuntimeError("Domain package version conflict could not be resolved")
                await self._verify_existing_draft(session, row, prepared)
                return _version_record(row, tenant.slug, agent.agent_key)

            row = await session.get(DomainPackageVersionModel, package_version_id)
            if row is None:
                raise RuntimeError("Created domain package version could not be loaded")
            session.add_all(
                [
                    DomainPackageAssetModel(
                        tenant_id=tenant.id,
                        agent_id=agent.id,
                        package_version_id=row.id,
                        asset_type=asset.asset_type.value,
                        asset_key=asset.key,
                        relative_path=asset.relative_path,
                        media_type=asset.media_type,
                        content_hash=asset.content_hash,
                        size_bytes=asset.size_bytes,
                        content=asset.content,
                    )
                    for asset in prepared.assets
                ]
            )
            await session.flush()
            return _version_record(row, tenant.slug, agent.agent_key)

    async def begin_evaluation(
        self,
        package_version_id: UUID,
        *,
        tenant_id: str,
        agent_id: str,
    ) -> DomainPackageVersionRecord:
        async with self._sessions() as session, session.begin():
            tenant, agent = await _resolve_scope(session, tenant_id, agent_id)
            row = await _scoped_version(
                session,
                package_version_id,
                tenant.id,
                agent,
                for_update=True,
            )
            if row.status == DomainPackageStatus.EVALUATING.value:
                return _version_record(row, tenant.slug, agent.agent_key)
            if row.status != DomainPackageStatus.DRAFT.value:
                raise ValueError("Only draft domain packages can begin evaluation")
            row.status = DomainPackageStatus.EVALUATING.value
            row.revision += 1
            row.updated_at = utc_now()
            return _version_record(row, tenant.slug, agent.agent_key)

    async def record_evaluation(
        self,
        package_version_id: UUID,
        evaluation: DomainPackageEvaluationResult,
        *,
        tenant_id: str,
        agent_id: str,
    ) -> DomainPackageVersionRecord:
        _ensure_json(evaluation.metrics, field_name="evaluation metrics")
        async with self._sessions() as session, session.begin():
            tenant, agent = await _resolve_scope(session, tenant_id, agent_id)
            row = await _scoped_version(
                session,
                package_version_id,
                tenant.id,
                agent,
                for_update=True,
            )
            existing = await session.scalar(
                select(DomainPackageEvaluationModel).where(
                    DomainPackageEvaluationModel.package_version_id == row.id,
                    DomainPackageEvaluationModel.report_hash == evaluation.report_hash,
                )
            )
            if existing is not None:
                self._verify_evaluation(existing, evaluation)
                return _version_record(row, tenant.slug, agent.agent_key)
            if row.status != DomainPackageStatus.EVALUATING.value:
                raise ValueError("Domain package must be evaluating before recording a result")
            package = DomainPackage.model_validate(row.manifest)
            if package.evaluation_suite and evaluation.suite != package.evaluation_suite:
                raise ValueError("Evaluation suite does not match the domain package manifest")
            session.add(
                DomainPackageEvaluationModel(
                    tenant_id=tenant.id,
                    agent_id=agent.id,
                    package_version_id=row.id,
                    suite=evaluation.suite,
                    dataset_version=evaluation.dataset_version,
                    passed=evaluation.passed,
                    score=evaluation.score,
                    summary=evaluation.summary,
                    metrics=evaluation.metrics,
                    report_hash=evaluation.report_hash,
                    created_at=evaluation.created_at,
                )
            )
            row.status = (
                DomainPackageStatus.AWAITING_APPROVAL.value
                if evaluation.passed
                else DomainPackageStatus.REJECTED.value
            )
            row.revision += 1
            row.updated_at = utc_now()
            return _version_record(row, tenant.slug, agent.agent_key)

    async def approve(
        self,
        package_version_id: UUID,
        *,
        tenant_id: str,
        agent_id: str,
        decided_by: str,
        decision_note: str | None = None,
    ) -> DomainPackageVersionRecord:
        return await self._decide_approval(
            package_version_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            approved=True,
            decided_by=decided_by,
            decision_note=decision_note,
        )

    async def reject(
        self,
        package_version_id: UUID,
        *,
        tenant_id: str,
        agent_id: str,
        decided_by: str,
        decision_note: str | None = None,
    ) -> DomainPackageVersionRecord:
        return await self._decide_approval(
            package_version_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            approved=False,
            decided_by=decided_by,
            decision_note=decision_note,
        )

    async def publish(
        self,
        package_version_id: UUID,
        *,
        tenant_id: str,
        agent_id: str,
        idempotency_key: str,
        performed_by: str,
        note: str | None = None,
    ) -> DomainPackageReleaseRecord:
        key = _required_text(idempotency_key, field_name="idempotency_key", max_length=200)
        actor = _required_text(performed_by, field_name="performed_by")
        async with self._sessions() as session, session.begin():
            tenant, agent = await _resolve_scope(
                session,
                tenant_id,
                agent_id,
                for_update=True,
            )
            existing = await _release_by_key(session, tenant.id, agent.id, key)
            if existing is not None:
                _verify_release_request(
                    existing,
                    package_version_id=package_version_id,
                    action="activate",
                    performed_by=actor,
                    note=note,
                )
                return _release_record(existing)
            row = await _scoped_version(
                session,
                package_version_id,
                tenant.id,
                agent,
                for_update=True,
            )
            if (
                row.status == DomainPackageStatus.ACTIVE.value
                and agent.active_version_id == row.agent_version_id
            ):
                active_release = await _latest_activation(session, row.id)
                if active_release is None:
                    raise RuntimeError("Active domain package is missing its release audit")
                return _release_record(active_release)
            if row.status != DomainPackageStatus.APPROVED.value:
                raise ValueError("Domain package must be approved before publication")
            await self._verify_release_gates(session, row)
            package = DomainPackage.model_validate(row.manifest)
            configuration = await self._agent_configuration(session, row, package)
            agent_version = await session.scalar(
                select(AgentVersionModel)
                .where(
                    AgentVersionModel.agent_id == agent.id,
                    AgentVersionModel.version == package.version,
                )
                .with_for_update()
            )
            if agent_version is None:
                agent_version = AgentVersionModel(
                    tenant_id=tenant.id,
                    agent_id=agent.id,
                    version=package.version,
                    instructions=package.instructions,
                    memory_namespace=package.memory_namespace,
                    configuration=configuration,
                )
                session.add(agent_version)
                await session.flush()
            else:
                _verify_agent_version(agent_version, tenant.id, package, configuration)

            previous_agent_version_id = agent.active_version_id
            if previous_agent_version_id == agent_version.id:
                raise ValueError("Agent version is already active without matching package state")
            previous_package = None
            if previous_agent_version_id is not None:
                await _locked_scoped_agent_version(
                    session,
                    previous_agent_version_id,
                    tenant.id,
                    agent.id,
                )
                previous_package = await session.scalar(
                    select(DomainPackageVersionModel)
                    .where(
                        DomainPackageVersionModel.agent_version_id
                        == previous_agent_version_id
                    )
                    .with_for_update()
                )
                if previous_package is not None:
                    if previous_package.status != DomainPackageStatus.ACTIVE.value:
                        raise RuntimeError("Previous domain package is not active")
                    previous_package.status = DomainPackageStatus.DEPRECATED.value
                    previous_package.revision += 1
                    previous_package.updated_at = utc_now()

            row.agent_version_id = agent_version.id
            row.status = DomainPackageStatus.ACTIVE.value
            row.revision += 1
            row.updated_at = utc_now()
            agent.active_version_id = agent_version.id
            agent.updated_at = utc_now()
            release = DomainPackageReleaseModel(
                id=uuid4(),
                tenant_id=tenant.id,
                agent_id=agent.id,
                package_version_id=row.id,
                domain_id=row.domain_id,
                action="activate",
                from_agent_version_id=previous_agent_version_id,
                to_agent_version_id=agent_version.id,
                idempotency_key=key,
                performed_by=actor,
                note=note,
            )
            session.add(release)
            await session.flush()
            return _release_record(release)

    async def rollback(
        self,
        package_version_id: UUID,
        *,
        tenant_id: str,
        agent_id: str,
        idempotency_key: str,
        performed_by: str,
        note: str | None = None,
    ) -> DomainPackageReleaseRecord:
        key = _required_text(idempotency_key, field_name="idempotency_key", max_length=200)
        actor = _required_text(performed_by, field_name="performed_by")
        async with self._sessions() as session, session.begin():
            tenant, agent = await _resolve_scope(
                session,
                tenant_id,
                agent_id,
                for_update=True,
            )
            existing = await _release_by_key(session, tenant.id, agent.id, key)
            if existing is not None:
                _verify_release_request(
                    existing,
                    package_version_id=package_version_id,
                    action="rollback",
                    performed_by=actor,
                    note=note,
                )
                return _release_record(existing)
            row = await _scoped_version(
                session,
                package_version_id,
                tenant.id,
                agent,
                for_update=True,
            )
            if row.status != DomainPackageStatus.ACTIVE.value:
                raise ValueError("Only the active domain package can be rolled back")
            if row.agent_version_id is None or agent.active_version_id != row.agent_version_id:
                raise ValueError("Domain package is not the agent's current active version")
            activation = await _latest_activation(session, row.id)
            if activation is None or activation.to_agent_version_id != row.agent_version_id:
                raise RuntimeError("Active domain package is missing a valid activation audit")
            latest_agent_release = await session.scalar(
                select(DomainPackageReleaseModel)
                .where(DomainPackageReleaseModel.agent_id == agent.id)
                .order_by(
                    DomainPackageReleaseModel.created_at.desc(),
                    DomainPackageReleaseModel.id.desc(),
                )
                .limit(1)
            )
            if latest_agent_release is None or latest_agent_release.id != activation.id:
                raise ValueError(
                    "Active version was restored by rollback and cannot be rolled back again"
                )

            previous_agent_version_id = activation.from_agent_version_id
            previous_package = None
            if previous_agent_version_id is not None:
                await _locked_scoped_agent_version(
                    session,
                    previous_agent_version_id,
                    tenant.id,
                    agent.id,
                )
                previous_package = await session.scalar(
                    select(DomainPackageVersionModel)
                    .where(
                        DomainPackageVersionModel.agent_version_id
                        == previous_agent_version_id
                    )
                    .with_for_update()
                )
                if previous_package is not None:
                    if (
                        previous_package.tenant_id != tenant.id
                        or previous_package.agent_id != agent.id
                    ):
                        raise ValueError("Rollback target is outside the current agent scope")
                    if previous_package.status != DomainPackageStatus.DEPRECATED.value:
                        raise RuntimeError("Rollback target domain package is not deprecated")
                    previous_package.status = DomainPackageStatus.ACTIVE.value
                    previous_package.revision += 1
                    previous_package.updated_at = utc_now()

            current_agent_version_id = row.agent_version_id
            row.status = DomainPackageStatus.ROLLED_BACK.value
            row.revision += 1
            row.updated_at = utc_now()
            agent.active_version_id = previous_agent_version_id
            agent.updated_at = utc_now()
            release = DomainPackageReleaseModel(
                id=uuid4(),
                tenant_id=tenant.id,
                agent_id=agent.id,
                package_version_id=row.id,
                domain_id=row.domain_id,
                action="rollback",
                from_agent_version_id=current_agent_version_id,
                to_agent_version_id=previous_agent_version_id,
                idempotency_key=key,
                performed_by=actor,
                note=note,
            )
            session.add(release)
            await session.flush()
            return _release_record(release)

    async def load_active_spec(self, *, tenant_id: str, agent_id: str) -> AgentSpec:
        async with self._sessions() as session:
            tenant, agent = await _resolve_scope(session, tenant_id, agent_id)
            if agent.active_version_id is None:
                raise KeyError("Agent has no active version")
            agent_version = await session.get(AgentVersionModel, agent.active_version_id)
            if agent_version is None:
                raise RuntimeError("Agent active version is missing")
            package_row = await session.scalar(
                select(DomainPackageVersionModel).where(
                    DomainPackageVersionModel.tenant_id == tenant.id,
                    DomainPackageVersionModel.agent_id == agent.id,
                    DomainPackageVersionModel.agent_version_id == agent_version.id,
                    DomainPackageVersionModel.status == DomainPackageStatus.ACTIVE.value,
                )
            )
            if package_row is None:
                raise KeyError("Agent active version is not a published domain package")
            package = DomainPackage.model_validate(package_row.manifest)
            if (
                package.id != agent.domain_id
                or package.version != agent_version.version
                or package.instructions != agent_version.instructions
                or package.memory_namespace != agent_version.memory_namespace
            ):
                raise RuntimeError("Active agent version does not match its domain package")
            domain_configuration = agent_version.configuration.get("domain_package")
            if not isinstance(domain_configuration, dict):
                raise RuntimeError("Active agent version lacks domain package configuration")
            if domain_configuration.get("content_hash") != package_row.content_hash:
                raise RuntimeError("Active domain package content hash does not match")
            if domain_configuration.get("manifest") != package_row.manifest:
                raise RuntimeError("Active domain package manifest does not match")
            assets = tuple(
                (
                    await session.scalars(
                        select(DomainPackageAssetModel)
                        .where(DomainPackageAssetModel.package_version_id == package_row.id)
                        .order_by(
                            DomainPackageAssetModel.asset_type,
                            DomainPackageAssetModel.asset_key,
                        )
                    )
                ).all()
            )
            expected_assets = [
                {
                    "asset_type": asset.asset_type,
                    "key": asset.asset_key,
                    "relative_path": asset.relative_path,
                    "media_type": asset.media_type,
                    "content_hash": asset.content_hash,
                    "size_bytes": asset.size_bytes,
                }
                for asset in assets
            ]
            if domain_configuration.get("assets") != expected_assets:
                raise RuntimeError("Active domain package assets do not match")
            return package.to_agent_spec().model_copy(update={"id": agent.agent_key})

    async def _decide_approval(
        self,
        package_version_id: UUID,
        *,
        tenant_id: str,
        agent_id: str,
        approved: bool,
        decided_by: str,
        decision_note: str | None,
    ) -> DomainPackageVersionRecord:
        actor = _required_text(decided_by, field_name="decided_by")
        status = "approved" if approved else "rejected"
        async with self._sessions() as session, session.begin():
            tenant, agent = await _resolve_scope(session, tenant_id, agent_id)
            row = await _scoped_version(
                session,
                package_version_id,
                tenant.id,
                agent,
                for_update=True,
            )
            existing = await session.scalar(
                select(DomainPackageApprovalModel).where(
                    DomainPackageApprovalModel.package_version_id == row.id
                )
            )
            if existing is not None:
                if (
                    existing.status != status
                    or existing.decided_by != actor
                    or existing.decision_note != decision_note
                ):
                    raise ValueError("Domain package already has a different approval decision")
                return _version_record(row, tenant.slug, agent.agent_key)
            if row.status != DomainPackageStatus.AWAITING_APPROVAL.value:
                raise ValueError("Domain package is not awaiting human approval")
            session.add(
                DomainPackageApprovalModel(
                    tenant_id=tenant.id,
                    agent_id=agent.id,
                    package_version_id=row.id,
                    status=status,
                    reason=(
                        "Domain package passed evaluation and was approved for publication"
                        if approved
                        else "Domain package was rejected during human review"
                    ),
                    decided_by=actor,
                    decision_note=decision_note,
                )
            )
            row.status = (
                DomainPackageStatus.APPROVED.value
                if approved
                else DomainPackageStatus.REJECTED.value
            )
            row.revision += 1
            row.updated_at = utc_now()
            return _version_record(row, tenant.slug, agent.agent_key)

    async def _verify_existing_draft(
        self,
        session: AsyncSession,
        row: DomainPackageVersionModel,
        prepared: PreparedDomainPackage,
    ) -> None:
        if (
            row.content_hash != prepared.content_hash
            or row.manifest != prepared.manifest
            or row.total_size_bytes != prepared.total_size_bytes
        ):
            raise ValueError(
                "Domain package versions are immutable; publish changed content as a new version"
            )
        existing_assets = tuple(
            (
                await session.scalars(
                    select(DomainPackageAssetModel)
                    .where(DomainPackageAssetModel.package_version_id == row.id)
                    .order_by(
                        DomainPackageAssetModel.asset_type,
                        DomainPackageAssetModel.asset_key,
                    )
                )
            ).all()
        )
        expected = tuple(
            sorted(
                (
                    asset.asset_type.value,
                    asset.key,
                    asset.relative_path,
                    asset.media_type,
                    asset.content_hash,
                    asset.size_bytes,
                )
                for asset in prepared.assets
            )
        )
        actual = tuple(
            (
                asset.asset_type,
                asset.asset_key,
                asset.relative_path,
                asset.media_type,
                asset.content_hash,
                asset.size_bytes,
            )
            for asset in existing_assets
        )
        if actual != expected:
            raise RuntimeError("Stored domain package assets do not match the content hash")

    @staticmethod
    def _verify_evaluation(
        existing: DomainPackageEvaluationModel,
        evaluation: DomainPackageEvaluationResult,
    ) -> None:
        if (
            existing.suite != evaluation.suite
            or existing.dataset_version != evaluation.dataset_version
            or existing.passed != evaluation.passed
            or existing.score != evaluation.score
            or existing.summary != evaluation.summary
            or existing.metrics != evaluation.metrics
        ):
            raise ValueError("Evaluation report hash is already bound to different content")

    @staticmethod
    async def _verify_release_gates(
        session: AsyncSession,
        row: DomainPackageVersionModel,
    ) -> None:
        evaluation = await session.scalar(
            select(DomainPackageEvaluationModel)
            .where(DomainPackageEvaluationModel.package_version_id == row.id)
            .order_by(
                DomainPackageEvaluationModel.created_at.desc(),
                DomainPackageEvaluationModel.id.desc(),
            )
            .limit(1)
        )
        if evaluation is None or not evaluation.passed:
            raise ValueError("Domain package has no passing evaluation")
        approval = await session.scalar(
            select(DomainPackageApprovalModel).where(
                DomainPackageApprovalModel.package_version_id == row.id,
                DomainPackageApprovalModel.status == "approved",
            )
        )
        if approval is None:
            raise ValueError("Domain package has no approved human decision")

    @staticmethod
    async def _agent_configuration(
        session: AsyncSession,
        row: DomainPackageVersionModel,
        package: DomainPackage,
    ) -> dict[str, object]:
        assets = tuple(
            (
                await session.scalars(
                    select(DomainPackageAssetModel)
                    .where(DomainPackageAssetModel.package_version_id == row.id)
                    .order_by(
                        DomainPackageAssetModel.asset_type,
                        DomainPackageAssetModel.asset_key,
                    )
                )
            ).all()
        )
        if not assets:
            raise RuntimeError("Domain package has no persisted assets")
        return {
            "domain_package": {
                "package_version_id": str(row.id),
                "domain_id": package.id,
                "version": package.version,
                "content_hash": row.content_hash,
                "manifest": row.manifest,
                "assets": [
                    {
                        "asset_type": asset.asset_type,
                        "key": asset.asset_key,
                        "relative_path": asset.relative_path,
                        "media_type": asset.media_type,
                        "content_hash": asset.content_hash,
                        "size_bytes": asset.size_bytes,
                    }
                    for asset in assets
                ],
            }
        }


async def _resolve_scope(
    session: AsyncSession,
    tenant_slug: str,
    agent_key: str,
    *,
    for_update: bool = False,
) -> tuple[TenantModel, AgentModel]:
    tenant = await session.scalar(select(TenantModel).where(TenantModel.slug == tenant_slug))
    if tenant is None:
        raise KeyError(f"Unknown tenant: {tenant_slug}")
    statement = select(AgentModel).where(
        AgentModel.tenant_id == tenant.id,
        AgentModel.agent_key == agent_key,
    )
    if for_update:
        statement = statement.with_for_update()
    agent = await session.scalar(statement)
    if agent is None:
        raise KeyError(f"Unknown agent for tenant {tenant_slug}: {agent_key}")
    return tenant, agent


async def _scoped_version(
    session: AsyncSession,
    package_version_id: UUID,
    tenant_id: UUID,
    agent: AgentModel,
    *,
    for_update: bool,
) -> DomainPackageVersionModel:
    statement = select(DomainPackageVersionModel).where(
        DomainPackageVersionModel.id == package_version_id,
        DomainPackageVersionModel.tenant_id == tenant_id,
        DomainPackageVersionModel.agent_id == agent.id,
        DomainPackageVersionModel.domain_id == agent.domain_id,
    )
    if for_update:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise KeyError("Unknown domain package version in requested scope")
    return row


async def _release_by_key(
    session: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    key: str,
) -> DomainPackageReleaseModel | None:
    row: DomainPackageReleaseModel | None = await session.scalar(
        select(DomainPackageReleaseModel).where(
            DomainPackageReleaseModel.tenant_id == tenant_id,
            DomainPackageReleaseModel.agent_id == agent_id,
            DomainPackageReleaseModel.idempotency_key == key,
        )
    )
    return row


async def _latest_activation(
    session: AsyncSession,
    package_version_id: UUID,
) -> DomainPackageReleaseModel | None:
    row: DomainPackageReleaseModel | None = await session.scalar(
        select(DomainPackageReleaseModel)
        .where(
            DomainPackageReleaseModel.package_version_id == package_version_id,
            DomainPackageReleaseModel.action == "activate",
        )
        .order_by(
            DomainPackageReleaseModel.created_at.desc(),
            DomainPackageReleaseModel.id.desc(),
        )
        .limit(1)
    )
    return row


async def _locked_scoped_agent_version(
    session: AsyncSession,
    agent_version_id: UUID,
    tenant_id: UUID,
    agent_id: UUID,
) -> AgentVersionModel:
    row = await session.scalar(
        select(AgentVersionModel)
        .where(
            AgentVersionModel.id == agent_version_id,
            AgentVersionModel.tenant_id == tenant_id,
            AgentVersionModel.agent_id == agent_id,
        )
        .with_for_update()
    )
    if row is None:
        raise RuntimeError("Agent active version is outside the current agent scope")
    return row


def _verify_agent_version(
    row: AgentVersionModel,
    tenant_id: UUID,
    package: DomainPackage,
    configuration: dict[str, object],
) -> None:
    if (
        row.tenant_id != tenant_id
        or row.instructions != package.instructions
        or row.memory_namespace != package.memory_namespace
        or row.configuration != configuration
    ):
        raise ValueError(
            "Agent versions are immutable; the existing version has different package content"
        )


def _verify_release_request(
    row: DomainPackageReleaseModel,
    *,
    package_version_id: UUID,
    action: str,
    performed_by: str,
    note: str | None,
) -> None:
    if (
        row.package_version_id != package_version_id
        or row.action != action
        or row.performed_by != performed_by
        or row.note != note
    ):
        raise ValueError("Idempotency key is already bound to a different release request")


def _version_record(
    row: DomainPackageVersionModel,
    tenant_slug: str,
    agent_key: str,
) -> DomainPackageVersionRecord:
    return DomainPackageVersionRecord(
        id=row.id,
        tenant_id=tenant_slug,
        agent_id=agent_key,
        domain_id=row.domain_id,
        version=row.version,
        content_hash=row.content_hash,
        status=DomainPackageStatus(row.status),
        revision=row.revision,
        agent_version_id=row.agent_version_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _release_record(row: DomainPackageReleaseModel) -> DomainPackageReleaseRecord:
    return DomainPackageReleaseRecord(
        id=row.id,
        package_version_id=row.package_version_id,
        action=row.action,
        from_agent_version_id=row.from_agent_version_id,
        to_agent_version_id=row.to_agent_version_id,
        idempotency_key=row.idempotency_key,
        performed_by=row.performed_by,
        note=row.note,
        created_at=row.created_at,
    )


def _required_text(value: str, *, field_name: str, max_length: int = 200) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    return normalized


def _ensure_json(value: object, *, field_name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain finite JSON values") from exc
