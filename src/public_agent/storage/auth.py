from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Collection
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from public_agent.auth import (
    AUTH_AUDIT_READ,
    AUTH_PRINCIPALS_READ,
    AUTH_PRINCIPALS_WRITE,
    AUTH_TOKENS_ISSUE,
    AUTH_TOKENS_READ,
    AUTH_TOKENS_REVOKE,
    CRITICAL_AUTH_PERMISSIONS,
    DEFAULT_MANAGEABLE_PERMISSIONS,
    APIPrincipalRecord,
    APITokenCodec,
    APITokenSummary,
    AuthCursorError,
    AuthenticatedPrincipal,
    AuthenticationAuditOutcome,
    AuthenticationAuditPage,
    AuthenticationAuditQuery,
    AuthenticationAuditRecord,
    AuthenticationError,
    AuthManagementAuthorizationError,
    AuthStateConflictError,
    IssuedAPIToken,
    ManagedPrincipalCreateRequest,
    PrincipalCreateRequest,
    PrincipalManagementPage,
    PrincipalManagementQuery,
    PrincipalStatus,
    TokenManagementPage,
    TokenManagementQuery,
)
from public_agent.core.types import utc_now
from public_agent.storage.models import (
    AgentModel,
    APIPrincipalAgentGrantModel,
    APIPrincipalModel,
    APITokenModel,
    AuthenticationAuditEventModel,
    TenantModel,
)

_AUTHENTICATE_ACTION = "authentication.authenticate"
_CREATE_PRINCIPAL_ACTION = "auth.principal.create"
_SET_PRINCIPAL_STATUS_ACTION = "auth.principal.status.set"
_ISSUE_TOKEN_ACTION = "auth.token.issue"
_REVOKE_TOKEN_ACTION = "auth.token.revoke"


