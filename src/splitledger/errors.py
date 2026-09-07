"""Domain errors and the single JSON error envelope the API speaks."""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for errors that map cleanly onto an HTTP response."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    """The requested resource does not exist, or is not visible to the caller."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    """The request collides with existing state, such as a duplicate email."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class DomainValidationError(AppError):
    """The payload parsed, but violates a business rule."""

    # Literal 422: Starlette renamed the constant, and both spellings must keep working.
    status_code = 422
    code = "validation_error"


class AuthenticationError(AppError):
    """No usable credentials were presented."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"


class PermissionDeniedError(AppError):
    """Valid credentials, insufficient rights."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the response body every failing endpoint returns."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


def jsonable_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Strip the non-serialisable ctx values pydantic sometimes attaches."""
    cleaned: list[dict[str, Any]] = []
    for error in exc.errors():
        item: dict[str, Any] = {k: v for k, v in error.items() if k != "ctx"}
        item["loc"] = [str(part) for part in error.get("loc", ())]
        cleaned.append(item)
    return cleaned


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers so every failure shares one response shape."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: Exception) -> JSONResponse:
        # Starlette types handlers as taking a bare Exception; registration guarantees the class.
        error = exc if isinstance(exc, AppError) else AppError(str(exc))
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(error, AuthenticationError) else None
        return JSONResponse(
            status_code=error.status_code,
            content=error_body(error.code, error.message, error.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: Exception) -> JSONResponse:
        details: dict[str, Any] = {}
        if isinstance(exc, RequestValidationError):
            details["errors"] = jsonable_errors(exc)
        return JSONResponse(
            status_code=DomainValidationError.status_code,
            content=error_body(
                "validation_error",
                "Request payload failed validation.",
                details,
            ),
        )
