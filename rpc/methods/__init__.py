from __future__ import annotations

"""
rpc.methods
===========

Registry and loader for JSON-RPC method implementations.

This package discovers and imports method modules (rpc.methods.*) and exposes
a decorator (`@method`) for registering functions as JSON-RPC methods.

Design goals
------------
- Keep FastAPI / HTTP concerns out of this layer; we only care about JSON-RPC.
- Allow method modules to import deps lazily and fail gracefully when optional
  subsystems (state service, mempool, etc.) are not available.
- Make it easy to add new namespaces (tx.*, state.*, chain.*, account.*, etc.).
"""

import importlib
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, Optional

from rpc import errors as rpc_errors

log = logging.getLogger(__name__)

# Signature of a JSON-RPC handler function
HandlerFunc = Callable[..., Awaitable[Any]] | Callable[..., Any]


@dataclass(frozen=True)
class RpcMethod:
    name: str
    func: HandlerFunc
    desc: str | None = None
    aliases: tuple[str, ...] = ()


# Global registry: method name → RpcMethod
_METHODS: Dict[str, RpcMethod] = {}


def get_methods() -> Mapping[str, RpcMethod]:
    """Return a read-only view of the registered methods."""
    return dict(_METHODS)


def register(name: str, func: HandlerFunc, *, desc: str | None = None, aliases: Iterable[str] = ()) -> None:
    """
    Register a function as a JSON-RPC method.

    This is normally used via the @method decorator below.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("method name must be a non-empty string")
    if not callable(func):
        raise TypeError("func must be callable")

    m = RpcMethod(name=name, func=func, desc=desc, aliases=tuple(aliases))
    if name in _METHODS:
        log.warning("Overwriting existing RPC method registration: %s", name)
    _METHODS[name] = m
    for alias in m.aliases:
        if alias in _METHODS:
            log.warning("Overwriting existing RPC method alias: %s", alias)
        _METHODS[alias] = m
    log.debug("Registered RPC method %s (aliases=%s)", name, m.aliases)


def method(name: str, *, desc: str | None = None, aliases: Iterable[str] = ()) -> Callable[[HandlerFunc], HandlerFunc]:
    """
    Decorator to register a function as a JSON-RPC method.

    Example:
        @method("tx.sendRawTransaction", desc="Submit signed tx")
        def send_raw(rawTx: str) -> str:
            ...
    """

    def decorator(fn: HandlerFunc) -> HandlerFunc:
        register(name, fn, desc=desc, aliases=aliases)
        return fn

    return decorator


def _iter_builtin_modules() -> Iterable[str]:
    """
    List of builtin method modules to import.

    We keep this in one place so it's easy to add/remove namespaces.
    """
    return [
        "rpc.methods.tx",
        "rpc.methods.state",
        "rpc.methods.chain",
        "rpc.methods.account",
        "rpc.methods.marketplace",
        # "rpc.methods.payments",  # disabled: depends on consensus.PolicyProvider which may be absent
    ]


def load_builtins() -> None:
    """
    Import all builtin method modules and register their methods.

    This is idempotent; calling it multiple times is safe.
    """
    for mod in _iter_builtin_modules():
        try:
            importlib.import_module(mod)
            log.debug("Loaded RPC methods module %s", mod)
        except Exception as exc:  # noqa: BLE001
            # Log and continue; a missing optional module should not make
            # the entire RPC server unusable.
            log.error("Failed to import RPC methods module %s: %s", mod, exc)


async def dispatch(name: str, params: Any) -> Any:
    """
    Dispatch a JSON-RPC request to a registered method.

    - `name`: method name (e.g., "tx.sendRawTransaction")
    - `params`: either a list (positional) or dict (named) of parameters.

    Returns:
        The method result, or raises RpcError on failure.
    """
    if name not in _METHODS:
        raise rpc_errors.MethodNotFound(name)

    m = _METHODS[name]
    fn = m.func

    try:
        sig = inspect.signature(fn)
    except Exception:
        sig = None

    try:
        if isinstance(params, dict):
            if sig is not None:
                bound = sig.bind_partial(**params)
                args = bound.args
                kwargs = bound.kwargs
            else:
                args = ()
                kwargs = params
        elif isinstance(params, (list, tuple)):
            if sig is not None:
                bound = sig.bind_partial(*params)
                args = bound.args
                kwargs = bound.kwargs
            else:
                args = tuple(params)
                kwargs = {}
        else:
            # Single param passed as scalar
            if sig is not None and len(sig.parameters) == 1:
                args = (params,)
                kwargs = {}
            else:
                raise rpc_errors.InvalidParams(
                    f"Params must be list/tuple/dict; got {type(params).__name__}"
                )

        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)  # type: ignore[misc]
        else:
            return fn(*args, **kwargs)  # type: ignore[misc]
    except rpc_errors.RpcError:
        # Bubble up structured RPC errors as-is
        raise
    except TypeError as exc:
        # Signature mismatch or bad params
        raise rpc_errors.InvalidParams(str(exc))
    except Exception as exc:  # noqa: BLE001
        # Anything else is an internal error
        raise rpc_errors.to_error(exc)


__all__ = [
    "RpcMethod",
    "HandlerFunc",
    "get_methods",
    "register",
    "method",
    "load_builtins",
    "dispatch",
]