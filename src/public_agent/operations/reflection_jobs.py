from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

OPERATIONS_JOBS_READ = "operations.jobs:read"
OPERATIONS_JOBS_RETRY = "operations.jobs:retry"
OPERATIONS_JOB_PERMISSIONS = frozenset(
    {
        OPERATIONS_JOBS_READ,
        OPERATIONS_JOBS_RETRY,
    }
)


class ManagedReflectionJobState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"


class OperationAuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    CONFLICT = "conflict"


class ReflectionJobQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    handler_version: str = Field(default="reflection-v1", min_length=1, max_length=64)
    agent_id: str | None = Field(default=None, min_length=1, max_length=100)
    status: ManagedReflectionJobState | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)

    @field_validator("tenant_id", "handler_version", "agent_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("text fields must not be blank")
        return normalized


class ReflectionJobStatsQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=100)
    handler_version: str = Field(default="reflection-v1", min_length=1, max_length=64)
    agent_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("tenant_id", "handler_version", "agent_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return ReflectionJobQuery.strip_text(value)


class ReflectionJobRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    agent_id: str
    handler_version: str
    status: ManagedReflectionJobState
    version: int
    attempts: int
    attempts_in_cycle: int
    max_attempts: int
    available_at: datetime
    lease_expires_at: datetime | None
    last_error_code: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReflectionJobPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ReflectionJobRecord, ...]
    next_cursor: str | None = None


class ReflectionJobStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    handler_version: str
    agent_id: str | None
    pending: int
    processing: int
    retry_wait: int
    succeeded: int
    dead_letter: int
    oldest_available_at: datetime | None
    oldest_available_age_seconds: int | None


class RetryReflectionJobRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_version: int = Field(ge=1)


class ReflectionJobRetryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    run_id: UUID
    agent_id: str
    previous_status: ManagedReflectionJobState
    status: ManagedReflectionJobState
    version: int
    idempotent_replay: bool = False


class ReflectionJobAuthorizationError(PermissionError):
    """The current actor cannot access the requested operations scope."""


class ReflectionJobCursorError(ValueError):
    """A reflection job keyset cursor is malformed or bound to another filter."""


class ReflectionJobConflictError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
