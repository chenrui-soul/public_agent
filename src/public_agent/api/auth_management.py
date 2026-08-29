from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from public_agent.api.base import APIError, APIPrincipal
from public_agent.auth import (
    APIPrincipalRecord,
    APITokenSummary,
    AuthCursorError,
    AuthenticatedPrincipal,
    AuthenticationAuditOutcome,
    AuthenticationAuditPage,
    AuthenticationAuditQuery,
    AuthManagementAuthorizationError,
    AuthStateConflictError,
    IssuedAPIToken,
    ManagedPrincipalCreateRequest,
    PrincipalManagementPage,
    PrincipalManagementQuery,
    PrincipalStatus,
    TokenManagementPage,
    TokenManagementQuery,
)


class AuthManagementPrincipal(APIPrincipal):
    """Trusted server-side identity used for authentication administration."""


AuthManagementPrincipalDependency = Callable[
    ..., AuthManagementPrincipal | Awaitable[AuthManagementPrincipal]
]


class AuthenticationManagementService(Protocol):
    async def list_principals(
        self,
        query: PrincipalManagementQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> PrincipalManagementPage: ...

    async def get_principal(
        self,
        *,
        principal_id: UUID,
        tenant_id: str,
        actor: AuthenticatedPrincipal,
    ) -> APIPrincipalRecord: ...

    async def create_managed_principal(
        self,
        request: ManagedPrincipalCreateRequest,
        *,
        actor: AuthenticatedPrincipal,
    ) -> APIPrincipalRecord: ...

    async def set_managed_principal_status(
        self,
        *,
        principal_id: UUID,
        status: PrincipalStatus,
        actor: AuthenticatedPrincipal,
    ) -> APIPrincipalRecord: ...

    async def list_tokens(
        self,
        query: TokenManagementQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> TokenManagementPage: ...

    async def issue_managed_token(
        self,
        *,
        principal_id: UUID,
        label: str,
        expires_at: datetime | None,
        actor: AuthenticatedPrincipal,
    ) -> IssuedAPIToken: ...

    async def revoke_managed_token(
        self,
        *,
        token_id: UUID,
        actor: AuthenticatedPrincipal,
    ) -> APITokenSummary: ...

    async def list_audit_events(
        self,
        query: AuthenticationAuditQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> AuthenticationAuditPage: ...


class PrincipalStatusRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: PrincipalStatus


class TokenIssueRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("token label must not be blank")
        return normalized


class PrincipalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    subject: str
    display_name: str
    status: PrincipalStatus
    permissions: tuple[str, ...]
    agent_ids: tuple[str, ...]
    all_agents: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: APIPrincipalRecord) -> PrincipalResponse:
        return cls(**record.model_dump(exclude={"tenant_id"}))


class PrincipalPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[PrincipalResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: PrincipalManagementPage) -> PrincipalPageResponse:
        return cls(
            items=tuple(PrincipalResponse.from_record(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class TokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    principal_id: UUID
    label: str
    prefix: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime

    @classmethod
    def from_summary(cls, record: APITokenSummary) -> TokenResponse:
        return cls(**record.model_dump())


class TokenPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[TokenResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: TokenManagementPage) -> TokenPageResponse:
        return cls(
            items=tuple(TokenResponse.from_summary(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class IssuedTokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    principal_id: UUID
    label: str
    token: str
    prefix: str
    expires_at: datetime | None
    created_at: datetime

    @classmethod
    def from_record(cls, record: IssuedAPIToken) -> IssuedTokenResponse:
        return cls(
            id=record.id,
            principal_id=record.principal_id,
            label=record.label,
            token=record.token.get_secret_value(),
            prefix=record.prefix,
            expires_at=record.expires_at,
            created_at=record.created_at,
        )


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    actor_principal_id: UUID | None
    actor_token_id: UUID | None
    action: str
    target_principal_id: UUID | None
    target_token_id: UUID | None
    outcome: AuthenticationAuditOutcome
    metadata: dict[str, str | int | bool | None]
    created_at: datetime


class AuditPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[AuditEventResponse, ...]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: AuthenticationAuditPage) -> AuditPageResponse:
        return cls(
            items=tuple(
                AuditEventResponse(**item.model_dump(exclude={"tenant_id"}))
                for item in page.items
            ),
            next_cursor=page.next_cursor,
        )


def install_auth_management_routes(
    app: FastAPI,
    *,
    service: AuthenticationManagementService,
    principal_dependency: AuthManagementPrincipalDependency,
) -> None:
    router = APIRouter(prefix="/v1/auth", tags=["authentication"])
    principal_depends = Depends(principal_dependency)

    @router.get("/principals", response_model=PrincipalPageResponse)
    async def list_principals(
        principal_status: PrincipalStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: AuthManagementPrincipal = principal_depends,
    ) -> PrincipalPageResponse:
        try:
            page = await service.list_principals(
                PrincipalManagementQuery(
                    tenant_id=current.tenant_id,
                    status=principal_status,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return PrincipalPageResponse.from_page(page)

    @router.post(
        "/principals",
        response_model=PrincipalResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_principal(
        body: ManagedPrincipalCreateRequest,
        current: AuthManagementPrincipal = principal_depends,
    ) -> PrincipalResponse:
        try:
            record = await service.create_managed_principal(body, actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None
        return PrincipalResponse.from_record(record)

    @router.get("/principals/{principal_id}", response_model=PrincipalResponse)
    async def get_principal(
        principal_id: UUID,
        current: AuthManagementPrincipal = principal_depends,
    ) -> PrincipalResponse:
        try:
            record = await service.get_principal(
                principal_id=principal_id,
                tenant_id=current.tenant_id,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return PrincipalResponse.from_record(record)

    @router.post(
        "/principals/{principal_id}/status",
        response_model=PrincipalResponse,
    )
    async def set_principal_status(
        principal_id: UUID,
        body: PrincipalStatusRequest,
        current: AuthManagementPrincipal = principal_depends,
    ) -> PrincipalResponse:
        try:
            record = await service.set_managed_principal_status(
                principal_id=principal_id,
                status=body.status,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return PrincipalResponse.from_record(record)

    @router.get(
        "/principals/{principal_id}/tokens",
        response_model=TokenPageResponse,
    )
    async def list_tokens(
        principal_id: UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: AuthManagementPrincipal = principal_depends,
    ) -> TokenPageResponse:
        try:
            page = await service.list_tokens(
                TokenManagementQuery(
                    tenant_id=current.tenant_id,
                    principal_id=principal_id,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return TokenPageResponse.from_page(page)

    @router.post(
        "/principals/{principal_id}/tokens",
        response_model=IssuedTokenResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def issue_token(
        principal_id: UUID,
        body: TokenIssueRequest,
        current: AuthManagementPrincipal = principal_depends,
    ) -> IssuedTokenResponse:
        try:
            record = await service.issue_managed_token(
                principal_id=principal_id,
                label=body.label,
                expires_at=body.expires_at,
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return IssuedTokenResponse.from_record(record)

    @router.post("/tokens/{token_id}/revoke", response_model=TokenResponse)
    async def revoke_token(
        token_id: UUID,
        current: AuthManagementPrincipal = principal_depends,
    ) -> TokenResponse:
        try:
            record = await service.revoke_managed_token(token_id=token_id, actor=current)
        except Exception as exc:
            raise _mapped_error(exc) from None
        return TokenResponse.from_summary(record)

    @router.get("/audit-events", response_model=AuditPageResponse)
    async def list_audit_events(
        action: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        outcome: AuthenticationAuditOutcome | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=500)] = None,
        current: AuthManagementPrincipal = principal_depends,
    ) -> AuditPageResponse:
        try:
            page = await service.list_audit_events(
                AuthenticationAuditQuery(
                    tenant_id=current.tenant_id,
                    action=action,
                    outcome=outcome,
                    limit=limit,
                    cursor=cursor,
                ),
                actor=current,
            )
        except Exception as exc:
            raise _mapped_error(exc) from None
        return AuditPageResponse.from_page(page)

    app.include_router(router)


def _mapped_error(exc: Exception) -> APIError:
    if isinstance(exc, APIError):
        return exc
    if isinstance(exc, AuthManagementAuthorizationError):
        return APIError(
            status_code=403,
            code="auth_management_forbidden",
            message="The authenticated principal cannot perform this authentication action.",
        )
    if isinstance(exc, KeyError):
        return APIError(
            status_code=404,
            code="auth_resource_not_found",
            message="The requested authentication resource was not found.",
        )
    if isinstance(exc, AuthCursorError):
        return APIError(
            status_code=400,
            code="invalid_cursor",
            message="The authentication management cursor is invalid.",
        )
    if isinstance(exc, AuthStateConflictError):
        return APIError(
            status_code=409,
            code="auth_state_conflict",
            message="The authentication resource cannot be changed from its current state.",
        )
    if isinstance(exc, ValueError):
        return APIError(
            status_code=400,
            code="invalid_auth_request",
            message="The authentication management request is invalid.",
        )
    return APIError(
        status_code=500,
        code="auth_management_internal_error",
        message="The authentication management operation could not be completed.",
    )
