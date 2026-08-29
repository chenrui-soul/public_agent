from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from public_agent.core.types import utc_now


class CandidateType(StrEnum):
    MEMORY = "memory"
    STRATEGY = "strategy"
    SKILL = "skill"
    POLICY = "policy"


class CandidateRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    EVALUATING = "evaluating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    EXPIRED = "expired"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


class LearningCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: str
    agent_id: str
    domain_id: str
    candidate_type: CandidateType
    risk: CandidateRisk
    title: str
    fingerprint: str = ""
    proposed_change: dict[str, Any]
    evidence_run_ids: tuple[UUID, ...] = ()
    status: CandidateStatus = CandidateStatus.PENDING
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    protected_until: datetime | None = None

    @field_validator("created_at", "updated_at", "expires_at", "protected_until")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def ensure_fingerprint(self) -> LearningCandidate:
        fingerprint = self.fingerprint.strip().lower()
        if not fingerprint:
            legacy = self.proposed_change.get("fingerprint")
            fingerprint = str(legacy).strip().lower() if legacy else ""
        if not fingerprint:
            canonical_change = json.dumps(
                self.proposed_change,
                default=str,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            scoped = "|".join(
                (
                    self.tenant_id,
                    self.agent_id,
                    self.domain_id,
                    self.candidate_type.value,
                    canonical_change,
                )
            )
            fingerprint = hashlib.sha256(scoped.encode("utf-8")).hexdigest()
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise ValueError("Candidate fingerprint must be a 64-character SHA-256 hex digest")
        object.__setattr__(self, "fingerprint", fingerprint)
        return self


class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    score: float = Field(ge=0, le=1)
    summary: str
    metrics: dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
