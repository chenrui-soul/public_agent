from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from public_agent.auth.base import APIPrincipalRecord, PrincipalStatus
from public_agent.operations import OPERATIONS_JOB_PERMISSIONS
from public_agent.operations.capacity_control import CAPACITY_GOVERNANCE_PERMISSIONS

AUTH_PRINCIPALS_READ = "auth.principals:read"
AUTH_PRINCIPALS_WRITE = "auth.principals:write"
AUTH_TOKENS_READ = "auth.tokens:read"
AUTH_TOKENS_ISSUE = "auth.tokens:issue"
AUTH_TOKENS_REVOKE = "auth.tokens:revoke"
AUTH_AUDIT_READ = "auth.audit:read"

AUTH_MANAGEMENT_PERMISSIONS = frozenset(
    {
        AUTH_PRINCIPALS_READ,
        AUTH_PRINCIPALS_WRITE,
        AUTH_TOKENS_READ,
        AUTH_TOKENS_ISSUE,
        AUTH_TOKENS_REVOKE,
        AUTH_AUDIT_READ,
    }
)
CRITICAL_AUTH_PERMISSIONS = frozenset(
    {
        AUTH_PRINCIPALS_WRITE,
        AUTH_TOKENS_ISSUE,
        AUTH_TOKENS_REVOKE,
    }
)
DEFAULT_MANAGEABLE_PERMISSIONS = frozenset(
    {
        *AUTH_MANAGEMENT_PERMISSIONS,
        "approvals:decide",
        "candidates:evaluate",
        "candidates:promote",
        "candidates:read",
        "knowledge:read",
        "knowledge:write",
        "memories:read",
        *OPERATIONS_JOB_PERMISSIONS,
        *CAPACITY_GOVERNANCE_PERMISSIONS,
        "runs:read",
        "runs:write",
    }
)


class AuthenticationAuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    CONFLICT = "conflict"


class ManagedPrincipalCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    permissions: tuple[str, ...] = Field(min_length=1, max_length=100)
    agent_ids: tuple[str, ...] = Field(default=(), max_length=500)
    all_agents: bool = False

    @field_validator("subject", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("permissions")
    @classmethod
    def normalize_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        from public_agent.auth.base import PrincipalCreateRequest

        normalized = PrincipalCreateRequest.normalize_permissions(value)
        return normalized

    @field_validator("agent_ids")
    @classmethod
    def normalize_agent_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        from public_agent.auth.base import PrincipalCreateRequest

        return PrincipalCreateRequest.normalize_agent_ids(value)

    @model_validator(mode="after")
    def validate_agent_scope(self) -> ManagedPrincipalCreateRequest:
        if self.all_agents == bool(self.agent_ids):
            raise ValueError("choose either all_agents or explicit agent_ids")
        return self


class PrincipalManagementQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    status: PrincipalStatus | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class PrincipalManagementPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[APIPrincipalRecord, ...]
    next_cursor: str | None = None


class APITokenSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    principal_id: UUID
    label: str
    prefix: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class TokenManagementQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    principal_id: UUID
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class TokenManagementPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[APITokenSummary, ...]
    next_cursor: str | None = None


class AuthenticationAuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str | None
    actor_principal_id: UUID | None
    actor_token_id: UUID | None
    action: str
    target_principal_id: UUID | None
    target_token_id: UUID | None
    outcome: AuthenticationAuditOutcome
    metadata: dict[str, str | int | bool | None]
    created_at: datetime


class AuthenticationAuditQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    action: str | None = Field(default=None, min_length=1, max_length=100)
    outcome: AuthenticationAuditOutcome | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)


class AuthenticationAuditPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[AuthenticationAuditRecord, ...]
    next_cursor: str | None = None


class AuthManagementAuthorizationError(PermissionError):
    """The authenticated actor cannot perform the requested management action."""


class AuthStateConflictError(RuntimeError):
    """The requested mutation would violate an authentication safety invariant."""


class AuthCursorError(ValueError):
    """A management keyset cursor is malformed or unsupported."""
