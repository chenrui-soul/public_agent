from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Protocol, TypeVar, overload

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from public_agent.api.base import APIError, APIPrincipal
from public_agent.auth import AuthenticatedPrincipal, AuthenticationError

_BEARER = HTTPBearer(auto_error=False)
PrincipalT = TypeVar("PrincipalT", bound=APIPrincipal)


class APIKeyAuthenticator(Protocol):
    async def authenticate(self, token: str) -> AuthenticatedPrincipal: ...


@overload
def create_bearer_principal_dependency(
    authenticator: APIKeyAuthenticator,
    *,
    principal_type: type[PrincipalT],
) -> Callable[..., Awaitable[PrincipalT]]: ...


@overload
def create_bearer_principal_dependency(
    authenticator: APIKeyAuthenticator,
) -> Callable[..., Awaitable[APIPrincipal]]: ...


def create_bearer_principal_dependency(
    authenticator: APIKeyAuthenticator,
    *,
    principal_type: type[APIPrincipal] = APIPrincipal,
) -> Callable[..., Awaitable[APIPrincipal]]:
    async def authenticated_principal(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(_BEARER),
        ],
    ) -> APIPrincipal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            await _audit_missing_authentication(authenticator)
            raise _authentication_required()
        try:
            principal = await authenticator.authenticate(credentials.credentials)
        except AuthenticationError:
            raise _authentication_required() from None
        except Exception:
            raise APIError(
                status_code=503,
                code="authentication_unavailable",
                message="Authentication is temporarily unavailable.",
            ) from None
        return principal_type.model_validate(principal.model_dump())

    return authenticated_principal


async def _audit_missing_authentication(authenticator: APIKeyAuthenticator) -> None:
    audit_failure = getattr(authenticator, "audit_authentication_failure", None)
    if audit_failure is None:
        return
    try:
        await audit_failure()
    except Exception:
        return


def _authentication_required() -> APIError:
    return APIError(
        status_code=401,
        code="authentication_required",
        message="Valid bearer authentication is required.",
    )
