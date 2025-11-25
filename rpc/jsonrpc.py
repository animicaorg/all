"""
Animica RPC — JSON-RPC 2.0 Dispatcher
=====================================

Features
--------
• Full JSON-RPC 2.0 compliance: single & batch, named & positional params, notifications.
• Structured error mapping (standard codes + app-defined via rpc.errors).
• Async-aware execution; works with FastAPI (router provided).
• Safe arg binding with optional context injection ("ctx"/"context"/"request").
• Deterministic responses: {"jsonrpc":"2.0", "id":..., "result":...} or {"error":...}.

This module is framework-light and can be reused outside FastAPI. For HTTP usage,
import `router` into rpc/server.py and mount at `/rpc`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple, Union

from fastapi import APIRouter, Request, Response

try:
    # Prefer our shared error types if available
    from .errors import (
        JsonRpcError,
        InvalidRequest,
        MethodNotFound,
        InvalidParams,
        InternalError,
    )
except Exception:  # pragma: no cover - fallback if errors module not ready
    class JsonRpcError(Exception):  # type: ignore
        code: int = -32000
        message: str = "Server error"
        data: Any = None

        def __init__(self, message: Optional[str] = None, *, code: Optional[int] = None, data: Any = None):
            if message is not None:
                self.message = message
            if code is not None:
                self.code = code
            self.data = data
            super().__init__(self.message)

    class InvalidRequest(JsonRpcError):  # type: ignore
        code = -32600
        message = "Invalid Request"

    class MethodNotFound(JsonRpcError):  # type: ignore
        code = -32601
        message = "Method not found"

    class InvalidParams(JsonRpcError):  # type: ignore
        code = -32602
        message = "Invalid params"

    class InternalError(JsonRpcError):  # type: ignore
        code = -32603
        message = "Internal error"


log = logging.getLogger(__name__)

Json = Dict[str, Any]
Params = Union[List[Any], Dict[str, Any]]
CallableLike = Union[Callable[..., Any], Callable[..., Awaitable[Any]]]


# --------------------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------------------

@dataclass
class Context:
    """
    Minimal per-request context passed to methods when they accept an arg named
    'ctx' or 'context' (or 'request' for FastAPI Request).

    Extend this as needed (e.g., auth/session, rate limits).
    """
    request: Optional[Request]
    received_at_ms: int
    client: Optional[Tuple[str, int]]
    headers: Dict[str, str]


def _now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------------------
# Method registry
# --------------------------------------------------------------------------------------

class MethodRegistry:
    """
    Name → callable registry with decorator sugar.

    Methods can be sync or async. They may declare an argument named 'ctx',
    'context', or 'request' to receive the Context/Request object.
    """

    def __init__(self) -> None:
        self._methods: Dict[str, CallableLike] = {}

    def method(self, name: str) -> Callable[[CallableLike], CallableLike]:
        def deco(fn: CallableLike) -> CallableLike:
            if not isinstance(name, str) or not name:
                raise ValueError("Method name must be non-empty string")
            if name in self._methods:
                raise ValueError(f"Method already registered: {name}")
            self._methods[name] = fn
            log.debug("JSON-RPC register %s → %s.%s", name, fn.__module__, fn.__name__)
            return fn
        return deco

    def register(self, name: str, fn: CallableLike) -> None:
        self.method(name)(fn)

    def get(self, name: str) -> CallableLike:
        fn = self._methods.get(name)
        if fn is None:
            raise MethodNotFound()
        return fn

    @property
    def names(self) -> List[str]:
        return sorted(self._methods.keys())


registry = MethodRegistry()


# --------------------------------------------------------------------------------------
# Error shaping
# --------------------------------------------------------------------------------------

def _error_obj(exc: Exception) -> Json:
    """
    Convert any exception into a JSON-RPC error object.

    If `exc` is a JsonRpcError (or subclass), we preserve `.code`, `.message`,
    and include `.data` if present. Otherwise:
      • ValueError/TypeError → InvalidParams
      • Otherwise → Server error (-32000) with message
    """
    if isinstance(exc, JsonRpcError):
        err = {"code": getattr(exc, "code", -32000), "message": getattr(exc, "message", str(exc))}
        data = getattr(exc, "data", None)
        if data is not None:
            err["data"] = data
        return err

    if isinstance(exc, (ValueError, TypeError)):
        return {"code": -32602, "message": "Invalid params", "data": str(exc)}

    # Default server error
    return {"code": -32000, "message": "Server error", "data": str(exc)}


# --------------------------------------------------------------------------------------
# Arg binding & execution
# --------------------------------------------------------------------------------------

def _bind_call_args(fn: CallableLike, params: Optional[Params], ctx: Context) -> Tuple[List[Any], Dict[str, Any]]:
    """
    Bind positional/named params to `fn` using its signature. Injects context into
    parameters named 'ctx'/'context'/'request' if not provided by caller.
    """
    sig = inspect.signature(fn)

    # Normalize params
    if params is None:
        args_obj: Params = []
    else:
        args_obj = params

    try:
        if isinstance(args_obj, list):
            bound = sig.bind_partial(*args_obj)  # allow extra defaults
        elif isinstance(args_obj, dict):
            bound = sig.bind_partial(**args_obj)
        else:
            raise InvalidParams("params must be array or object")
    except TypeError as e:
        # Signature mismatch (wrong arity/unknown kw)
        raise InvalidParams(str(e))

    # Optional context injection
    for want in ("ctx", "context"):
        if want in sig.parameters and want not in bound.arguments:
            bound.arguments[want] = ctx
    if "request" in sig.parameters and "request" not in bound.arguments:
        bound.arguments["request"] = ctx.request

    # Return as arg/kw lists
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if name in bound.arguments:
            if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD) and name not in kwargs:
                # preserve order for positional-first
                args.append(bound.arguments[name])
            elif param.kind in (param.KEYWORD_ONLY, param.VAR_KEYWORD):
                kwargs[name] = bound.arguments[name]
            else:
                # default to kwargs for safety
                kwargs[name] = bound.arguments[name]
    # Include any extras that were bound (e.g., var-positional)
    for k, v in bound.arguments.items():
        if k not in sig.parameters:
            kwargs[k] = v
    return args, kwargs


async def _maybe_await(x: Any) -> Any:
    if inspect.isawaitable(x):
        return await x  # type: ignore[no-any-return]
    return x


# --------------------------------------------------------------------------------------
# Core dispatch
# --------------------------------------------------------------------------------------

def _validate_id(id_val: Any) -> Any:
    # Spec allows string, number, or null for id
    if id_val is None or isinstance(id_val, (str, int, float)):
        return id_val
    # If id is invalid type, the request itself is invalid
    raise InvalidRequest("id must be string, number, or null")


def _validate_request_obj(obj: Json) -> Tuple[str, Optional[Params], Any]:
    """
    Validate base request object; returns (method, params, id).
    Raises InvalidRequest on structural errors. Does NOT validate method existence.
    """
    if not isinstance(obj, dict):
        raise InvalidRequest("Request must be an object")

    if obj.get("jsonrpc") != "2.0":
        raise InvalidRequest("jsonrpc must be '2.0'")

    method = obj.get("method")
    if not isinstance(method, str) or not method:
        raise InvalidRequest("method must be a non-empty string")

    params: Optional[Params] = obj.get("params")
    if params is not None and not isinstance(params, (list, dict)):
        raise InvalidRequest("params, if present, must be array or object")

    # id is optional (notification when absent)
    id_present = "id" in obj
    req_id = obj.get("id") if id_present else _NO_ID
    if id_present:
        _ = _validate_id(req_id)
    return method, params, req_id


_NO_ID = object()  # sentinel for notification


async def dispatch_one(obj: Json, ctx: Context) -> Optional[Json]:
    """
    Dispatch a single JSON-RPC request object.
    Returns a response object or None (for notifications).
    """
    try:
        method_name, params, req_id = _validate_request_obj(obj)
        fn = registry.get(method_name)
        args, kwargs = _bind_call_args(fn, params, ctx)
        result = await _maybe_await(fn(*args, **kwargs))
        # Notification?
        if req_id is _NO_ID:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except Exception as exc:
        # Notification? Still no response, even on error per spec (but we *log* it)
        req_id = obj.get("id", _NO_ID)
        if req_id is _NO_ID:
            log.debug("Error in notification %s: %s", obj.get("method"), exc)
            return None
        return {"jsonrpc": "2.0", "id": req_id, "error": _error_obj(exc)}


async def dispatch(payload: Union[Json, List[Any]], ctx: Context) -> Union[Json, List[Json]]:
    """
    Dispatch a parsed JSON payload (already json.loads'ed).
    Handles single objects and batches.
    """
    if isinstance(payload, list):
        if len(payload) == 0:
            # Empty batch is invalid
            return {"jsonrpc": "2.0", "id": None, "error": _error_obj(InvalidRequest("empty batch"))}
        # Process concurrently but keep result order for request objects
        tasks = [dispatch_one(obj, ctx) if isinstance(obj, dict)
                 else asyncio.create_task(dispatch_one({"jsonrpc": "2.0", "method": "__invalid__", "id": None}, ctx))
                 for obj in payload]
        results: List[Optional[Json]] = []
        for t in tasks:
            r = await t
            results.append(r)
        # Filter out None (notifications)
        out = [r for r in results if r is not None]
        # If batch had only notifications, spec says return empty list
        return out
    elif isinstance(payload, dict):
        return await dispatch_one(payload, ctx) or {"jsonrpc": "2.0", "id": None, "result": None}
    else:
        # Entire payload invalid
        return {"jsonrpc": "2.0", "id": None, "error": _error_obj(InvalidRequest("payload must be object or array"))}


# --------------------------------------------------------------------------------------
# FastAPI router
# --------------------------------------------------------------------------------------

router = APIRouter()

@router.post("/")
async def jsonrpc_endpoint(request: Request) -> Response:
    """
    HTTP endpoint for JSON-RPC POST.
    """
    try:
        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            raise InvalidRequest("malformed JSON")
        # Build context
        client = request.client
        ctx = Context(
            request=request,
            received_at_ms=_now_ms(),
            client=(client.host, client.port) if client else None,  # type: ignore[arg-type]
            headers={k.lower(): v for k, v in request.headers.items()},
        )
        result = await dispatch(payload, ctx)
        return Response(content=json.dumps(result, separators=(",", ":")), media_type="application/json")
    except JsonRpcError as e:
        # Top-level structural errors
        err = {"jsonrpc": "2.0", "id": None, "error": _error_obj(e)}
        return Response(content=json.dumps(err, separators=(",", ":")), status_code=400, media_type="application/json")
    except Exception as e:  # pragma: no cover
        err = {"jsonrpc": "2.0", "id": None, "error": _error_obj(InternalError(str(e)))}
        return Response(content=json.dumps(err, separators=(",", ":")), status_code=500, media_type="application/json")


# --------------------------------------------------------------------------------------
# Introspection helper (optional)
# --------------------------------------------------------------------------------------

@registry.method("rpc.listMethods")
async def rpc_list_methods() -> List[str]:
    """Return the list of registered method names (for debugging/clients)."""
    return registry.names
