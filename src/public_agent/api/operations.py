from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query
from pydantic import BaseModel, ConfigDict

from public_agent.api.base import APIError, APIPrincipal
from public_agent.auth import AuthenticatedPrincipal
from public_agent.operations import (
    ManagedReflectionJobState,
    ReflectionJobAuthorizationError,
    ReflectionJobConflictError,
    ReflectionJobCursorError,
    ReflectionJobPage,
    ReflectionJobQuery,
    ReflectionJobRecord,
    ReflectionJobRetryResult,
    ReflectionJobStats,
    ReflectionJobStatsQuery,
    RetryReflectionJobRequest,
)


class OperationsPrincipal(APIPrincipal):
    """Trusted server-side identity used for reflection job operations."""


OperationsPrincipalDependency = Callable[
    ..., OperationsPrincipal | Awaitable[OperationsPrincipal]
]


class ReflectionJobOperationsService(Protocol):
    async def stats(
        self,
        query: ReflectionJobStatsQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobStats: ...

    async def list_jobs(
        self,
        query: ReflectionJobQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobPage: ...

    async def get_job(
        self,
        *,
        job_id: UUID,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobRecord: ...

    async def retry_job(
        self,
        *,
        job_id: UUID,
        expected_version: int,
        idempotency_key: str,
        actor: AuthenticatedPrincipal,
    ) -> ReflectionJobRetryResult: ...


class ReflectionJobResponse(BaseModel):
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

    @classmethod
    def from_record(cls, record: ReflectionJobRecord) -> ReflectionJobResponse:
        return cls(**record.model_dump())


class ReflectionJobPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ReflectionJobResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: ReflectionJobPage) -> ReflectionJobPageResponse:
        return cls(
            items=tuple(ReflectionJobResponse.from_record(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class ReflectionJobStatsResponse(BaseModel):
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

    @classmethod
    def from_record(cls, record: ReflectionJobStats) -> ReflectionJobStatsResponse:
        return cls(**record.model_dump())


class ReflectionJobRetryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    run_id: UUID
    agent_id: str
    previous_status: ManagedReflectionJobState
    status: ManagedReflectionJobState
    version: int
    idempotent_replay: bool

    @classmethod
    def from_record(
        cls,
        record: ReflectionJobRetryResult,
    ) -> ReflectionJobRetryResponse:
        return cls(**record.model_dump())


def install_operations_routes(
    app: FastAPI,
    *,
    service: ReflectionJobOperationsService,
    principal_dependency: OperationsPrincipalDependency,
) -> None:
    router = APIRouter(
        prefix="/v1/operations/reflection-jobs",
        tags=["operations"],
    )
    principal_depends = Depends(principal_dependency)

    @router.get("/stats", response_model=ReflectionJobStatsResponse)
    async def reflection_job_stats(
        handler_version: Annotated[
            str,
            Query(min_length=1, max_length=64),
        ] = "reflection-v1",
        agent_id: Annotated[
            str | None,
            Query(min_length=1, max_length=100),
        ] = None,
        current: OperationsPrincipal = principal_depends,
    ) -> ReflectionJobStatsResponse:
        try:
            record = await service.stats(
                ReflectionJobStatsQuery(
                    tenant_id=current.tenant_id,
                    handler_version=handler_version,
                    agent_id=agent_id,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return ReflectionJobStatsResponse.from_record(record)

    @router.get("", response_model=ReflectionJobPageResponse)
    async def list_reflection_jobs(
        handler_version: Annotated[
            str,
            Query(min_length=1, max_length=64),
        ] = "reflection-v1",
        agent_id: Annotated[
            str | None,
            Query(min_length=1, max_length=100),
        ] = None,
        job_status: Annotated[
            ManagedReflectionJobState | None,
            Query(alias="status"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: OperationsPrincipal = principal_depends,
    ) -> ReflectionJobPageResponse:
        try:
            page = await service.list_jobs(
                ReflectionJobQuery(
                    tenant_id=current.tenant_id,
                    handler_version=handler_version,
                    agent_id=agent_id,
                    status=job_status,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return ReflectionJobPageResponse.from_page(page)

    @router.get("/{job_id}", response_model=ReflectionJobResponse)
    async def get_reflection_job(
        job_id: UUID,
        current: OperationsPrincipal = principal_depends,
    ) -> ReflectionJobResponse:
        try:
            record = await service.get_job(job_id=job_id, actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None
        return ReflectionJobResponse.from_record(record)

    @router.post("/{job_id}/retry", response_model=ReflectionJobRetryResponse)
    async def retry_reflection_job(
        job_id: UUID,
        body: RetryReflectionJobRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
        current: OperationsPrincipal = principal_depends,
    ) -> ReflectionJobRetryResponse:
        try:
            record = await service.retry_job(
                job_id=job_id,
                expected_version=body.expected_version,
                idempotency_key=idempotency_key,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return ReflectionJobRetryResponse.from_record(record)

    app.include_router(router)


def _mapped_error(exc: Exception) -> APIError:
    if isinstance(exc, APIError):
        return exc
    if isinstance(exc, ReflectionJobAuthorizationError):
        return APIError(
            status_code=403,
            code="operations_jobs_forbidden",
            message="The authenticated principal cannot access this operations scope.",
        )
    if isinstance(exc, KeyError):
        return APIError(
            status_code=404,
            code="reflection_job_not_found",
            message="The requested reflection job was not found.",
        )
    if isinstance(exc, ReflectionJobCursorError):
        return APIError(
            status_code=400,
            code="invalid_cursor",
            message="The reflection job cursor is invalid.",
        )
    if isinstance(exc, ReflectionJobConflictError):
        codes = {
            "operations.jobs.idempotency_conflict": "idempotency_conflict",
            "operations.jobs.state_conflict": "reflection_job_state_conflict",
            "operations.jobs.version_conflict": "reflection_job_version_conflict",
        }
        return APIError(
            status_code=409,
            code=codes.get(exc.code, "reflection_job_conflict"),
            message="The reflection job cannot be retried from its current state.",
        )
    if isinstance(exc, ValueError):
        return APIError(
            status_code=400,
            code="invalid_operations_request",
            message="The reflection job operations request is invalid.",
        )
    return APIError(
        status_code=500,
        code="operations_internal_error",
        message="The reflection job operations request could not be completed.",
    )
