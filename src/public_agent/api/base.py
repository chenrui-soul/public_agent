from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from public_agent.auth import AuthenticatedPrincipal


class APIPrincipal(AuthenticatedPrincipal):
    """Trusted server-side identity shared by management API resources."""

    def require(
        self,
        *,
        agent_id: str,
        permission: str,
        code: str,
        message: str,
    ) -> None:
        if permission not in self.permissions or not self.can_access_agent(agent_id):
            raise APIError(status_code=403, code=code, message=message)


PrincipalDependency = Callable[..., APIPrincipal | Awaitable[APIPrincipal]]


class APIError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def install_api_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_failed",
                    "message": "Request validation failed.",
                }
            },
        )
