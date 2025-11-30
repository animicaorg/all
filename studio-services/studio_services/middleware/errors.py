from __future__ import annotations

"""
Exception → RFC7807 "problem+json" mappers for FastAPI.

- Produces `application/problem+json` for:
    * Internal ApiError subclasses (from studio_services.errors)
    * Starlette/FastAPI HTTPException
    * Pydantic/RequestValidationError
    * Unhandled exceptions (500)
- Attaches tracing hints when available:
    request.state.request_id, request.state.trace_id
- Never leaks stack traces in responses; logs them instead.
"""

import logging
from typing import Any, Dict, Optional, Tuple, Type

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Try import our structured errors; remain tolerant if shapes differ
try:
    from studio_services.errors import ApiError  # type: ignore
except Exception:  # pragma: no cover - defensive

    class ApiError(Exception):  # type: ignore
        pass


PROBLEM_CT = "application/problem+json"


def _get_logger():
    # Prefer structlog if configured; otherwise stdlib
    try:
        import structlog  # type: ignore

        return structlog.get_logger(__name__)
    except Exception:
        return logging.getLogger(__name__)


log = _get_logger()


def _state_ids(request: Request) -> Dict[str, str]:
    rid = getattr(request.state, "request_id", "") or ""
    tid = getattr(request.state, "trace_id", "") or ""
    return {"request_id": rid, "trace_id": tid}


def _base_problem(
    request: Request,
    *,
    status: int,
    title: str,
    detail: Optional[str] = None,
    type_uri: str = "about:blank",
    code: Optional[str] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build an RFC7807 dictionary with safe extension members.
    """
    prob: Dict[str, Any] = {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": detail or "",
        "instance": str(request.url.path),
        **_state_ids(request),
    }
    if code:
        prob["code"] = code
    if extras:
        # RFC7807 allows arbitrary extension members at top-level
        for k, v in extras.items():
            # avoid clobbering base fields
            if k not in prob:
                prob[k] = v
    return prob


def _to_status_title(status_code: int) -> Tuple[int, str]:
    # Minimal mapping to avoid importing http.HTTPStatus (smaller surface)
    titles = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        408: "Request Timeout",
        409: "Conflict",
        413: "Payload Too Large",
        415: "Unsupported Media Type",
        422: "Unprocessable Entity",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }
    return status_code, titles.get(status_code, "Error")


# --------------------------- Handlers ---------------------------


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    # Support multiple attribute shapes to keep compatibility
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "http_status", None)
        or getattr(exc, "status", 500)
    )
    code = getattr(exc, "code", exc.__class__.__name__)
    detail = getattr(exc, "detail", None) or getattr(exc, "message", str(exc)) or ""
    type_uri = getattr(exc, "type_uri", "about:blank")
    extras = getattr(exc, "extra", None) or getattr(exc, "extras", None)

    status, title = _to_status_title(int(status))
    body = _base_problem(
        request,
        status=status,
        title=title,
        detail=detail,
        type_uri=type_uri,
        code=str(code) if code is not None else None,
        extras=extras if isinstance(extras, dict) else None,
    )

    # Log at warning/error depending on status
    if status >= 500:
        log.error("api_error", **body)
    else:
        log.warning("api_error", **body)

    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CT)


async def _handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    status, title = _to_status_title(int(exc.status_code))
    detail = str(exc.detail) if getattr(exc, "detail", None) else ""
    body = _base_problem(request, status=status, title=title, detail=detail)
    # 4xx is expected; 5xx should be rare
    (log.warning if 400 <= status < 500 else log.error)("http_exception", **body)
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CT)


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    status, title = _to_status_title(422)
    errors = exc.errors()  # pydantic-style list[dict]
    body = _base_problem(
        request,
        status=status,
        title=title,
        detail="Request validation failed.",
        extras={"errors": errors},
    )
    log.warning("validation_error", **body)
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CT)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    status, title = _to_status_title(500)
    body = _base_problem(
        request,
        status=status,
        title=title,
        detail="An unexpected error occurred. Please retry or contact support with the request_id.",
    )
    # Log full exception with stack
    log.exception("unhandled_exception", **body)
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CT)


# --------------------------- Installer ---------------------------


def install_error_handlers(app: FastAPI) -> None:
    """
    Register exception handlers on the given FastAPI app.

    Usage:
        app = FastAPI()
        install_error_handlers(app)
    """
    # Our domain errors
    app.add_exception_handler(ApiError, _handle_api_error)  # type: ignore[arg-type]

    # Framework-level
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)

    # Catch-all
    app.add_exception_handler(Exception, _handle_unexpected_error)


__all__ = [
    "install_error_handlers",
    "PROBLEM_CT",
]
