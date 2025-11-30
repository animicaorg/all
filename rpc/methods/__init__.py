from __future__ import annotations

"""
rpc.methods
===========

A lightweight registry that binds JSON-RPC method names (e.g. "chain.getHead")
to Python callables.

Design goals
------------
- Simple: a dict mapping {method_name: MethodSpec}.
- Flexible: methods can declare optional Pydantic param/result models.
- Lazy: importing this package loads built-in namespaces (chain, block, tx, state, receipt).
- Safe: duplicate registrations must opt-in with replace=True.

Typical method module usage
---------------------------
from . import method

@method("chain.getHead", desc="Return the current best head header")
def get_head() -> dict:
    ...

Dispatcher integration
----------------------
The JSON-RPC dispatcher (rpc/jsonrpc.py) can:

    from rpc.methods import ensure_loaded, get_registry

    ensure_loaded()
    REG = get_registry()

    # Then REG[name].call(params) or REG[name].func(**kwargs)

Where `params` may be positional (list/tuple) or named (dict).
"""

import importlib
import inspect
import threading
import typing as t
from dataclasses import dataclass, field

try:
    # Optional types for nicer validation hooks
    from pydantic import BaseModel  # type: ignore

    PydanticModel = BaseModel
except Exception:  # pragma: no cover - optional dep

    class _Dummy:  # type: ignore
        pass

    PydanticModel = _Dummy  # type: ignore


@dataclass(frozen=True)
class MethodSpec:
    """Metadata about a JSON-RPC method binding."""

    name: str
    func: t.Callable[..., t.Any]
    desc: str | None = None
    params_model: type[PydanticModel] | None = None
    result_model: type[PydanticModel] | None = None
    namespace: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def call(self, params: t.Any = None) -> t.Any:
        """
        Call the underlying function with params that may be:
          - None
          - list/tuple (positional)
          - dict (named)
        If a params_model is set, it is applied to named params.
        If a result_model is set, the result is validated/coerced.
        """
        # Normalize params
        if params is None:
            args: tuple[t.Any, ...] = ()
            kwargs: dict[str, t.Any] = {}
        elif isinstance(params, (list, tuple)):
            args = tuple(params)
            kwargs = {}
        elif isinstance(params, dict):
            args = ()
            kwargs = params
        else:
            # Some clients send a single non-container param
            args = (params,)
            kwargs = {}

        # Validate / coerce params if a model is defined (named only)
        if self.params_model is not None and kwargs:
            validated = self.params_model(**kwargs)  # type: ignore[misc]
            # Pydantic v1 exposes .dict(); v2 exposes .model_dump()
            kwargs = getattr(
                validated, "model_dump", getattr(validated, "dict", lambda **_: {})
            )()

        result = self.func(*args, **kwargs)

        if self.result_model is not None:
            # validate/coerce result
            model = self.result_model  # type: ignore[assignment]
            if isinstance(result, dict):
                return model(**result)  # type: ignore[misc]
            return model.parse_obj(result)  # type: ignore[attr-defined]

        return result


# ---- Global registry --------------------------------------------------------

_REGISTRY: dict[str, MethodSpec] = {}
_LOADED = False
_LOCK = threading.RLock()

_BUILTIN_MODULES = (
    "rpc.methods.chain",
    "rpc.methods.block",
    "rpc.methods.tx",
    "rpc.methods.state",
    "rpc.methods.receipt",
    "rpc.methods.miner",
    "rpc.methods.marketplace",  # ANM token marketplace methods
    "rpc.methods.payments",  # Payment webhook handler for Stripe/PayPal
    "rpc.methods.quantum",  # Quantum jobs & workers explorer RPC
)


def register(
    name: str,
    func: t.Callable[..., t.Any],
    *,
    desc: str | None = None,
    params_model: type[PydanticModel] | None = None,
    result_model: type[PydanticModel] | None = None,
    aliases: t.Iterable[str] = (),
    replace: bool = False,
) -> MethodSpec:
    """Register a method callable under a JSON-RPC name."""
    if not isinstance(name, str) or "." not in name:
        raise ValueError(
            f"Method name must be namespaced like 'ns.method', got {name!r}"
        )

    namespace = name.split(".", 1)[0]

    with _LOCK:
        if name in _REGISTRY and not replace:
            raise KeyError(f"Method {name!r} is already registered")
        spec = MethodSpec(
            name=name,
            func=func,
            desc=desc or _func_desc(func),
            params_model=params_model,
            result_model=result_model,
            namespace=namespace,
            aliases=tuple(aliases or ()),
        )
        _REGISTRY[name] = spec
        for alias in spec.aliases:
            _REGISTRY[alias] = spec
        return spec


def method(
    name: str,
    *,
    desc: str | None = None,
    params_model: type[PydanticModel] | None = None,
    result_model: type[PydanticModel] | None = None,
    aliases: t.Iterable[str] = (),
    replace: bool = False,
):
    """
    Decorator to register a function as a JSON-RPC method.

    Example:
        @method("chain.getHead")
        def get_head(): ...
    """

    def _wrap(fn: t.Callable[..., t.Any]):
        register(
            name,
            fn,
            desc=desc,
            params_model=params_model,
            result_model=result_model,
            aliases=aliases,
            replace=replace,
        )
        return fn

    return _wrap


def resolve(name: str) -> MethodSpec:
    ensure_loaded()
    with _LOCK:
        spec = _REGISTRY.get(name)
        if spec is None:
            raise KeyError(f"Unknown method: {name}")
        return spec


def get_registry() -> dict[str, MethodSpec]:
    """Return a snapshot of the registry (after ensuring built-ins are loaded)."""
    ensure_loaded()
    with _LOCK:
        return dict(_REGISTRY)


def list_methods(namespace: str | None = None) -> list[str]:
    ensure_loaded()
    with _LOCK:
        names = sorted(k for k in _REGISTRY.keys() if "." in k)
        if namespace:
            names = [n for n in names if n.startswith(namespace + ".")]
        # Deduplicate aliases by unique MethodSpec identity
        uniq: dict[int, str] = {}
        for n in names:
            uniq[id(_REGISTRY[n])] = n
        return sorted(uniq.values())


def load_builtins() -> None:
    """Import built-in method modules so their @method decorators run."""
    for mod in _BUILTIN_MODULES:
        importlib.import_module(mod)


def ensure_loaded() -> None:
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        load_builtins()
        _LOADED = True


def clear_registry() -> None:
    """Testing helper: clear all registrations and mark as not loaded."""
    global _LOADED
    with _LOCK:
        _REGISTRY.clear()
        _LOADED = False


# ---- Introspection helpers --------------------------------------------------


def _func_desc(fn: t.Callable[..., t.Any]) -> str | None:
    """Return a compact one-line description for a function from its docstring/signature."""
    try:
        doc = (fn.__doc__ or "").strip().splitlines()[0].strip()
    except Exception:
        doc = ""
    sig = ""
    try:
        sig = str(inspect.signature(fn))
    except Exception:
        pass
    base = doc or f"{fn.__name__}{sig}"
    return base or None


__all__ = [
    "MethodSpec",
    "register",
    "method",
    "resolve",
    "get_registry",
    "list_methods",
    "ensure_loaded",
    "clear_registry",
]
