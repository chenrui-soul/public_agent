from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from public_agent.api.base import APIError, APIPrincipal
from public_agent.application import ApprovalRecord, RunRecord
from public_agent.core.types import ApprovalDecision, RunContext, RunStatus

MAX_RUN_TASK_CHARACTERS = 100_000
MAX_RUN_METADATA_BYTES = 16_384
_RESERVED_METADATA_KEYS = frozenset({"authorized_knowledge_access_tags"})


class RunPrincipal(APIPrincipal):
    """Authenticated server-side identity used to authorize run management."""


RunPrincipalDependency = Callable[..., RunPrincipal | Awaitable[RunPrincipal]]


class RunManagementService(Protocol):
    async def create_run(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        task: str,
        context: RunContext,
        idempotency_key: str,
    ) -> RunRecord: ...

    async def get_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> RunRecord: ...

    async def cancel_run(
        self,
        *,
        run_id: UUID,
        tenant_id: str,
        agent_id: str,
        canceled_by: str,
        cancellation_note: str | None = None,
    ) -> RunRecord: ...

    async def get_approval(
        self,
        *,
        approval_id: UUID,
        tenant_id: str,
        agent_id: str,
    ) -> ApprovalRecord: ...

    async def decide_approval(
        self,
        *,
        approval_id: UUID,
        tenant_id: str,
        agent_id: str,
        decision: ApprovalDecision,
        decided_by: str,
        decision_note: str | None = None,
        lease_seconds: int = 300,
    ) -> RunRecord: ...


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1, max_length=100)
    task: str = Field(min_length=1, max_length=MAX_RUN_TASK_CHARACTERS)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @field_validator("agent_id", "task", "session_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("text fields must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_metadata(self) -> CreateRunRequest:
        if _RESERVED_METADATA_KEYS.intersection(self.metadata):
            raise ValueError("metadata contains server-reserved keys")
        try:
            encoded = json.dumps(
                self.metadata,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain finite JSON values") from exc
        if len(encoded) > MAX_RUN_METADATA_BYTES:
            raise ValueError("metadata exceeds the configured byte limit")
        return self


class CancelRunRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2_000)


class DecideApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1, max_length=100)
    decision: ApprovalDecision
    note: str | None = Field(default=None, max_length=2_000)
    lease_seconds: int = Field(default=300, ge=1, le=3_600)