class PostgresAPIKeyService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        codec: APITokenCodec,
        max_active_tokens: int = 100,
        last_used_write_interval_seconds: int = 300,
        manageable_permissions: Collection[str] = DEFAULT_MANAGEABLE_PERMISSIONS,
    ) -> None:
        if not 1 <= max_active_tokens <= 1_000:
            raise ValueError("max_active_tokens must be between 1 and 1000")
        if not 0 <= last_used_write_interval_seconds <= 86_400:
            raise ValueError("last_used_write_interval_seconds must be between 0 and 86400")
        self._sessions = sessions
        self._codec = codec
        self._max_active_tokens = max_active_tokens
        self._last_used_interval = timedelta(seconds=last_used_write_interval_seconds)
        self._manageable_permissions = frozenset(manageable_permissions)
        if not self._manageable_permissions:
            raise ValueError("manageable_permissions must not be empty")

    async def create_principal(self, request: PrincipalCreateRequest) -> APIPrincipalRecord:
        async with self._sessions() as session, session.begin():
            return await _create_principal(session, request)

    async def issue_token(
        self,
        *,
        principal_id: UUID,
        tenant_id: str,
        label: str,
        expires_at: datetime | None = None,
    ) -> IssuedAPIToken:
        normalized_label = label.strip()
        if not normalized_label or len(normalized_label) > 200:
            raise ValueError("token label must contain 1 to 200 characters")
        now = utc_now()
        if expires_at is not None:
            if expires_at.tzinfo is None or expires_at <= now:
                raise ValueError("token expiry must be a future timezone-aware datetime")
            if expires_at > now + timedelta(days=3_650):
                raise ValueError("token expiry cannot exceed 3650 days")
        async with self._sessions() as session, session.begin():
            tenant, principal = await _resolve_principal_scope(
                session,
                principal_id=principal_id,
                tenant_slug=tenant_id,
                for_update=True,
            )
            return await self._issue_token_in_session(
                session,
                tenant=tenant,
                principal=principal,
                label=normalized_label,
                expires_at=expires_at,
                now=now,
            )

    async def authenticate(self, token: str) -> AuthenticatedPrincipal:
        try:
            prefix, candidate_digest = self._codec.parse(token)
        except AuthenticationError:
            await self._append_authentication_failure()
            raise
        now = utc_now()
        authenticated: AuthenticatedPrincipal | None = None
        denied = False
        async with self._sessions() as session, session.begin():
            result = (
                await session.execute(
                    select(APITokenModel, APIPrincipalModel, TenantModel)
                    .join(
                        APIPrincipalModel,
                        APIPrincipalModel.id == APITokenModel.principal_id,
                    )
                    .join(TenantModel, TenantModel.id == APITokenModel.tenant_id)
                    .where(APITokenModel.prefix == prefix)
                )
            ).one_or_none()
            stored_digest = result[0].secret_digest if result is not None else bytes(32)
            digest_matches = self._codec.matches(candidate_digest, stored_digest)
            if result is None:
                denied = True
                _add_audit_event(
                    session,
                    tenant_id=None,
                    actor_principal_id=None,
                    actor_token_id=None,
                    action=_AUTHENTICATE_ACTION,
                    outcome=AuthenticationAuditOutcome.DENIED,
                    metadata={"reason": "invalid_credentials"},
                )
            else:
                token_row, principal, tenant = result
                denied = (
                    not digest_matches
                    or token_row.revoked_at is not None
                    or (token_row.expires_at is not None and token_row.expires_at <= now)
                    or principal.status != PrincipalStatus.ACTIVE.value
                    or not tenant.active
                )
                agent_ids = await _principal_agent_keys(session, principal.id)
                denied = denied or (principal.all_agents and bool(agent_ids))
                denied = denied or (not principal.all_agents and not agent_ids)
                if denied:
                    _add_audit_event(
                        session,
                        tenant_id=tenant.id,
                        actor_principal_id=None,
                        actor_token_id=None,
                        action=_AUTHENTICATE_ACTION,
                        target_principal_id=principal.id,
                        target_token_id=token_row.id,
                        outcome=AuthenticationAuditOutcome.DENIED,
                        metadata={"reason": "invalid_credentials"},
                    )
                else:
                    threshold = now - self._last_used_interval
                    await session.execute(
                        update(APITokenModel)
                        .where(
                            APITokenModel.id == token_row.id,
                            or_(
                                APITokenModel.last_used_at.is_(None),
                                APITokenModel.last_used_at < threshold,
                            ),
                        )
                        .values(last_used_at=now)
                    )
                    authenticated = AuthenticatedPrincipal(
                        principal_id=principal.id,
                        token_id=token_row.id,
                        subject=principal.subject,
                        tenant_id=tenant.slug,
                        allowed_agent_ids=frozenset(agent_ids),
                        all_agents=principal.all_agents,
                        permissions=frozenset(principal.permissions),
                    )
                    _add_audit_event(
                        session,
                        tenant_id=tenant.id,
                        actor_principal_id=principal.id,
                        actor_token_id=token_row.id,
                        action=_AUTHENTICATE_ACTION,
                        target_principal_id=principal.id,
                        target_token_id=token_row.id,
                        outcome=AuthenticationAuditOutcome.SUCCESS,
                    )
        if denied or authenticated is None:
            raise AuthenticationError("authentication required")
        return authenticated

    async def audit_authentication_failure(self) -> None:
        await self._append_authentication_failure()

    async def revoke_token(
        self,
        *,
        token_id: UUID,
        tenant_id: str,
    ) -> bool:
        async with self._sessions() as session, session.begin():
            tenant = await session.scalar(
                select(TenantModel).where(TenantModel.slug == tenant_id)
            )
            if tenant is None:
                raise KeyError("Unknown tenant")
            row = await session.scalar(
                select(APITokenModel)
                .where(
                    APITokenModel.id == token_id,
                    APITokenModel.tenant_id == tenant.id,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError("Unknown API token in requested tenant")
            if row.revoked_at is not None:
                return False
            row.revoked_at = utc_now()
            return True

    async def set_principal_status(
        self,
        *,
        principal_id: UUID,
        tenant_id: str,
        status: PrincipalStatus,
    ) -> APIPrincipalRecord:
        async with self._sessions() as session, session.begin():
            tenant, principal = await _resolve_principal_scope(
                session,
                principal_id=principal_id,
                tenant_slug=tenant_id,
                for_update=True,
            )
            principal.status = status.value
            await session.flush()
            await session.refresh(principal, attribute_names=["updated_at"])
            return await _principal_record(session, principal, tenant_slug=tenant.slug)

    async def list_principals(
        self,
        query: PrincipalManagementQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> PrincipalManagementPage:
        async with self._sessions() as session, session.begin():
            tenant, actor_row, actor_agent_ids = await _resolve_management_actor(
                session,
                actor=actor,
                required_permission=AUTH_PRINCIPALS_READ,
            )
            if tenant.slug != query.tenant_id:
                raise KeyError("Unknown authentication principal scope")
            statement = select(APIPrincipalModel).where(
                APIPrincipalModel.tenant_id == tenant.id
            )
            if query.status is not None:
                statement = statement.where(APIPrincipalModel.status == query.status.value)
            if not actor_row.all_agents:
                outside_scope = exists(
                    select(APIPrincipalAgentGrantModel.principal_id).where(
                        APIPrincipalAgentGrantModel.principal_id
                        == APIPrincipalModel.id,
                        APIPrincipalAgentGrantModel.agent_id.not_in(actor_agent_ids),
                    )
                )
                statement = statement.where(
                    APIPrincipalModel.all_agents.is_(False),
                    ~outside_scope,
                )
            if query.cursor is not None:
                created_at, record_id = _decode_cursor(query.cursor)
                statement = statement.where(
                    or_(
                        APIPrincipalModel.created_at < created_at,
                        and_(
                            APIPrincipalModel.created_at == created_at,
                            APIPrincipalModel.id < record_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    statement.order_by(
                        APIPrincipalModel.created_at.desc(),
                        APIPrincipalModel.id.desc(),
                    ).limit(query.limit + 1)
                )
            )
            page_rows = rows[: query.limit]
            records = tuple(
                [
                    await _principal_record(session, row, tenant_slug=tenant.slug)
                    for row in page_rows
                ]
            )
            next_cursor = None
            if len(rows) > query.limit and page_rows:
                last = page_rows[-1]
                next_cursor = _encode_cursor(last.created_at, last.id)
            return PrincipalManagementPage(items=records, next_cursor=next_cursor)

    async def get_principal(
        self,
        *,
        principal_id: UUID,
        tenant_id: str,
        actor: AuthenticatedPrincipal,
    ) -> APIPrincipalRecord:
        async with self._sessions() as session, session.begin():
            tenant, actor_row, actor_agent_ids = await _resolve_management_actor(
                session,
                actor=actor,
                required_permission=AUTH_PRINCIPALS_READ,
            )
            if tenant.slug != tenant_id:
                raise KeyError("Unknown API principal in requested tenant")
            target = await session.scalar(
                select(APIPrincipalModel).where(
                    APIPrincipalModel.id == principal_id,
                    APIPrincipalModel.tenant_id == tenant.id,
                )
            )
            if target is None or not await _scope_is_manageable(
                session,
                actor=actor_row,
                actor_agent_ids=actor_agent_ids,
                target=target,
            ):
                raise KeyError("Unknown API principal in requested tenant")
            return await _principal_record(session, target, tenant_slug=tenant.slug)

    async def create_managed_principal(
        self,
        request: ManagedPrincipalCreateRequest,
        *,
        actor: AuthenticatedPrincipal,
    ) -> APIPrincipalRecord:
        try:
            async with self._sessions() as session, session.begin():
                tenant, actor_row, actor_agent_ids = await _resolve_management_actor(
                    session,
                    actor=actor,
                    required_permission=AUTH_PRINCIPALS_WRITE,
                    for_update=True,
                )
                await _acquire_tenant_security_lock(session, tenant.id)
                await self._validate_delegation(
                    session,
                    actor=actor_row,
                    actor_agent_ids=actor_agent_ids,
                    request=request,
                )
                record = await _create_principal(
                    session,
                    PrincipalCreateRequest(tenant_id=tenant.slug, **request.model_dump()),
                )
                _add_audit_event(
                    session,
                    tenant_id=tenant.id,
                    actor_principal_id=actor_row.id,
                    actor_token_id=actor.token_id,
                    action=_CREATE_PRINCIPAL_ACTION,
                    target_principal_id=record.id,
                    outcome=AuthenticationAuditOutcome.SUCCESS,
                    metadata={"permission_count": len(record.permissions)},
                )
                return record
        except (
            AuthManagementAuthorizationError,
            AuthStateConflictError,
            KeyError,
            ValueError,
        ) as exc:
            await self._append_management_failure(
                actor=actor,
                action=_CREATE_PRINCIPAL_ACTION,
                outcome=_failure_outcome(exc),
            )
            raise

    async def set_managed_principal_status(
        self,
        *,
        principal_id: UUID,
        status: PrincipalStatus,
        actor: AuthenticatedPrincipal,
    ) -> APIPrincipalRecord:
        try:
            async with self._sessions() as session, session.begin():
                tenant, actor_row, actor_agent_ids = await _resolve_management_actor(
                    session,
                    actor=actor,
                    required_permission=AUTH_PRINCIPALS_WRITE,
                    for_update=True,
                )
                await _acquire_tenant_security_lock(session, tenant.id)
                target = await session.scalar(
                    select(APIPrincipalModel)
                    .where(
                        APIPrincipalModel.id == principal_id,
                        APIPrincipalModel.tenant_id == tenant.id,
                    )
                    .with_for_update()
                )
                if target is None or not await _scope_is_manageable(
                    session,
                    actor=actor_row,
                    actor_agent_ids=actor_agent_ids,
                    target=target,
                ):
                    raise KeyError("Unknown API principal in requested tenant")
                if (
                    status is PrincipalStatus.DISABLED
                    and target.status == PrincipalStatus.ACTIVE.value
                ):
                    await _ensure_security_admin_continuity(
                        session,
                        tenant_id=tenant.id,
                        principal=target,
                        now=utc_now(),
                        disabling=True,
                    )
                changed = target.status != status.value
                target.status = status.value
                await session.flush()
                await session.refresh(target, attribute_names=["updated_at"])
                record = await _principal_record(session, target, tenant_slug=tenant.slug)
                _add_audit_event(
                    session,
                    tenant_id=tenant.id,
                    actor_principal_id=actor_row.id,
                    actor_token_id=actor.token_id,
                    action=_SET_PRINCIPAL_STATUS_ACTION,
                    target_principal_id=target.id,
                    outcome=AuthenticationAuditOutcome.SUCCESS,
                    metadata={"changed": changed, "status": status.value},
                )
                return record
        except (
            AuthManagementAuthorizationError,
            AuthStateConflictError,
            KeyError,
            ValueError,
        ) as exc:
            await self._append_management_failure(
                actor=actor,
                action=_SET_PRINCIPAL_STATUS_ACTION,
                target_principal_id=principal_id,
                outcome=_failure_outcome(exc),
            )
            raise

    async def list_tokens(
        self,
        query: TokenManagementQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> TokenManagementPage:
        async with self._sessions() as session, session.begin():
            tenant, actor_row, actor_agent_ids = await _resolve_management_actor(
                session,
                actor=actor,
                required_permission=AUTH_TOKENS_READ,
            )
            if tenant.slug != query.tenant_id:
                raise KeyError("Unknown API principal in requested tenant")
            target = await session.scalar(
                select(APIPrincipalModel).where(
                    APIPrincipalModel.id == query.principal_id,
                    APIPrincipalModel.tenant_id == tenant.id,
                )
            )
            if target is None or not await _scope_is_manageable(
                session,
                actor=actor_row,
                actor_agent_ids=actor_agent_ids,
                target=target,
            ):
                raise KeyError("Unknown API principal in requested tenant")
            statement = select(APITokenModel).where(
                APITokenModel.tenant_id == tenant.id,
                APITokenModel.principal_id == target.id,
            )
            if query.cursor is not None:
                created_at, record_id = _decode_cursor(query.cursor)
                statement = statement.where(
                    or_(
                        APITokenModel.created_at < created_at,
                        and_(
                            APITokenModel.created_at == created_at,
                            APITokenModel.id < record_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    statement.order_by(
                        APITokenModel.created_at.desc(),
                        APITokenModel.id.desc(),
                    ).limit(query.limit + 1)
                )
            )
            page_rows = rows[: query.limit]
            next_cursor = None
            if len(rows) > query.limit and page_rows:
                last = page_rows[-1]
                next_cursor = _encode_cursor(last.created_at, last.id)
            return TokenManagementPage(
                items=tuple(_token_summary(row) for row in page_rows),
                next_cursor=next_cursor,
            )

    async def issue_managed_token(
        self,
        *,
        principal_id: UUID,
        label: str,
        expires_at: datetime | None,
        actor: AuthenticatedPrincipal,
    ) -> IssuedAPIToken:
        normalized_label, now = _validate_token_issue(label=label, expires_at=expires_at)
        try:
            async with self._sessions() as session, session.begin():
                tenant, actor_row, actor_agent_ids = await _resolve_management_actor(
                    session,
                    actor=actor,
                    required_permission=AUTH_TOKENS_ISSUE,
                    for_update=True,
                )
                await _acquire_tenant_security_lock(session, tenant.id)
                target = await session.scalar(
                    select(APIPrincipalModel)
                    .where(
                        APIPrincipalModel.id == principal_id,
                        APIPrincipalModel.tenant_id == tenant.id,
                    )
                    .with_for_update()
                )
                if target is None or not await _scope_is_manageable(
                    session,
                    actor=actor_row,
                    actor_agent_ids=actor_agent_ids,
                    target=target,
                ):
                    raise KeyError("Unknown API principal in requested tenant")
                issued = await self._issue_token_in_session(
                    session,
                    tenant=tenant,
                    principal=target,
                    label=normalized_label,
                    expires_at=expires_at,
                    now=now,
                )
                _add_audit_event(
                    session,
                    tenant_id=tenant.id,
                    actor_principal_id=actor_row.id,
                    actor_token_id=actor.token_id,
                    action=_ISSUE_TOKEN_ACTION,
                    target_principal_id=target.id,
                    target_token_id=issued.id,
                    outcome=AuthenticationAuditOutcome.SUCCESS,
                    metadata={"has_expiry": expires_at is not None},
                )
                return issued
        except (
            AuthManagementAuthorizationError,
            AuthStateConflictError,
            KeyError,
            ValueError,
        ) as exc:
            await self._append_management_failure(
                actor=actor,
                action=_ISSUE_TOKEN_ACTION,
                target_principal_id=principal_id,
                outcome=_failure_outcome(exc),
            )
            raise

    async def revoke_managed_token(
        self,
        *,
        token_id: UUID,
        actor: AuthenticatedPrincipal,
    ) -> APITokenSummary:
        try:
            async with self._sessions() as session, session.begin():
                tenant, actor_row, actor_agent_ids = await _resolve_management_actor(
                    session,
                    actor=actor,
                    required_permission=AUTH_TOKENS_REVOKE,
                    for_update=True,
                )
                await _acquire_tenant_security_lock(session, tenant.id)
                token_row = await session.scalar(
                    select(APITokenModel)
                    .where(
                        APITokenModel.id == token_id,
                        APITokenModel.tenant_id == tenant.id,
                    )
                    .with_for_update()
                )
                if token_row is None:
                    raise KeyError("Unknown API token in requested tenant")
                target = await session.scalar(
                    select(APIPrincipalModel)
                    .where(
                        APIPrincipalModel.id == token_row.principal_id,
                        APIPrincipalModel.tenant_id == tenant.id,
                    )
                    .with_for_update()
                )
                if target is None or not await _scope_is_manageable(
                    session,
                    actor=actor_row,
                    actor_agent_ids=actor_agent_ids,
                    target=target,
                ):
                    raise KeyError("Unknown API token in requested tenant")
                changed = token_row.revoked_at is None
                if changed:
                    await _ensure_security_admin_continuity(
                        session,
                        tenant_id=tenant.id,
                        principal=target,
                        now=utc_now(),
                        excluding_token_id=token_row.id,
                    )
                    token_row.revoked_at = utc_now()
                    await session.flush()
                summary = _token_summary(token_row)
                _add_audit_event(
                    session,
                    tenant_id=tenant.id,
                    actor_principal_id=actor_row.id,
                    actor_token_id=actor.token_id,
                    action=_REVOKE_TOKEN_ACTION,
                    target_principal_id=target.id,
                    target_token_id=token_row.id,
                    outcome=AuthenticationAuditOutcome.SUCCESS,
                    metadata={"changed": changed},
                )
                return summary
        except (
            AuthManagementAuthorizationError,
            AuthStateConflictError,
            KeyError,
            ValueError,
        ) as exc:
            await self._append_management_failure(
                actor=actor,
                action=_REVOKE_TOKEN_ACTION,
                target_token_id=token_id,
                outcome=_failure_outcome(exc),
            )
            raise

    async def list_audit_events(
        self,
        query: AuthenticationAuditQuery,
        *,
        actor: AuthenticatedPrincipal,
    ) -> AuthenticationAuditPage:
        async with self._sessions() as session, session.begin():
            tenant, _, _ = await _resolve_management_actor(
                session,
                actor=actor,
                required_permission=AUTH_AUDIT_READ,
            )
            if tenant.slug != query.tenant_id:
                raise KeyError("Unknown authentication audit scope")
            statement = select(AuthenticationAuditEventModel).where(
                AuthenticationAuditEventModel.tenant_id == tenant.id
            )
            if query.action is not None:
                statement = statement.where(
                    AuthenticationAuditEventModel.action == query.action
                )
            if query.outcome is not None:
                statement = statement.where(
                    AuthenticationAuditEventModel.outcome == query.outcome.value
                )
            if query.cursor is not None:
                created_at, record_id = _decode_cursor(query.cursor)
                statement = statement.where(
                    or_(
                        AuthenticationAuditEventModel.created_at < created_at,
                        and_(
                            AuthenticationAuditEventModel.created_at == created_at,
                            AuthenticationAuditEventModel.id < record_id,
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    statement.order_by(
                        AuthenticationAuditEventModel.created_at.desc(),
                        AuthenticationAuditEventModel.id.desc(),
                    ).limit(query.limit + 1)
                )
            )
            page_rows = rows[: query.limit]
            next_cursor = None
            if len(rows) > query.limit and page_rows:
                last = page_rows[-1]
                next_cursor = _encode_cursor(last.created_at, last.id)
            return AuthenticationAuditPage(
                items=tuple(
                    _audit_record(row, tenant_slug=tenant.slug) for row in page_rows
                ),
                next_cursor=next_cursor,
            )

    async def _issue_token_in_session(
        self,
        session: AsyncSession,
        *,
        tenant: TenantModel,
        principal: APIPrincipalModel,
        label: str,
        expires_at: datetime | None,
        now: datetime,
    ) -> IssuedAPIToken:
        if principal.status != PrincipalStatus.ACTIVE.value:
            raise ValueError("cannot issue a token for a disabled principal")
        active_count = await session.scalar(
            select(func.count(APITokenModel.id)).where(
                APITokenModel.principal_id == principal.id,
                APITokenModel.revoked_at.is_(None),
                or_(APITokenModel.expires_at.is_(None), APITokenModel.expires_at > now),
            )
        )
        if int(active_count or 0) >= self._max_active_tokens:
            raise ValueError("principal has reached the active token limit")
        for _ in range(3):
            material = self._codec.issue()
            row = APITokenModel(
                id=uuid4(),
                principal_id=principal.id,
                tenant_id=tenant.id,
                prefix=material.prefix,
                secret_digest=material.digest,
                label=label,
                expires_at=expires_at,
            )
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError:
                continue
            return IssuedAPIToken(
                id=row.id,
                principal_id=principal.id,
                label=row.label,
                token=material.token,
                prefix=row.prefix,
                expires_at=row.expires_at,
                created_at=row.created_at,
            )
        raise RuntimeError("could not allocate a unique API token prefix")

    async def _validate_delegation(
        self,
        session: AsyncSession,
        *,
        actor: APIPrincipalModel,
        actor_agent_ids: tuple[UUID, ...],
        request: ManagedPrincipalCreateRequest,
    ) -> None:
        requested_permissions = frozenset(request.permissions)
        if not requested_permissions <= self._manageable_permissions:
            raise AuthManagementAuthorizationError("permission is not server-manageable")
        if not requested_permissions <= frozenset(actor.permissions):
            raise AuthManagementAuthorizationError("permission delegation denied")
        if request.all_agents:
            if not actor.all_agents:
                raise AuthManagementAuthorizationError("agent scope delegation denied")
            return
        requested_agents = await _resolve_agents(
            session,
            tenant_id=actor.tenant_id,
            agent_keys=request.agent_ids,
        )
        if not actor.all_agents and not {row.id for row in requested_agents} <= set(
            actor_agent_ids
        ):
            raise AuthManagementAuthorizationError("agent scope delegation denied")

    async def _append_authentication_failure(self) -> None:
        async with self._sessions() as session, session.begin():
            _add_audit_event(
                session,
                tenant_id=None,
                actor_principal_id=None,
                actor_token_id=None,
                action=_AUTHENTICATE_ACTION,
                outcome=AuthenticationAuditOutcome.DENIED,
                metadata={"reason": "invalid_credentials"},
            )

    async def _append_management_failure(
        self,
        *,
        actor: AuthenticatedPrincipal,
        action: str,
        outcome: AuthenticationAuditOutcome,
        target_principal_id: UUID | None = None,
        target_token_id: UUID | None = None,
    ) -> None:
        try:
            async with self._sessions() as session, session.begin():
                tenant_id = await session.scalar(
                    select(TenantModel.id).where(TenantModel.slug == actor.tenant_id)
                )
                _add_audit_event(
                    session,
                    tenant_id=tenant_id,
                    actor_principal_id=actor.principal_id,
                    actor_token_id=actor.token_id,
                    action=action,
                    target_principal_id=target_principal_id,
                    target_token_id=target_token_id,
                    outcome=outcome,
                    metadata={"reason": "policy_guard"},
                )
        except Exception:
            return


async def _create_principal(
    session: AsyncSession,
    request: PrincipalCreateRequest,
) -> APIPrincipalRecord:
    tenant = await session.scalar(
        select(TenantModel).where(
            TenantModel.slug == request.tenant_id,
            TenantModel.active.is_(True),
        )
    )
    if tenant is None:
        raise KeyError("Unknown active tenant")
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                _principal_lock_id(tenant_id=tenant.id, subject=request.subject)
            )
        )
    )
    agents = await _resolve_agents(
        session,
        tenant_id=tenant.id,
        agent_keys=request.agent_ids,
    )
    existing = await session.scalar(
        select(APIPrincipalModel).where(
            APIPrincipalModel.tenant_id == tenant.id,
            APIPrincipalModel.subject == request.subject,
        )
    )
    if existing is not None:
        record = await _principal_record(session, existing, tenant_slug=tenant.slug)
        if (
            record.display_name != request.display_name
            or record.permissions != request.permissions
            or record.agent_ids != request.agent_ids
            or record.all_agents != request.all_agents
        ):
            raise ValueError("principal subject is bound to different configuration")
        return record
    row = APIPrincipalModel(
        id=uuid4(),
        tenant_id=tenant.id,
        subject=request.subject,
        display_name=request.display_name,
        status=PrincipalStatus.ACTIVE.value,
        permissions=list(request.permissions),
        all_agents=request.all_agents,
    )
    session.add(row)
    await session.flush()
    for agent in agents:
        session.add(
            APIPrincipalAgentGrantModel(
                principal_id=row.id,
                agent_id=agent.id,
                tenant_id=tenant.id,
            )
        )
    await session.flush()
    return await _principal_record(session, row, tenant_slug=tenant.slug)


async def _resolve_agents(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_keys: tuple[str, ...],
) -> tuple[AgentModel, ...]:
    if not agent_keys:
        return ()
    agents = tuple(
        await session.scalars(
            select(AgentModel).where(
                AgentModel.tenant_id == tenant_id,
                AgentModel.agent_key.in_(agent_keys),
            )
        )
    )
    if {agent.agent_key for agent in agents} != set(agent_keys):
        raise KeyError("Unknown agent in requested tenant")
    return agents


async def _resolve_principal_scope(
    session: AsyncSession,
    *,
    principal_id: UUID,
    tenant_slug: str,
    for_update: bool,
) -> tuple[TenantModel, APIPrincipalModel]:
    tenant = await session.scalar(select(TenantModel).where(TenantModel.slug == tenant_slug))
    if tenant is None:
        raise KeyError("Unknown tenant")
    statement = select(APIPrincipalModel).where(
        APIPrincipalModel.id == principal_id,
        APIPrincipalModel.tenant_id == tenant.id,
    )
    if for_update:
        statement = statement.with_for_update()
    principal = await session.scalar(statement)
    if principal is None:
        raise KeyError("Unknown API principal in requested tenant")
    return tenant, principal


async def _resolve_management_actor(
    session: AsyncSession,
    *,
    actor: AuthenticatedPrincipal,
    required_permission: str,
    for_update: bool = False,
) -> tuple[TenantModel, APIPrincipalModel, tuple[UUID, ...]]:
    if actor.principal_id is None or actor.token_id is None:
        raise AuthManagementAuthorizationError("managed authentication identity required")
    tenant = await session.scalar(
        select(TenantModel).where(
            TenantModel.slug == actor.tenant_id,
            TenantModel.active.is_(True),
        )
    )
    if tenant is None:
        raise AuthManagementAuthorizationError("managed authentication identity required")
    principal_statement = select(APIPrincipalModel).where(
        APIPrincipalModel.id == actor.principal_id,
        APIPrincipalModel.tenant_id == tenant.id,
        APIPrincipalModel.status == PrincipalStatus.ACTIVE.value,
    )
    token_statement = select(APITokenModel).where(
        APITokenModel.id == actor.token_id,
        APITokenModel.principal_id == actor.principal_id,
        APITokenModel.tenant_id == tenant.id,
        APITokenModel.revoked_at.is_(None),
        or_(APITokenModel.expires_at.is_(None), APITokenModel.expires_at > utc_now()),
    )
    if for_update:
        principal_statement = principal_statement.with_for_update()
        token_statement = token_statement.with_for_update()
    principal = await session.scalar(principal_statement)
    token_row = await session.scalar(token_statement)
    if principal is None or token_row is None or required_permission not in principal.permissions:
        raise AuthManagementAuthorizationError("management permission denied")
    agent_ids = await _principal_agent_ids(session, principal.id)
    if principal.all_agents == bool(agent_ids):
        raise AuthManagementAuthorizationError("invalid management agent scope")
    return tenant, principal, agent_ids


async def _scope_is_manageable(
    session: AsyncSession,
    *,
    actor: APIPrincipalModel,
    actor_agent_ids: tuple[UUID, ...],
    target: APIPrincipalModel,
) -> bool:
    if actor.all_agents:
        return True
    if target.all_agents:
        return False
    target_agent_ids = await _principal_agent_ids(session, target.id)
    return set(target_agent_ids) <= set(actor_agent_ids)


async def _principal_agent_ids(
    session: AsyncSession,
    principal_id: UUID,
) -> tuple[UUID, ...]:
    return tuple(
        await session.scalars(
            select(APIPrincipalAgentGrantModel.agent_id).where(
                APIPrincipalAgentGrantModel.principal_id == principal_id
            )
        )
    )


async def _acquire_tenant_security_lock(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(
        select(func.pg_advisory_xact_lock(_tenant_security_lock_id(tenant_id)))
    )


async def _ensure_security_admin_continuity(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    principal: APIPrincipalModel,
    now: datetime,
    disabling: bool = False,
    excluding_token_id: UUID | None = None,
) -> None:
    protected_permissions = (
        CRITICAL_AUTH_PERMISSIONS.intersection(principal.permissions)
        if principal.all_agents
        else frozenset()
    )
    if not protected_permissions or principal.status != PrincipalStatus.ACTIVE.value:
        return
    usable_token_conditions = [
        APITokenModel.principal_id == principal.id,
        APITokenModel.revoked_at.is_(None),
        or_(APITokenModel.expires_at.is_(None), APITokenModel.expires_at > now),
    ]
    if excluding_token_id is not None:
        usable_token_conditions.append(APITokenModel.id != excluding_token_id)
    if not disabling:
        another_token = await session.scalar(
            select(exists().where(*usable_token_conditions))
        )
        if another_token:
            return
    else:
        currently_usable = await session.scalar(
            select(exists().where(*usable_token_conditions))
        )
        if not currently_usable:
            return
    for permission in protected_permissions:
        alternative_has_token = exists(
            select(APITokenModel.id).where(
                APITokenModel.principal_id == APIPrincipalModel.id,
                APITokenModel.revoked_at.is_(None),
                or_(APITokenModel.expires_at.is_(None), APITokenModel.expires_at > now),
            )
        )
        alternative = await session.scalar(
            select(
                exists().where(
                    APIPrincipalModel.tenant_id == tenant_id,
                    APIPrincipalModel.id != principal.id,
                    APIPrincipalModel.status == PrincipalStatus.ACTIVE.value,
                    APIPrincipalModel.all_agents.is_(True),
                    APIPrincipalModel.permissions.contains([permission]),
                    alternative_has_token,
                )
            )
        )
        if not alternative:
            raise AuthStateConflictError(
                "operation would remove the last usable security administrator"
            )


def _validate_token_issue(
    *,
    label: str,
    expires_at: datetime | None,
) -> tuple[str, datetime]:
    normalized_label = label.strip()
    if not normalized_label or len(normalized_label) > 200:
        raise ValueError("token label must contain 1 to 200 characters")
    now = utc_now()
    if expires_at is not None:
        if expires_at.tzinfo is None or expires_at <= now:
            raise ValueError("token expiry must be a future timezone-aware datetime")
        if expires_at > now + timedelta(days=3_650):
            raise ValueError("token expiry cannot exceed 3650 days")
    return normalized_label, now


def _add_audit_event(
    session: AsyncSession,
    *,
    tenant_id: UUID | None,
    actor_principal_id: UUID | None,
    actor_token_id: UUID | None,
    action: str,
    outcome: AuthenticationAuditOutcome,
    target_principal_id: UUID | None = None,
    target_token_id: UUID | None = None,
    metadata: dict[str, str | int | bool | None] | None = None,
) -> None:
    session.add(
        AuthenticationAuditEventModel(
            id=uuid4(),
            tenant_id=tenant_id,
            actor_principal_id=actor_principal_id,
            actor_token_id=actor_token_id,
            action=action,
            target_principal_id=target_principal_id,
            target_token_id=target_token_id,
            outcome=outcome.value,
            safe_metadata=dict(metadata or {}),
        )
    )


def _failure_outcome(exc: Exception) -> AuthenticationAuditOutcome:
    if isinstance(exc, AuthStateConflictError):
        return AuthenticationAuditOutcome.CONFLICT
    return AuthenticationAuditOutcome.DENIED


async def _principal_agent_keys(session: AsyncSession, principal_id: UUID) -> tuple[str, ...]:
    return tuple(
        await session.scalars(
            select(AgentModel.agent_key)
            .join(
                APIPrincipalAgentGrantModel,
                APIPrincipalAgentGrantModel.agent_id == AgentModel.id,
            )
            .where(APIPrincipalAgentGrantModel.principal_id == principal_id)
            .order_by(AgentModel.agent_key)
        )
    )


async def _principal_record(
    session: AsyncSession,
    row: APIPrincipalModel,
    *,
    tenant_slug: str,
) -> APIPrincipalRecord:
    agent_ids = await _principal_agent_keys(session, row.id)
    return APIPrincipalRecord(
        id=row.id,
        tenant_id=tenant_slug,
        subject=row.subject,
        display_name=row.display_name,
        status=PrincipalStatus(row.status),
        permissions=tuple(row.permissions),
        agent_ids=agent_ids,
        all_agents=row.all_agents,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _principal_lock_id(*, tenant_id: UUID, subject: str) -> int:
    digest = hashlib.sha256(f"{tenant_id}|{subject}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _tenant_security_lock_id(tenant_id: UUID) -> int:
    digest = hashlib.sha256(f"auth-security|{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _token_summary(row: APITokenModel) -> APITokenSummary:
    return APITokenSummary(
        id=row.id,
        principal_id=row.principal_id,
        label=row.label,
        prefix=row.prefix,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
    )


def _audit_record(
    row: AuthenticationAuditEventModel,
    *,
    tenant_slug: str | None,
) -> AuthenticationAuditRecord:
    return AuthenticationAuditRecord(
        id=row.id,
        tenant_id=tenant_slug,
        actor_principal_id=row.actor_principal_id,
        actor_token_id=row.actor_token_id,
        action=row.action,
        target_principal_id=row.target_principal_id,
        target_token_id=row.target_token_id,
        outcome=AuthenticationAuditOutcome(row.outcome),
        metadata={
            key: value
            for key, value in row.safe_metadata.items()
            if isinstance(value, (str, int, bool)) or value is None
        },
        created_at=row.created_at,
    )


def _encode_cursor(created_at: datetime, record_id: UUID) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(record_id), "v": 1},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    if not value or len(value) > 500:
        raise AuthCursorError("invalid authentication management cursor")
    try:
        padding = "=" * (-len(value) % 4)
        encoded = (value + padding).encode("ascii")
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"created_at", "id", "v"}:
            raise TypeError
        if payload["v"] != 1:
            raise ValueError
        created_at = datetime.fromisoformat(payload["created_at"])
        record_id = UUID(payload["id"])
        if created_at.tzinfo is None:
            raise ValueError
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise AuthCursorError("invalid authentication management cursor") from exc
    return created_at, record_id
