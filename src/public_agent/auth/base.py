from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

_PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,49}:[a-z][a-z0-9_.-]{0,49}$")


class PrincipalStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class AuthenticatedPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal_id: UUID | None = None
    token_id: UUID | None = None
    subject: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=100)
    allowed_agent_ids: frozenset[str] = frozenset()
    all_agents: bool = False
    permissions: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def validate_agent_scope(self) -> AuthenticatedPrincipal:
        if not self.all_agents and not self.allowed_agent_ids:
            raise ValueError("principal must allow explicit agents or all_agents")
        if self.all_agents and self.allowed_agent_ids:
            raise ValueError("all_agents cannot be combined with explicit agents")
        return self

    def can_access_agent(self, agent_id: str) -> bool:
        return self.all_agents or agent_id in self.allowed_agent_ids


class PrincipalCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    permissions: tuple[str, ...] = Field(min_length=1, max_length=100)
    agent_ids: tuple[str, ...] = Field(default=(), max_length=500)
    all_agents: bool = False

    @field_validator("tenant_id", "subject", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("permissions")
    @classmethod
    def normalize_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip().lower() for item in value if item.strip()}))
        if not normalized or len(normalized) > 100:
            raise ValueError("permissions must contain between 1 and 100 values")
        if any(_PERMISSION_PATTERN.fullmatch(item) is None for item in normalized):
            raise ValueError("permissions must use resource:action names")
        return normalized

    @field_validator("agent_ids")
    @classmethod
    def normalize_agent_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if len(normalized) > 500 or any(len(item) > 100 for item in normalized):
            raise ValueError("agent_ids exceed the configured limits")
        return normalized

    @model_validator(mode="after")
    def validate_agent_scope(self) -> PrincipalCreateRequest:
        if self.all_agents == bool(self.agent_ids):
            raise ValueError("choose either all_agents or explicit agent_ids")
        return self


class APIPrincipalRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str
    subject: str
    display_name: str
    status: PrincipalStatus
    permissions: tuple[str, ...]
    agent_ids: tuple[str, ...]
    all_agents: bool
    created_at: datetime
    updated_at: datetime


class IssuedAPIToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    principal_id: UUID
    label: str
    token: SecretStr
    prefix: str
    expires_at: datetime | None = None
    created_at: datetime


class AuthenticationError(RuntimeError):
    """Safe authentication failure that never reveals token state or material."""
