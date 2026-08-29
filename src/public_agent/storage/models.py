from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from public_agent.knowledge.base import KNOWLEDGE_EMBEDDING_DIMENSIONS


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TenantModel(TimestampMixin, Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AgentModel(TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_key", name="uq_agents_tenant_key"),
        UniqueConstraint("id", "tenant_id", name="uq_agents_id_tenant"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    active_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "agent_versions.id",
            name="fk_agents_active_version",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )


class APIPrincipalModel(TimestampMixin, Base):
    __tablename__ = "api_principals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_api_principals_status",
        ),
        CheckConstraint(
            "cardinality(permissions) BETWEEN 1 AND 100",
            name="ck_api_principals_permissions",
        ),
        UniqueConstraint(
            "tenant_id",
            "subject",
            name="uq_api_principals_tenant_subject",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_api_principals_id_tenant"),
        Index("ix_api_principals_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    permissions: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False)
    all_agents: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class APIPrincipalAgentGrantModel(Base):
    __tablename__ = "api_principal_agent_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ("principal_id", "tenant_id"),
            ("api_principals.id", "api_principals.tenant_id"),
            ondelete="CASCADE",
            name="fk_api_principal_agent_grants_principal_scope",
        ),
        ForeignKeyConstraint(
            ("agent_id", "tenant_id"),
            ("agents.id", "agents.tenant_id"),
            ondelete="CASCADE",
            name="fk_api_principal_agent_grants_agent_scope",
        ),
        Index("ix_api_principal_agent_grants_agent", "tenant_id", "agent_id"),
    )

    principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)


