"""Errors as RFC 9457 problem details (``application/problem+json``).

Domain exceptions are mapped here, in one table, so status codes never leak
into the domain layer. Problem ``type`` values are URNs: stable identifiers
that clients can switch on, documented in the README.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from esign.domain.errors import (
    ConcurrencyError,
    DocumentTooLargeError,
    DomainError,
    IdempotencyKeyReusedError,
    InvalidDocumentError,
    InvalidTransitionError,
    NotFoundError,
    SigningLinkExpiredError,
    SigningLinkInactiveError,
    SigningLinkNotFoundError,
    ValidationError,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"
TYPE_PREFIX = "urn:esign:problem:"

# exception class -> (HTTP status, type slug, title)
_DOMAIN_PROBLEMS: dict[type[DomainError], tuple[int, str, str]] = {
    NotFoundError: (404, "not-found", "Resource not found"),
    ValidationError: (422, "validation-failed", "Request violates a business rule"),
    InvalidDocumentError: (422, "invalid-document", "Document is not a usable PDF"),
    DocumentTooLargeError: (413, "document-too-large", "Document is too large"),
    InvalidTransitionError: (409, "invalid-state-transition", "Invalid envelope state transition"),
    SigningLinkNotFoundError: (404, "signing-link-not-found", "Signing link not found"),
    SigningLinkExpiredError: (410, "signing-link-expired", "Signing link has expired"),
    SigningLinkInactiveError: (410, "signing-link-inactive", "Signing link is no longer active"),
    ConcurrencyError: (409, "concurrent-modification", "Concurrent modification"),
    IdempotencyKeyReusedError: (422, "idempotency-key-reused", "Idempotency-Key reused"),
}


class ProblemDetails(BaseModel):
    """Documents the error shape in OpenAPI. Extension members are allowed."""

    model_config = ConfigDict(extra="allow")

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


def problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str | None = None,
    problem_type: str = "about:blank",
    headers: Mapping[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": problem_type,
        "title": title,
        "status": status,
        "instance": request.url.path,
        **extensions,
    }
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


def _domain_problem(request: Request, exc: DomainError) -> JSONResponse:
    # Walk the class hierarchy so subclasses inherit their parent's mapping.
    for cls in type(exc).__mro__:
        if cls in _DOMAIN_PROBLEMS:
            status, slug, title = _DOMAIN_PROBLEMS[cls]
            break
    else:
        status, slug, title = 400, "domain-error", "Request rejected"

    extensions: dict[str, Any] = {}
    if isinstance(exc, InvalidTransitionError):
        extensions = {"current_status": exc.current, "target_status": exc.target}
    return problem_response(
        request,
        status=status,
        title=title,
        detail=str(exc),
        problem_type=TYPE_PREFIX + slug,
        **extensions,
    )


def _http_problem(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    title = HTTPStatus(exc.status_code).phrase
    detail = exc.detail if exc.detail != title else None
    return problem_response(
        request, status=exc.status_code, title=title, detail=detail, headers=exc.headers
    )


def _validation_problem(request: Request, exc: RequestValidationError) -> JSONResponse:
    # The offending input values are left out on purpose: they may contain personal data.
    errors = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        title="Request validation failed",
        problem_type=TYPE_PREFIX + "invalid-request",
        errors=errors,
    )


def internal_error_response(request: Request) -> JSONResponse:
    return problem_response(
        request,
        status=500,
        title="Internal Server Error",
        detail="An unexpected error occurred. Quote the X-Request-ID header when reporting it.",
    )


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def on_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return _domain_problem(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _http_problem(request, exc)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _validation_problem(request, exc)
