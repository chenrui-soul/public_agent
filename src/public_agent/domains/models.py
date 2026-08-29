from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from public_agent.core.types import AgentSpec, utc_now

SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class DomainAssetType(StrEnum):
    INSTRUCTIONS = "instructions"
    SKILL = "skill"
    POLICY = "policy"
    WORKFLOW = "workflow"
    EVALUATION = "evaluation"


class DomainPackageStatus(StrEnum):
    DRAFT = "draft"
    EVALUATING = "evaluating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


class DomainAssetDeclaration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_type: DomainAssetType
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    path: str = Field(min_length=1, max_length=500)
    media_type: str = Field(default="text/plain", min_length=1, max_length=100)

    @model_validator(mode="after")
    def reject_reserved_asset_type(self) -> DomainAssetDeclaration:
        if self.asset_type is DomainAssetType.INSTRUCTIONS:
            raise ValueError("instructions assets are created from the package instructions")
        return self


class DomainPolicies(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    require_citations: bool = False
    high_risk_requires_human_review: bool = True
    prohibited_actions: tuple[str, ...] = ()

    @field_validator("prohibited_actions")
    @classmethod
    def normalize_prohibited_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if any(len(item) > 200 for item in normalized):
            raise ValueError("prohibited actions must be at most 200 characters")
        return normalized


class DomainPackage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    name: str = Field(min_length=1, max_length=200)
    version: str
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(min_length=1, max_length=262_144)
    memory_namespace: str = Field(min_length=1, max_length=150)
    knowledge_namespace: str | None = Field(default=None, max_length=150)
    knowledge_top_k: int = Field(default=5, ge=1, le=20)
    allowed_tools: tuple[str, ...] = ()
    max_steps: int = Field(default=12, ge=1, le=100)
    policies: DomainPolicies = Field(default_factory=DomainPolicies)
    evaluation_suite: str | None = Field(default=None, max_length=200)
    assets: tuple[DomainAssetDeclaration, ...] = Field(default=(), max_length=256)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if SEMVER_PATTERN.fullmatch(value) is None:
            raise ValueError("version must use MAJOR.MINOR.PATCH semantic versioning")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def normalize_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if any(len(item) > 100 for item in normalized):
            raise ValueError("allowed tool names must be at most 100 characters")
        return normalized

    @model_validator(mode="after")
    def reject_duplicate_assets(self) -> DomainPackage:
        identities: set[tuple[DomainAssetType, str]] = set()
        paths: set[str] = set()
        for asset in self.assets:
            identity = (asset.asset_type, asset.key)
            normalized_path = asset.path.replace("\\", "/").casefold()
            if identity in identities:
                raise ValueError("domain asset type and key must be unique")
            if normalized_path in paths:
                raise ValueError("domain asset paths must be unique")
            identities.add(identity)
            paths.add(normalized_path)
        return self

    def to_agent_spec(self) -> AgentSpec:
        return AgentSpec(
            id=self.id,
            name=self.name,
            version=self.version,
            instructions=self.instructions,
            memory_namespace=self.memory_namespace,
            knowledge_namespace=self.knowledge_namespace,
            knowledge_top_k=self.knowledge_top_k,
            allowed_tools=self.allowed_tools,
            max_steps=self.max_steps,
            metadata={
                "description": self.description,
                "domain_id": self.id,
                "evaluation_suite": self.evaluation_suite,
                "policies": self.policies.model_dump(mode="json"),
                "assets": [asset.model_dump(mode="json") for asset in self.assets],
            },
        )


class PreparedDomainAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_type: DomainAssetType
    key: str
    relative_path: str
    media_type: str
    content: str
    content_hash: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)


class PreparedDomainPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package: DomainPackage
    content_hash: str = Field(pattern=SHA256_PATTERN)
    manifest: dict[str, Any]
    assets: tuple[PreparedDomainAsset, ...] = Field(min_length=2)
    total_size_bytes: int = Field(ge=0)


class DomainPackageEvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    suite: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=100)
    passed: bool
    score: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=10_000)
    metrics: dict[str, Any] = Field(default_factory=dict)
    report_hash: str = ""
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def ensure_report_hash(self) -> DomainPackageEvaluationResult:
        try:
            canonical = json.dumps(
                {
                    "suite": self.suite,
                    "dataset_version": self.dataset_version,
                    "passed": self.passed,
                    "score": self.score,
                    "summary": self.summary,
                    "metrics": self.metrics,
                },
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("evaluation metrics must contain finite JSON values") from exc
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.report_hash and self.report_hash != expected:
            raise ValueError("report_hash does not match the normalized evaluation report")
        object.__setattr__(self, "report_hash", expected)
        return self


class DomainPackageVersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str
    agent_id: str
    domain_id: str
    version: str
    content_hash: str = Field(pattern=SHA256_PATTERN)
    status: DomainPackageStatus
    revision: int = Field(ge=1)
    agent_version_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class DomainPackageReleaseRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    package_version_id: UUID
    action: str
    from_agent_version_id: UUID | None = None
    to_agent_version_id: UUID | None = None
    idempotency_key: str
    performed_by: str
    note: str | None = None
    created_at: datetime
