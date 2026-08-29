from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from public_agent.auth import AuthenticatedPrincipal, PrincipalStatus
from public_agent.core.types import utc_now
from public_agent.operations.capacity_control import CapacityGovernanceAuthorizationError
from public_agent.storage.models import (
    APIPrincipalAgentGrantModel,
    APIPrincipalModel,
    APITokenModel,
    TenantModel,
)


@dataclass(frozen=True, slots=True)
class AuthorizedGlobalActor:
    tenant_id: UUID
    principal_id: UUID
    token_id: UUID
    subject: str


async def authorize_global_operation(
    session: AsyncSession,
    *,
    actor: AuthenticatedPrincipal,
    governance_tenant: str,
    permission: str,
    for_update: bool = False,
) -> AuthorizedGlobalActor:
    if (
        actor.principal_id is None
        or actor.token_id is None
        or actor.tenant_id != governance_tenant
    ):
        raise CapacityGovernanceAuthorizationError("managed global identity required")
    tenant_statement = select(TenantModel).where(
        TenantModel.slug == governance_tenant,
        TenantModel.active.is_(True),
    )
    principal_statement = select(APIPrincipalModel).where(
        APIPrincipalModel.id == actor.principal_id,
        APIPrincipalModel.status == PrincipalStatus.ACTIVE.value,
    )
    token_statement = select(APITokenModel).where(
        APITokenModel.id == actor.token_id,
        APITokenModel.principal_id == actor.principal_id,
        APITokenModel.revoked_at.is_(None),
        or_(APITokenModel.expires_at.is_(None), APITokenModel.expires_at > utc_now()),
    )
    if for_update:
        tenant_statement = tenant_statement.with_for_update()
        principal_statement = principal_statement.with_for_update()
        token_statement = token_statement.with_for_update()
    tenant = await session.scalar(tenant_statement)
    if tenant is None:
        raise CapacityGovernanceAuthorizationError("managed global identity required")
    principal = await session.scalar(
        principal_statement.where(APIPrincipalModel.tenant_id == tenant.id)
    )
    token = await session.scalar(token_statement.where(APITokenModel.tenant_id == tenant.id))
    grant_count = await session.scalar(
        select(func.count(APIPrincipalAgentGrantModel.agent_id)).where(
            APIPrincipalAgentGrantModel.principal_id == actor.principal_id,
            APIPrincipalAgentGrantModel.tenant_id == tenant.id,
        )
    )
    if (
        principal is None
        or token is None
        or not principal.all_agents
        or int(grant_count or 0) != 0
        or permission not in principal.permissions
    ):
        raise CapacityGovernanceAuthorizationError("capacity governance permission denied")
    return AuthorizedGlobalActor(
        tenant_id=tenant.id,
        principal_id=principal.id,
        token_id=token.id,
        subject=principal.subject,
    )