class APITokenModel(TimestampMixin, Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        CheckConstraint(
            "octet_length(secret_digest) = 32",
            name="ck_api_tokens_digest_size",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_api_tokens_expiry",
        ),
        ForeignKeyConstraint(
            ("principal_id", "tenant_id"),
            ("api_principals.id", "api_principals.tenant_id"),
            ondelete="CASCADE",
            name="fk_api_tokens_principal_scope",
        ),
        UniqueConstraint("prefix", name="uq_api_tokens_prefix"),
        Index(
            "ix_api_tokens_principal_active",
            "principal_id",
            "revoked_at",
            "expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    secret_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthenticationAuditEventModel(Base):
    __tablename__ = "authentication_audit_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success','denied','conflict')",
            name="ck_authentication_audit_events_outcome",
        ),
        Index(
            "ix_authentication_audit_events_tenant_created",
            "tenant_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_authentication_audit_events_actor_created",
            "actor_principal_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    actor_principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    actor_token_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    target_token_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AgentVersionModel(TimestampMixin, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    memory_namespace: Mapped[str] = mapped_column(String(150), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class DomainPackageVersionModel(TimestampMixin, Base):
    __tablename__ = "domain_package_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'draft','evaluating','awaiting_approval','approved','active','deprecated',"
            "'rolled_back','rejected'"
            ")",
            name="ck_domain_package_versions_status",
        ),
        CheckConstraint("revision > 0", name="ck_domain_package_versions_revision"),
        CheckConstraint(
            "total_size_bytes >= 0",
            name="ck_domain_package_versions_total_size",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_domain_package_versions_content_hash",
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "domain_id",
            "version",
            name="uq_domain_package_versions_scope_version",
        ),
        UniqueConstraint(
            "agent_version_id",
            name="uq_domain_package_versions_agent_version",
        ),
        Index(
            "ix_domain_package_versions_scope_status",
            "tenant_id",
            "agent_id",
            "domain_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )


class DomainPackageAssetModel(Base):
    __tablename__ = "domain_package_assets"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('instructions','skill','policy','workflow','evaluation')",
            name="ck_domain_package_assets_type",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_domain_package_assets_size"),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_domain_package_assets_content_hash",
        ),
        UniqueConstraint(
            "package_version_id",
            "asset_type",
            "asset_key",
            name="uq_domain_package_assets_version_type_key",
        ),
        UniqueConstraint(
            "package_version_id",
            "relative_path",
            name="uq_domain_package_assets_version_path",
        ),
        Index("ix_domain_package_assets_version", "package_version_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    package_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("domain_package_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_key: Mapped[str] = mapped_column(String(100), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class DomainPackageEvaluationModel(Base):
    __tablename__ = "domain_package_evaluations"
    __table_args__ = (
        CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_domain_package_evaluations_score",
        ),
        CheckConstraint(
            "report_hash ~ '^[0-9a-f]{64}$'",
            name="ck_domain_package_evaluations_report_hash",
        ),
        UniqueConstraint(
            "package_version_id",
            "report_hash",
            name="uq_domain_package_evaluations_version_report",
        ),
        Index(
            "ix_domain_package_evaluations_version_created",
            "package_version_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    package_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("domain_package_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    suite: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class DomainPackageApprovalModel(Base):
    __tablename__ = "domain_package_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('approved','rejected')",
            name="ck_domain_package_approvals_status",
        ),
        UniqueConstraint(
            "package_version_id",
            name="uq_domain_package_approvals_version",
        ),
        Index("ix_domain_package_approvals_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    package_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("domain_package_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(200), nullable=False)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class DomainPackageReleaseModel(Base):
    __tablename__ = "domain_package_releases"
    __table_args__ = (
        CheckConstraint(
            "action IN ('activate','rollback')",
            name="ck_domain_package_releases_action",
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "idempotency_key",
            name="uq_domain_package_releases_scope_idempotency",
        ),
        Index(
            "ix_domain_package_releases_version_created",
            "package_version_id",
            "created_at",
        ),
        Index(
            "ix_domain_package_releases_scope_created",
            "tenant_id",
            "agent_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    package_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("domain_package_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_agent_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    to_agent_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    performed_by: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RunModel(TimestampMixin, Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'queued','running','waiting_approval','succeeded','failed','canceled','timed_out'"
            ")",
            name="ck_runs_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_runs_id_tenant"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_runs_tenant_idempotency"),
        CheckConstraint(
            "(resume_token IS NULL AND resume_lease_expires_at IS NULL) OR "
            "(resume_token IS NOT NULL AND resume_lease_expires_at IS NOT NULL "
            "AND status = 'running')",
            name="ck_runs_resume_lease_consistent",
        ),
        Index("ix_runs_tenant_agent_created", "tenant_id", "agent_id", "created_at"),
        Index("ix_runs_status_resume_lease", "status", "resume_lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    agent_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    task: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    resume_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )


class RunEventModel(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
        Index("ix_run_events_tenant_run", "tenant_id", "run_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OutboxJobModel(TimestampMixin, Base):
    __tablename__ = "outbox_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','retry_wait','succeeded','dead_letter')",
            name="ck_outbox_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_outbox_jobs_attempts"),
        CheckConstraint(
            "attempts_in_cycle >= 0",
            name="ck_outbox_jobs_attempts_in_cycle",
        ),
        CheckConstraint("version >= 1", name="ck_outbox_jobs_version"),
        CheckConstraint(
            "last_processing_duration_ms IS NULL OR last_processing_duration_ms >= 0",
            name="ck_outbox_jobs_last_processing_duration",
        ),
        CheckConstraint(
            "total_processing_duration_ms >= 0",
            name="ck_outbox_jobs_total_processing_duration",
        ),
        CheckConstraint(
            "max_attempts BETWEEN 1 AND 100",
            name="ck_outbox_jobs_max_attempts",
        ),
        CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND worker_id IS NOT NULL) OR "
            "(status <> 'processing' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL AND worker_id IS NULL)",
            name="ck_outbox_jobs_lease_consistent",
        ),
        ForeignKeyConstraint(
            ("run_id", "tenant_id"),
            ("runs.id", "runs.tenant_id"),
            ondelete="CASCADE",
            name="fk_outbox_jobs_run_scope",
        ),
        UniqueConstraint(
            "job_type",
            "run_id",
            "handler_version",
            name="uq_outbox_jobs_run_handler",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_outbox_jobs_id_tenant"),
        Index(
            "ix_outbox_jobs_claim",
            "status",
            "available_at",
            "lease_expires_at",
            "created_at",
            "id",
        ),
        Index(
            "ix_outbox_jobs_handler_status_available",
            "job_type",
            "handler_version",
            "status",
            "available_at",
        ),
        Index(
            "ix_outbox_jobs_handler_completed_duration",
            "job_type",
            "handler_version",
            "completed_at",
            "total_processing_duration_ms",
        ),
        Index("ix_outbox_jobs_tenant_run", "tenant_id", "run_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts_in_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    worker_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_processing_duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    total_processing_duration_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )


class OutboxJobArchiveModel(Base):
    __tablename__ = "outbox_job_archives"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded','dead_letter')",
            name="ck_outbox_job_archives_terminal_status",
        ),
        CheckConstraint("version >= 1", name="ck_outbox_job_archives_version"),
        CheckConstraint("attempts >= 0", name="ck_outbox_job_archives_attempts"),
        CheckConstraint(
            "attempts_in_cycle >= 0",
            name="ck_outbox_job_archives_attempts_in_cycle",
        ),
        CheckConstraint(
            "total_processing_duration_ms >= 0",
            name="ck_outbox_job_archives_total_processing_duration",
        ),
        Index(
            "ix_outbox_job_archives_handler_completed",
            "job_type",
            "handler_version",
            "completed_at",
        ),
        Index(
            "ix_outbox_job_archives_tenant_run",
            "tenant_id",
            "run_id",
        ),
        {"postgresql_partition_by": "RANGE (completed_at)"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts_in_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_processing_duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    total_processing_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReflectionCapacityObservationModel(Base):
    __tablename__ = "reflection_capacity_observations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy','warning','critical')",
            name="ck_reflection_capacity_observations_status",
        ),
        UniqueConstraint(
            "job_type",
            "handler_version",
            "observed_at",
            name="uq_reflection_capacity_observations_sample",
        ),
        Index(
            "ix_reflection_capacity_observations_handler_observed",
            "job_type",
            "handler_version",
            "observed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    ready: Mapped[int] = mapped_column(Integer, nullable=False)
    processing: Mapped[int] = mapped_column(Integer, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False)
    dead_letter: Mapped[int] = mapped_column(Integer, nullable=False)
    oldest_ready_age_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    active_workers: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_workers: Mapped[int] = mapped_column(Integer, nullable=False)
    errored_workers: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_jobs: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_workers: Mapped[int] = mapped_column(Integer, nullable=False)
    scale_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReflectionCapacityCalibrationModel(Base):
    __tablename__ = "reflection_capacity_calibrations"
    __table_args__ = (
        CheckConstraint(
            "sample_count > 0",
            name="ck_reflection_capacity_calibrations_sample_count",
        ),
        Index(
            "ix_reflection_capacity_calibrations_handler_created",
            "job_type",
            "handler_version",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dead_letter_count: Mapped[int] = mapped_column(Integer, nullable=False)
    p50_processing_ms: Mapped[float] = mapped_column(Float, nullable=False)
    p95_processing_ms: Mapped[float] = mapped_column(Float, nullable=False)
    p99_processing_ms: Mapped[float] = mapped_column(Float, nullable=False)
    observed_jobs_per_hour: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReflectionCapacityPolicyModel(Base):
    __tablename__ = "reflection_capacity_policies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','superseded','rolled_back')",
            name="ck_reflection_capacity_policies_status",
        ),
        CheckConstraint(
            "source_type IN ('settings_baseline','calibration')",
            name="ck_reflection_capacity_policies_source_type",
        ),
        CheckConstraint(
            "policy_version >= 1",
            name="ck_reflection_capacity_policies_version",
        ),
        UniqueConstraint(
            "job_type",
            "handler_version",
            "policy_version",
            name="uq_reflection_capacity_policies_version",
        ),
        Index(
            "uq_reflection_capacity_policies_active",
            "job_type",
            "handler_version",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_reflection_capacity_policies_handler_created",
            "job_type",
            "handler_version",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_calibration_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_calibrations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    previous_policy_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_policies.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReflectionCapacityChangeRequestModel(TimestampMixin, Base):
    __tablename__ = "reflection_capacity_change_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'pending_window','awaiting_approval','approved','rejected',"
            "'cooling_down','effective','ineffective','rolled_back'"
            ")",
            name="ck_reflection_capacity_change_requests_status",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_reflection_capacity_change_requests_version",
        ),
        CheckConstraint(
            "window_required_seconds BETWEEN 60 AND 2592000",
            name="ck_reflection_capacity_change_requests_window_seconds",
        ),
        CheckConstraint(
            "window_minimum_observations BETWEEN 2 AND 100000",
            name="ck_reflection_capacity_change_requests_window_samples",
        ),
        UniqueConstraint(
            "calibration_id",
            name="uq_reflection_capacity_change_requests_calibration",
        ),
        Index(
            "ix_reflection_capacity_change_requests_handler_status",
            "job_type",
            "handler_version",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calibration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_calibrations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_policy_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    published_policy_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_policies.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    proposed_thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    window_required_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    window_minimum_observations: Mapped[int] = mapped_column(Integer, nullable=False)
    window_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    window_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(200), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejected_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    published_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    effect_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rolled_back_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rollback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReflectionCapacityGovernanceAlertModel(TimestampMixin, Base):
    __tablename__ = "reflection_capacity_governance_alerts"
    __table_args__ = (
        CheckConstraint(
            "alert_type IN ('policy_drift')",
            name="ck_reflection_capacity_governance_alerts_type",
        ),
        CheckConstraint(
            "severity IN ('warning','critical')",
            name="ck_reflection_capacity_governance_alerts_severity",
        ),
        CheckConstraint(
            "status IN ('open','acknowledged','resolved')",
            name="ck_reflection_capacity_governance_alerts_status",
        ),
        CheckConstraint(
            "version >= 1 AND sample_count >= 1 AND reopened_count >= 0",
            name="ck_reflection_capacity_governance_alerts_counts",
        ),
        CheckConstraint(
            "(expected_policy_id IS NULL) = (expected_policy_version IS NULL) "
            "AND (expected_policy_version IS NULL OR expected_policy_version >= 1)",
            name="ck_reflection_capacity_governance_alerts_policy",
        ),
        CheckConstraint(
            "(status = 'open' AND acknowledged_at IS NULL AND resolved_at IS NULL) "
            "OR (status = 'acknowledged' AND acknowledged_by IS NOT NULL "
            "AND acknowledged_principal_id IS NOT NULL "
            "AND acknowledged_token_id IS NOT NULL "
            "AND acknowledged_at IS NOT NULL AND resolved_at IS NULL) "
            "OR (status = 'resolved' AND resolved_at IS NOT NULL)",
            name="ck_reflection_capacity_governance_alerts_lifecycle",
        ),
        CheckConstraint(
            "octet_length(expected_fingerprint) = 64 "
            "AND octet_length(observed_fingerprint) = 64 "
            "AND octet_length(dedupe_key) = 64",
            name="ck_reflection_capacity_governance_alerts_fingerprints",
        ),
        UniqueConstraint(
            "dedupe_key",
            name="uq_reflection_capacity_governance_alerts_dedupe",
        ),
        Index(
            "ix_reflection_capacity_governance_alerts_handler_status",
            "job_type",
            "handler_version",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_reflection_capacity_governance_alerts_expected",
            "job_type",
            "handler_version",
            "expected_fingerprint",
            "last_observation_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_policy_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_policies.id", ondelete="RESTRICT"),
        nullable=True,
    )
    expected_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observation_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    acknowledged_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acknowledged_principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    acknowledged_token_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reopened_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ReflectionCapacityGovernanceIncidentModel(TimestampMixin, Base):
    __tablename__ = "reflection_capacity_governance_incidents"
    __table_args__ = (
        CheckConstraint(
            "signal IN ('audit_failure_spike','alert_sla_breached',"
            "'alert_reopen_repeat','drill_check_failed',"
            "'knowledge_unsafe_persistent','knowledge_degraded_repeat',"
            "'knowledge_requarantined')",
            name="ck_reflection_capacity_governance_incidents_signal",
        ),
        CheckConstraint(
            "severity IN ('warning','critical')",
            name="ck_reflection_capacity_governance_incidents_severity",
        ),
        CheckConstraint(
            "status IN ('open','acknowledged','resolved')",
            name="ck_reflection_capacity_governance_incidents_status",
        ),
        CheckConstraint(
            "version >= 1 AND occurrence_count >= 1 AND reopened_count >= 0",
            name="ck_reflection_capacity_governance_incidents_counts",
        ),
        CheckConstraint(
            "(status = 'open' AND acknowledged_at IS NULL AND resolved_at IS NULL) "
            "OR (status = 'acknowledged' AND acknowledged_by IS NOT NULL "
            "AND acknowledged_principal_id IS NOT NULL "
            "AND acknowledged_token_id IS NOT NULL "
            "AND acknowledged_at IS NOT NULL AND resolved_at IS NULL) "
            "OR (status = 'resolved' AND resolved_at IS NOT NULL)",
            name="ck_reflection_capacity_governance_incidents_lifecycle",
        ),
        CheckConstraint(
            "octet_length(fingerprint) = 64 "
            "AND octet_length(evidence_fingerprint) = 64",
            name="ck_reflection_capacity_governance_incidents_fingerprints",
        ),
        UniqueConstraint(
            "fingerprint",
            name="uq_reflection_capacity_governance_incidents_fingerprint",
        ),
        Index(
            "ix_reflection_capacity_governance_incidents_tenant_status",
            "tenant_id",
            "handler_version",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_reflection_capacity_governance_incidents_source",
            "tenant_id",
            "handler_version",
            "signal",
            "source_id",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    signal: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_evidence_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reopened_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    acknowledged_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acknowledged_principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    acknowledged_token_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ReflectionCapacityGovernanceRemediationModel(TimestampMixin, Base):
    __tablename__ = "reflection_capacity_governance_remediations"
    __table_args__ = (
        CheckConstraint(
            "playbook IN ('audit_failure_containment','alert_sla_recovery',"
            "'alert_reopen_stabilization','drill_control_repair',"
            "'knowledge_safety_containment','knowledge_quality_review',"
            "'knowledge_recurrence_review')",
            name="ck_capacity_remediations_playbook",
        ),
        CheckConstraint(
            "status IN ('awaiting_approval','approved','verification_pending',"
            "'verified','rejected','failed')",
            name="ck_capacity_remediations_status",
        ),
        CheckConstraint(
            "version >= 1 AND incident_cycle >= 0",
            name="ck_capacity_remediations_versions",
        ),
        CheckConstraint(
            "execution_result IS NULL OR execution_result IN ('completed','failed')",
            name="ck_capacity_remediations_result",
        ),
        CheckConstraint(
            "execution_evidence IS NULL OR execution_evidence IN "
            "('containment_applied','configuration_reviewed','monitoring_extended',"
            "'schema_control_restored','knowledge_quarantine_reviewed',"
            "'quality_evidence_reviewed','restoration_history_reviewed')",
            name="ck_capacity_remediations_evidence",
        ),
        CheckConstraint(
            "(status = 'awaiting_approval' AND approved_at IS NULL "
            "AND rejected_at IS NULL AND executed_at IS NULL AND verified_at IS NULL) "
            "OR (status = 'approved' AND approved_by IS NOT NULL "
            "AND approved_at IS NOT NULL AND executed_at IS NULL "
            "AND rejected_at IS NULL AND verified_at IS NULL) "
            "OR (status = 'rejected' AND rejected_by IS NOT NULL "
            "AND rejected_at IS NOT NULL AND approved_at IS NULL "
            "AND executed_at IS NULL AND verified_at IS NULL) "
            "OR (status IN ('verification_pending','failed') "
            "AND approved_at IS NOT NULL AND executed_by IS NOT NULL "
            "AND executed_at IS NOT NULL AND execution_result IS NOT NULL "
            "AND execution_evidence IS NOT NULL AND incident_version_at_execution IS NOT NULL "
            "AND verified_at IS NULL) "
            "OR (status = 'verified' AND approved_at IS NOT NULL "
            "AND executed_at IS NOT NULL AND execution_result = 'completed' "
            "AND verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_capacity_remediations_lifecycle",
        ),
        UniqueConstraint(
            "incident_id",
            "incident_cycle",
            name="uq_capacity_remediations_incident_cycle",
        ),
        Index(
            "ix_capacity_remediations_tenant_status",
            "tenant_id",
            "handler_version",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_capacity_remediations_incident",
            "tenant_id",
            "handler_version",
            "incident_id",
            "incident_cycle",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_incidents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    incident_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    playbook: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    requested_by: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_token_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_principal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    approved_token_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rejected_principal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    rejected_token_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    executed_principal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    executed_token_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_evidence: Mapped[str | None] = mapped_column(String(64), nullable=True)
    incident_version_at_execution: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verified_principal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    verified_token_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReflectionCapacityGovernancePostmortemModel(TimestampMixin, Base):
    __tablename__ = "reflection_capacity_governance_postmortems"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_review','published','quarantined','rejected')",
            name="ck_capacity_postmortems_status",
        ),
        CheckConstraint(
            "version >= 1 AND incident_cycle >= 0 "
            "AND incident_version >= 1 AND remediation_version >= 1",
            name="ck_capacity_postmortems_versions",
        ),
        CheckConstraint(
            "root_cause IN ('authorization_control_gap','policy_drift',"
            "'operational_process_gap','observability_gap','schema_control_gap')",
            name="ck_capacity_postmortems_root_cause",
        ),
        CheckConstraint(
            "impact IN ('governance_delay','control_degradation','repeated_alerting',"
            "'access_disruption','no_external_impact')",
            name="ck_capacity_postmortems_impact",
        ),
        CheckConstraint(
            "prevention IN ('access_review','policy_validation','process_hardening',"
            "'monitoring_expansion','schema_verification')",
            name="ck_capacity_postmortems_prevention",
        ),
        CheckConstraint(
            "char_length(summary) BETWEEN 10 AND 1000 "
            "AND octet_length(content_fingerprint) = 64",
            name="ck_capacity_postmortems_content",
        ),
        CheckConstraint(
            "embedding_dimensions IS NULL "
            f"OR embedding_dimensions = {KNOWLEDGE_EMBEDDING_DIMENSIONS}",
            name="ck_capacity_postmortems_embedding_dimensions",
        ),
        CheckConstraint(
            "knowledge_namespace IS NULL "
            "OR knowledge_namespace = 'operations.governance.postmortems'",
            name="ck_capacity_postmortems_namespace",
        ),
        CheckConstraint(
            "restore_count >= 0 AND "
            "((restore_count = 0 AND last_restored_at IS NULL) OR "
            "(restore_count >= 1 AND last_restored_at IS NOT NULL "
            "AND last_quarantined_at IS NOT NULL "
            "AND quarantine_feedback_id IS NOT NULL))",
            name="ck_capacity_postmortems_restore_history",
        ),
        CheckConstraint(
            "(status = 'quarantined' AND last_quarantined_at IS NOT NULL "
            "AND quarantine_feedback_id IS NOT NULL) OR "
            "(status IN ('awaiting_review','rejected') "
            "AND last_quarantined_at IS NULL AND quarantine_feedback_id IS NULL "
            "AND restore_count = 0 AND last_restored_at IS NULL) OR "
            "status = 'published'",
            name="ck_capacity_postmortems_quarantine_history",
        ),
        CheckConstraint(
            "(status = 'awaiting_review' AND reviewed_at IS NULL "
            "AND knowledge_namespace IS NULL AND knowledge_source_key IS NULL "
            "AND knowledge_version IS NULL AND published_content IS NULL "
            "AND lexical_text IS NULL AND lexical_profile IS NULL "
            "AND embedding_profile IS NULL AND embedding_dimensions IS NULL "
            "AND embedding IS NULL AND published_at IS NULL) "
            "OR (status = 'rejected' AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND knowledge_namespace IS NULL "
            "AND knowledge_source_key IS NULL AND knowledge_version IS NULL "
            "AND published_content IS NULL AND lexical_text IS NULL "
            "AND lexical_profile IS NULL AND embedding_profile IS NULL "
            "AND embedding_dimensions IS NULL AND embedding IS NULL "
            "AND published_at IS NULL) "
            "OR (status IN ('published','quarantined') AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND knowledge_namespace IS NOT NULL "
            "AND knowledge_source_key IS NOT NULL AND knowledge_version IS NOT NULL "
            "AND published_content IS NOT NULL AND lexical_text IS NOT NULL "
            "AND lexical_profile IS NOT NULL AND embedding_profile IS NOT NULL "
            "AND embedding_dimensions IS NOT NULL AND embedding IS NOT NULL "
            "AND published_at IS NOT NULL)",
            name="ck_capacity_postmortems_lifecycle",
        ),
        UniqueConstraint(
            "remediation_id",
            name="uq_capacity_postmortems_remediation",
        ),
        UniqueConstraint(
            "tenant_id",
            "content_fingerprint",
            name="uq_capacity_postmortems_tenant_fingerprint",
        ),
        Index(
            "ix_capacity_postmortems_tenant_status",
            "tenant_id",
            "handler_version",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_capacity_postmortems_source",
            "tenant_id",
            "handler_version",
            "incident_id",
            "remediation_id",
        ),
        Index(
            "ix_capacity_postmortems_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_capacity_postmortems_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_incidents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    remediation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_remediations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    incident_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    incident_version: Mapped[int] = mapped_column(Integer, nullable=False)
    remediation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    root_cause: Mapped[str] = mapped_column(String(64), nullable=False)
    impact: Mapped[str] = mapped_column(String(64), nullable=False)
    prevention: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_token_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_principal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_token_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    knowledge_namespace: Mapped[str | None] = mapped_column(String(150), nullable=True)
    knowledge_source_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    knowledge_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    published_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    lexical_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lexical_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(lexical_text, ''))", persisted=True),
        nullable=False,
    )
    embedding_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(KNOWLEDGE_EMBEDDING_DIMENSIONS),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    quarantine_feedback_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    restore_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_restored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ReflectionCapacityGovernanceKnowledgeFeedbackModel(TimestampMixin, Base):
    __tablename__ = "reflection_capacity_governance_knowledge_feedback"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_review','confirmed','dismissed','superseded')",
            name="ck_capacity_knowledge_feedback_status",
        ),
        CheckConstraint(
            "signal IN ('helpful','not_helpful','safety_concern')",
            name="ck_capacity_knowledge_feedback_signal",
        ),
        CheckConstraint(
            "reason IN ('relevance','accuracy','staleness','unsafe_content')",
            name="ck_capacity_knowledge_feedback_reason",
        ),
        CheckConstraint(
            "(signal = 'safety_concern') = (reason = 'unsafe_content')",
            name="ck_capacity_knowledge_feedback_safety_pair",
        ),
        CheckConstraint(
            "version >= 1 AND postmortem_version >= 1 "
            "AND octet_length(content_fingerprint) = 64",
            name="ck_capacity_knowledge_feedback_versions",
        ),
        CheckConstraint(
            "(status IN ('awaiting_review','superseded') AND reviewed_by IS NULL "
            "AND reviewed_principal_id IS NULL AND reviewed_token_id IS NULL "
            "AND reviewed_at IS NULL) OR "
            "(status IN ('confirmed','dismissed') AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL)",
            name="ck_capacity_knowledge_feedback_lifecycle",
        ),
        UniqueConstraint(
            "postmortem_id",
            "reported_principal_id",
            "postmortem_version",
            name="uq_capacity_knowledge_feedback_reporter_version",
        ),
        Index(
            "ix_capacity_knowledge_feedback_tenant_status",
            "tenant_id",
            "handler_version",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_capacity_knowledge_feedback_postmortem",
            "tenant_id",
            "handler_version",
            "postmortem_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    postmortem_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_postmortems.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    postmortem_version: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    signal: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reported_by: Mapped[str] = mapped_column(String(200), nullable=False)
    reported_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reported_token_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_principal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_token_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReflectionCapacityGovernanceKnowledgeQualitySnapshotModel(Base):
    __tablename__ = "reflection_capacity_governance_knowledge_quality_snapshots"
    __table_args__ = (
        CheckConstraint(
            "assessment IN ('insufficient','healthy','degraded','unsafe')",
            name="ck_capacity_knowledge_quality_assessment",
        ),
        CheckConstraint(
            "postmortem_version >= 1 "
            "AND octet_length(content_fingerprint) = 64 "
            "AND octet_length(evidence_fingerprint) = 64",
            name="ck_capacity_knowledge_quality_versions",
        ),
        CheckConstraint(
            "total_feedback >= 0 AND awaiting_review_count >= 0 "
            "AND confirmed_helpful_count >= 0 AND confirmed_not_helpful_count >= 0 "
            "AND confirmed_safety_count >= 0 AND dismissed_count >= 0 "
            "AND superseded_count >= 0 AND total_feedback = "
            "awaiting_review_count + confirmed_helpful_count + "
            "confirmed_not_helpful_count + confirmed_safety_count + "
            "dismissed_count + superseded_count",
            name="ck_capacity_knowledge_quality_counts",
        ),
        UniqueConstraint(
            "postmortem_id",
            "postmortem_version",
            "evidence_fingerprint",
            name="uq_capacity_knowledge_quality_evidence",
        ),
        Index(
            "ix_capacity_knowledge_quality_tenant_assessment",
            "tenant_id",
            "handler_version",
            "assessment",
            "captured_at",
            "id",
        ),
        Index(
            "ix_capacity_knowledge_quality_tenant_captured",
            "tenant_id",
            "handler_version",
            "captured_at",
            "id",
        ),
        Index(
            "ix_capacity_knowledge_quality_postmortem",
            "tenant_id",
            "handler_version",
            "postmortem_id",
            "captured_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    postmortem_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_postmortems.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    postmortem_version: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    total_feedback: Mapped[int] = mapped_column(Integer, nullable=False)
    awaiting_review_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_helpful_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_not_helpful_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_safety_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dismissed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    superseded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_by: Mapped[str] = mapped_column(String(200), nullable=False)
    captured_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    captured_token_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReflectionCapacityGovernanceKnowledgeRecoveryModel(TimestampMixin, Base):
    __tablename__ = "reflection_capacity_governance_knowledge_recoveries"
    __table_args__ = (
        CheckConstraint(
            "reason = 'false_positive'",
            name="ck_capacity_knowledge_recoveries_reason",
        ),
        CheckConstraint(
            "status IN ('awaiting_review','approved','rejected')",
            name="ck_capacity_knowledge_recoveries_status",
        ),
        CheckConstraint(
            "version >= 1 AND postmortem_version >= 1 "
            "AND octet_length(content_fingerprint) = 64",
            name="ck_capacity_knowledge_recoveries_versions",
        ),
        CheckConstraint(
            "(status = 'awaiting_review' AND reviewed_by IS NULL "
            "AND reviewed_principal_id IS NULL AND reviewed_token_id IS NULL "
            "AND reviewed_at IS NULL AND restored_knowledge_version IS NULL) OR "
            "(status = 'rejected' AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND restored_knowledge_version IS NULL) OR "
            "(status = 'approved' AND reviewed_by IS NOT NULL "
            "AND reviewed_principal_id IS NOT NULL AND reviewed_token_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND restored_knowledge_version IS NOT NULL)",
            name="ck_capacity_knowledge_recoveries_lifecycle",
        ),
        Index(
            "uq_capacity_knowledge_recoveries_active",
            "postmortem_id",
            "postmortem_version",
            unique=True,
            postgresql_where=text("status = 'awaiting_review'"),
        ),
        Index(
            "ix_capacity_knowledge_recoveries_tenant_status",
            "tenant_id",
            "handler_version",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_capacity_knowledge_recoveries_postmortem",
            "tenant_id",
            "handler_version",
            "postmortem_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    postmortem_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_postmortems.id", ondelete="RESTRICT"),
        nullable=False,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "reflection_capacity_governance_knowledge_quality_snapshots.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    quarantine_feedback_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "reflection_capacity_governance_knowledge_feedback.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    postmortem_version: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    requested_by: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_token_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_principal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_token_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_knowledge_version: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )


class ReflectionCapacityGovernanceAuditEventModel(Base):
    __tablename__ = "reflection_capacity_governance_audit_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success','denied','conflict')",
            name="ck_reflection_capacity_governance_audit_outcome",
        ),
        Index(
            "ix_reflection_capacity_governance_audit_tenant_created",
            "tenant_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_reflection_capacity_governance_audit_actor_created",
            "actor_principal_id",
            "created_at",
        ),
        Index(
            "ix_reflection_capacity_governance_audit_filter_created",
            "tenant_id",
            "handler_version",
            "outcome",
            "action",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    actor_token_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_change_requests.id", ondelete="RESTRICT"),
        nullable=True,
    )
    alert_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_alerts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    incident_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_incidents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    postmortem_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reflection_capacity_governance_postmortems.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReflectionJobRetryRequestModel(Base):
    __tablename__ = "reflection_job_retry_requests"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success','conflict')",
            name="ck_reflection_job_retry_requests_outcome",
        ),
        CheckConstraint(
            "expected_version >= 1 AND result_version >= 1",
            name="ck_reflection_job_retry_requests_versions",
        ),
        ForeignKeyConstraint(
            ("job_id", "tenant_id"),
            ("outbox_jobs.id", "outbox_jobs.tenant_id"),
            ondelete="CASCADE",
            name="fk_reflection_job_retry_requests_job_scope",
        ),
        ForeignKeyConstraint(
            ("run_id", "tenant_id"),
            ("runs.id", "runs.tenant_id"),
            ondelete="CASCADE",
            name="fk_reflection_job_retry_requests_run_scope",
        ),
        ForeignKeyConstraint(
            ("agent_id", "tenant_id"),
            ("agents.id", "agents.tenant_id"),
            ondelete="CASCADE",
            name="fk_reflection_job_retry_requests_agent_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key_hash",
            name="uq_reflection_job_retry_requests_idempotency",
        ),
        Index(
            "ix_reflection_job_retry_requests_job_created",
            "job_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReflectionJobOperationAuditEventModel(Base):
    __tablename__ = "reflection_job_operation_audit_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success','denied','conflict')",
            name="ck_reflection_job_operation_audit_events_outcome",
        ),
        Index(
            "ix_reflection_job_operation_audit_tenant_created",
            "tenant_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_reflection_job_operation_audit_job_created",
            "job_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_token_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReflectionWorkerHeartbeatModel(Base):
    __tablename__ = "reflection_worker_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('idle','running','stopping','stopped')",
            name="ck_reflection_worker_heartbeats_status",
        ),
        CheckConstraint(
            "processed_jobs >= 0",
            name="ck_reflection_worker_heartbeats_processed_jobs",
        ),
        Index(
            "ix_reflection_worker_heartbeats_status_seen",
            "status",
            "last_seen_at",
        ),
        Index(
            "ix_reflection_worker_heartbeats_handler_seen",
            "job_type",
            "handler_version",
            "last_seen_at",
            "status",
        ),
    )

    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    instance_token: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    processed_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("outbox_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MemoryModel(TimestampMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memories_confidence"),
        CheckConstraint("importance >= 0 AND importance <= 1", name="ck_memories_importance"),
        CheckConstraint(
            "status IN ('candidate','active','superseded','expired','rejected')",
            name="ck_memories_status",
        ),
        Index(
            "ix_memories_scope_active",
            "tenant_id",
            "agent_id",
            "domain_id",
            "namespace",
            "status",
        ),
        Index(
            "ix_memories_management_scan",
            "tenant_id",
            "agent_id",
            "domain_id",
            "status",
            "created_at",
            "id",
        ),
        Index("ix_memories_candidate_id", "candidate_id"),
        UniqueConstraint("candidate_id", name="uq_memories_candidate_id"),
        CheckConstraint("recall_count >= 0", name="ck_memories_recall_count"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace: Mapped[str] = mapped_column(String(150), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    candidate_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "learning_candidates.id",
            name="fk_memories_candidate_id_learning_candidates",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recall_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_recalled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MemoryEmbeddingModel(Base):
    __tablename__ = "memory_embeddings"
    __table_args__ = (
        UniqueConstraint("memory_id", "embedding_profile", name="uq_memory_embedding_profile"),
        CheckConstraint("dimensions > 0", name="ck_memory_embeddings_dimensions"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    memory_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    embedding_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgeDocumentModel(TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','superseded','archived')",
            name="ck_knowledge_documents_status",
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "domain_id",
            "namespace",
            "source_key",
            "version",
            name="uq_knowledge_documents_scope_source_version",
        ),
        Index(
            "ix_knowledge_documents_scope_status",
            "tenant_id",
            "agent_id",
            "domain_id",
            "namespace",
            "status",
        ),
        Index(
            "ix_knowledge_documents_access_tags_gin",
            "access_tags",
            postgresql_using="gin",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace: Mapped[str] = mapped_column(String(150), nullable=False)
    source_key: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    access_tags: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )


class KnowledgeIngestionJobModel(TimestampMixin, Base):
    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','canceled')",
            name="ck_knowledge_ingestion_jobs_status",
        ),
        CheckConstraint(
            "stage IN ('parsing','embedding','publishing','completed')",
            name="ck_knowledge_ingestion_jobs_stage",
        ),
        CheckConstraint(
            "processed_chunks >= 0 AND total_chunks >= 0 "
            "AND processed_chunks <= total_chunks",
            name="ck_knowledge_ingestion_jobs_progress",
        ),
        CheckConstraint("attempts >= 0", name="ck_knowledge_ingestion_jobs_attempts"),
        CheckConstraint(
            "source_bytes IS NULL OR "
            "(octet_length(source_bytes) > 0 AND octet_length(source_bytes) <= 8388608)",
            name="ck_knowledge_ingestion_jobs_source_size",
        ),
        CheckConstraint(
            "source_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_ingestion_jobs_hashes",
        ),
        CheckConstraint(
            "(status = 'running' AND step_token IS NOT NULL "
            "AND step_lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND step_token IS NULL "
            "AND step_lease_expires_at IS NULL)",
            name="ck_knowledge_ingestion_jobs_lease",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND stage = 'completed' "
            "AND document_id IS NOT NULL AND source_bytes IS NULL) OR "
            "(status <> 'succeeded' AND stage <> 'completed' "
            "AND document_id IS NULL)",
            name="ck_knowledge_ingestion_jobs_terminal",
        ),
        CheckConstraint(
            "parsed_text IS NULL OR char_length(parsed_text) <= 2000000",
            name="ck_knowledge_ingestion_jobs_parsed_text_size",
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "idempotency_key",
            name="uq_knowledge_ingestion_jobs_scope_idempotency",
        ),
        Index(
            "ix_knowledge_ingestion_jobs_scope_status_created",
            "tenant_id",
            "agent_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_knowledge_ingestion_jobs_status_lease",
            "status",
            "step_lease_expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace: Mapped[str] = mapped_column(String(150), nullable=False)
    source_key: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    source_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="parsing")
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    access_tags: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    processed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    step_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class KnowledgeIngestionChunkModel(Base):
    __tablename__ = "knowledge_ingestion_chunks"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "chunk_index",
            name="uq_knowledge_ingestion_chunks_job_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_knowledge_ingestion_chunks_index"),
        CheckConstraint(
            "start_char >= 0 AND end_char > start_char",
            name="ck_knowledge_ingestion_chunks_char_range",
        ),
        CheckConstraint(
            "char_length(content) > 0",
            name="ck_knowledge_ingestion_chunks_content",
        ),
        Index("ix_knowledge_ingestion_chunks_job", "job_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(KNOWLEDGE_EMBEDDING_DIMENSIONS),
        nullable=True,
    )


class KnowledgeChunkModel(TimestampMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunks_document_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index"),
        CheckConstraint("start_char >= 0", name="ck_knowledge_chunks_start_char"),
        CheckConstraint("end_char > start_char", name="ck_knowledge_chunks_char_range"),
        CheckConstraint(
            f"embedding_dimensions = {KNOWLEDGE_EMBEDDING_DIMENSIONS}",
            name="ck_knowledge_chunks_embedding_dimensions",
        ),
        Index(
            "ix_knowledge_chunks_scope",
            "tenant_id",
            "agent_id",
            "domain_id",
            "namespace",
        ),
        Index(
            "ix_knowledge_chunks_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_knowledge_chunks_lexical_profile",
            "tenant_id",
            "agent_id",
            "lexical_profile",
        ),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace: Mapped[str] = mapped_column(String(150), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    lexical_text: Mapped[str] = mapped_column(Text, nullable=False)
    lexical_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('pg_catalog.simple', coalesce(lexical_text, ''))",
            persisted=True,
        ),
        nullable=False,
    )
    embedding_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(KNOWLEDGE_EMBEDDING_DIMENSIONS),
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )


class RAGEvaluationRunModel(TimestampMixin, Base):
    __tablename__ = "rag_evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('passed','failed')",
            name="ck_rag_evaluation_runs_status",
        ),
        CheckConstraint("top_k >= 1 AND top_k <= 20", name="ck_rag_evaluation_runs_top_k"),
        CheckConstraint("duration_ms >= 0", name="ck_rag_evaluation_runs_duration"),
        CheckConstraint(
            "embedding_dimensions > 0",
            name="ck_rag_evaluation_runs_embedding_dimensions",
        ),
        Index(
            "ix_rag_evaluation_runs_scope_dataset_created",
            "tenant_id",
            "agent_id",
            "dataset_hash",
            "created_at",
        ),
        Index(
            "ix_rag_evaluation_runs_profile_status_created",
            "embedding_profile",
            "embedding_dimensions",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace: Mapped[str] = mapped_column(String(150), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    retriever_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    regression_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    baseline_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    gate: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RAGEvaluationCaseResultModel(TimestampMixin, Base):
    __tablename__ = "rag_evaluation_case_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "case_id",
            name="uq_rag_evaluation_case_results_run_case",
        ),
        CheckConstraint(
            "latency_ms >= 0",
            name="ck_rag_evaluation_case_results_latency",
        ),
        CheckConstraint(
            "difficulty IN ('easy','medium','hard')",
            name="ck_rag_evaluation_case_results_difficulty",
        ),
        Index("ix_rag_evaluation_case_results_run_passed", "run_id", "passed"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rag_evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(String(150), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    expected_source_keys: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)),
        nullable=False,
    )
    retrieved_source_keys: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)),
        nullable=False,
    )
    retrieved_hits: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    retrieval_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    citation_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(300)), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
    )


class LearningCandidateModel(TimestampMixin, Base):
    __tablename__ = "learning_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'pending','evaluating','awaiting_approval','approved','active','deprecated',"
            "'expired','rolled_back','rejected'"
            ")",
            name="ck_learning_candidates_status",
        ),
        Index("ix_learning_candidates_scope_status", "tenant_id", "agent_id", "status"),
        Index(
            "ix_learning_candidates_governance_scan",
            "tenant_id",
            "agent_id",
            "domain_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_learning_candidates_scope_fingerprint_status",
            "tenant_id",
            "agent_id",
            "domain_id",
            "fingerprint",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_change: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_run_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    protected_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class CandidateLineageModel(Base):
    __tablename__ = "candidate_lineages"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('merge','compression')",
            name="ck_candidate_lineages_relation_type",
        ),
        CheckConstraint("source_version > 0", name="ck_candidate_lineages_source_version"),
        Index(
            "ix_candidate_lineages_source_child",
            "source_candidate_id",
            "child_candidate_id",
        ),
    )

    child_candidate_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("learning_candidates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_candidate_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("learning_candidates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class CandidateGovernanceActionModel(Base):
    __tablename__ = "candidate_governance_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('expire','evict','compress')",
            name="ck_candidate_governance_actions_action",
        ),
        CheckConstraint(
            "value_score >= 0 AND value_score <= 1",
            name="ck_candidate_governance_actions_value_score",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_candidate_governance_actions_idempotency",
        ),
        Index(
            "ix_candidate_governance_actions_scope_created",
            "tenant_id",
            "agent_id",
            "created_at",
        ),
        Index(
            "ix_candidate_governance_actions_candidate_created",
            "candidate_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("learning_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(500), nullable=False)
    value_score: Mapped[float] = mapped_column(Float, nullable=False)
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    target_status: Mapped[str] = mapped_column(String(32), nullable=False)
    replacement_candidate_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("learning_candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class EvaluationModel(TimestampMixin, Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="ck_evaluations_score"),
        Index("ix_evaluations_candidate_created", "candidate_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("learning_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class ApprovalModel(TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired','canceled')",
            name="ck_approvals_status",
        ),
        CheckConstraint(
            "(run_id IS NOT NULL) <> (candidate_id IS NOT NULL)",
            name="ck_approvals_single_subject",
        ),
        Index("ix_approvals_tenant_status", "tenant_id", "status"),
        Index("ix_approvals_run_created", "run_id", "created_at"),
        Index("ix_approvals_candidate_created", "candidate_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    candidate_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("learning_candidates.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