class SafeRunError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    agent_id: str
    agent_version: str
    status: str
    reason: str
    tool_call_id: str
    tool_name: str
    tool_version: str
    decided_by: str | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ApprovalRecord) -> ApprovalResponse:
        return cls(
            id=record.id,
            run_id=record.run_id,
            agent_id=record.agent_id,
            agent_version=record.agent_version,
            status=record.status,
            reason="This tool call requires human approval.",
            tool_call_id=record.tool_call_id,
            tool_name=record.tool_name,
            tool_version=record.tool_version,
            decided_by=record.decided_by,
            decision_note=record.decision_note,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class RunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    agent_id: str
    agent_version: str
    status: RunStatus
    output: str | None
    error: SafeRunError | None
    steps: int = Field(ge=0)
    pending_approval: ApprovalResponse | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: RunRecord) -> RunResponse:
        return cls(
            id=record.id,
            agent_id=record.agent_id,
            agent_version=record.agent_version,
            status=record.status,
            output=record.output,
            error=_safe_run_error(record.status),
            steps=record.steps,
            pending_approval=(
                ApprovalResponse.from_record(record.pending_approval)
                if record.pending_approval is not None
                else None
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


def install_run_routes(
    app: FastAPI,
    *,
    service: RunManagementService,
    principal_dependency: RunPrincipalDependency,
) -> None:
    router = APIRouter(prefix="/v1", tags=["runs"])
    principal_depends = Depends(principal_dependency)

    @router.post("/runs", response_model=RunResponse)
    async def create_run(
        body: CreateRunRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
        current: RunPrincipal = principal_depends,
    ) -> RunResponse:
        _require_run(current, agent_id=body.agent_id, permission="runs:write")
        try:
            record = await service.create_run(
                tenant_id=current.tenant_id,
                agent_id=body.agent_id,
                task=body.task,
                context=RunContext(
                    tenant_id=current.tenant_id,
                    session_id=body.session_id,
                    user_id=current.subject,
                    metadata=body.metadata,
                ),
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="run") from None
        return RunResponse.from_record(record)

    @router.get("/runs/{run_id}", response_model=RunResponse)
    async def get_run(
        run_id: UUID,
        agent_id: Annotated[str, Query(min_length=1, max_length=100)],
        current: RunPrincipal = principal_depends,
    ) -> RunResponse:
        _require_run(current, agent_id=agent_id, permission="runs:read")
        try:
            record = await service.get_run(
                run_id=run_id,
                tenant_id=current.tenant_id,
                agent_id=agent_id,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="run") from None
        return RunResponse.from_record(record)

    @router.post("/runs/{run_id}/cancel", response_model=RunResponse)
    async def cancel_run(
        run_id: UUID,
        body: CancelRunRequest,
        current: RunPrincipal = principal_depends,
    ) -> RunResponse:
        _require_run(current, agent_id=body.agent_id, permission="runs:write")
        try:
            record = await service.cancel_run(
                run_id=run_id,
                tenant_id=current.tenant_id,
                agent_id=body.agent_id,
                canceled_by=current.subject,
                cancellation_note=body.note,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="run") from None
        return RunResponse.from_record(record)

    @router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
    async def get_approval(
        approval_id: UUID,
        agent_id: Annotated[str, Query(min_length=1, max_length=100)],
        current: RunPrincipal = principal_depends,
    ) -> ApprovalResponse:
        _require_run(current, agent_id=agent_id, permission="runs:read")
        try:
            record = await service.get_approval(
                approval_id=approval_id,
                tenant_id=current.tenant_id,
                agent_id=agent_id,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="approval") from None
        return ApprovalResponse.from_record(record)

    @router.post("/approvals/{approval_id}/decide", response_model=RunResponse)
    async def decide_approval(
        approval_id: UUID,
        body: DecideApprovalRequest,
        current: RunPrincipal = principal_depends,
    ) -> RunResponse:
        _require_run(current, agent_id=body.agent_id, permission="approvals:decide")
        try:
            record = await service.decide_approval(
                approval_id=approval_id,
                tenant_id=current.tenant_id,
                agent_id=body.agent_id,
                decision=body.decision,
                decided_by=current.subject,
                decision_note=body.note,
                lease_seconds=body.lease_seconds,
            )
        except Exception as exc:
            raise _mapped_error(exc, resource="approval") from None
        return RunResponse.from_record(record)

    app.include_router(router)


def _require_run(principal: APIPrincipal, *, agent_id: str, permission: str) -> None:
    principal.require(
        agent_id=agent_id,
        permission=permission,
        code="run_forbidden",
        message="The authenticated principal cannot access this run scope.",
    )


def _mapped_error(exc: Exception, *, resource: str) -> APIError:
    if isinstance(exc, APIError):
        return exc
    if isinstance(exc, KeyError):
        return APIError(
            status_code=404,
            code=f"{resource}_not_found",
            message=f"The requested {resource} was not found.",
        )
    message = str(exc).lower()
    if isinstance(exc, ValueError) and "idempotency" in message:
        return APIError(
            status_code=409,
            code="idempotency_conflict",
            message="The idempotency key is bound to a different run request.",
        )
    if isinstance(exc, (ValueError, RuntimeError)) and (
        "decision" in message
        or "resum" in message
        or "waiting" in message
        or "progress" in message
    ):
        return APIError(
            status_code=409,
            code="approval_state_conflict",
            message="The approval cannot be decided in its current state.",
        )
    if isinstance(exc, (ValueError, RuntimeError)):
        return APIError(
            status_code=409,
            code="agent_not_runnable",
            message="The active agent definition cannot run this request.",
        )
    return APIError(
        status_code=500,
        code="run_internal_error",
        message="The run operation could not be completed.",
    )


def _safe_run_error(status: RunStatus) -> SafeRunError | None:
    if status is RunStatus.FAILED:
        return SafeRunError(code="run_failed", message="The run could not be completed.")
    if status is RunStatus.CANCELED:
        return SafeRunError(code="run_canceled", message="The run was canceled.")
    if status is RunStatus.TIMED_OUT:
        return SafeRunError(code="run_timed_out", message="The run timed out.")
    return None
