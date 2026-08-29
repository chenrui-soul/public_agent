from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ToolRisk(StrEnum):
    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    HIGH_RISK_WRITE = "high_risk_write"
    IRREVERSIBLE = "irreversible"


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    provider_state: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str = "1.0.0"
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    risk: ToolRisk = ToolRisk.READ
    timeout_seconds: float = Field(default=30.0, gt=0)
    idempotent: bool = True
    requires_approval: bool = False


class ModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    model_name: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    provider_state: dict[str, Any] = Field(default_factory=dict)


class AgentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    version: str
    instructions: str
    memory_namespace: str
    knowledge_namespace: str | None = None
    knowledge_top_k: int = Field(default=5, ge=1, le=20)
    allowed_tools: tuple[str, ...] = ()
    max_steps: int = Field(default=12, ge=1, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    session_id: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    tool_call: ToolCall
    tool_version: str
    tool_definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str
    created_at: datetime = Field(default_factory=utc_now)


class RunCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    step: int
    messages: tuple[Message, ...]
    pending_approval: ApprovalRequest
    remaining_tool_calls: tuple[ToolCall, ...] = Field(min_length=1)
    required_citation_ids: tuple[str, ...] = ()
    agent_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    status: RunStatus
    output: str | None = None
    error: str | None = None
    steps: int = 0
    checkpoint: RunCheckpoint | None = None
