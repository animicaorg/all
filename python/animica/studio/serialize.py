"""Packing a call (function + args) for transport, and unpacking its result.

Two modes, mirroring the trust trade-off discussed in ARCHITECTURE.md:

* ``ref`` (default, safe) — the function is identified by ``module:qualname`` and
  arguments are JSON. The worker *imports* the function from shipped source and
  calls it. No arbitrary pickle is executed. This is the right default for an
  untrusted, decentralized provider fleet.

* ``pickle`` (opt-in) — the whole ``(fn, args, kwargs)`` is ``cloudpickle``-d.
  This supports closures/lambdas/locals like Modal, but means a provider executes
  arbitrary pickled code, so it's only appropriate once the sandbox is hardened
  (gVisor/Firecracker). Selected with ``serialization="pickle"`` on the function,
  or automatically as a fallback when ``ref`` can't represent the call.

The packed call is a plain JSON-able dict so the registry/broker/provider can
route it without importing this module or the user's code.
"""

from __future__ import annotations

import base64
import inspect
import json
from typing import Any, Callable, Dict, Optional, Tuple

from .errors import SerializationError

SPEC_VERSION = 1
_RESULT_SENTINEL = "__ANIMICA_STUDIO_RESULT__"
_ERROR_SENTINEL = "__ANIMICA_STUDIO_ERROR__"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _json_safe(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def _read_source(fn: Callable) -> Tuple[Optional[str], Optional[str]]:
    """Return (source_text, filename) of the module that defines ``fn``.

    Shipping the defining file lets a worker import a ``__main__``/script function
    that isn't on its import path. Best-effort: returns (None, None) if source
    can't be read (e.g. a C function or an interactive session).
    """
    try:
        src_file = inspect.getsourcefile(fn) or inspect.getfile(fn)
    except (TypeError, OSError):
        return None, None
    if not src_file:
        return None, None
    try:
        with open(src_file, "r", encoding="utf-8") as fh:
            return fh.read(), src_file.rsplit("/", 1)[-1]
    except OSError:
        return None, None


def pack_call(
    fn: Callable,
    args: tuple,
    kwargs: dict,
    *,
    mode: str = "auto",
    ship_source: bool = True,
) -> Dict[str, Any]:
    """Pack a callable + arguments into a transport dict.

    ``mode`` is ``"auto"`` (prefer ref, fall back to pickle), ``"ref"``, or
    ``"pickle"``.
    """
    entrypoint = f"{getattr(fn, '__module__', '?')}:{getattr(fn, '__qualname__', getattr(fn, '__name__', '?'))}"
    nested = "." in getattr(fn, "__qualname__", "") and "<locals>" in getattr(fn, "__qualname__", "")
    can_ref = (not nested) and _json_safe(list(args)) and _json_safe(dict(kwargs))

    if mode == "ref" and not can_ref:
        raise SerializationError(
            f"Cannot ref-serialize {entrypoint}: arguments are not JSON-serializable "
            f"or the function is a closure/lambda. Use serialization='pickle'."
        )

    use_pickle = mode == "pickle" or (mode == "auto" and not can_ref)

    if use_pickle:
        try:
            import cloudpickle  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise SerializationError(
                "pickle serialization needs cloudpickle (`pip install cloudpickle`). "
                "Or make the call ref-serializable (top-level function + JSON args)."
            ) from exc
        blob = cloudpickle.dumps((fn, args, kwargs))
        return {
            "version": SPEC_VERSION,
            "mode": "pickle",
            "entrypoint": entrypoint,
            "pickle_b64": _b64(blob),
        }

    source, filename = (_read_source(fn) if ship_source else (None, None))
    return {
        "version": SPEC_VERSION,
        "mode": "ref",
        "entrypoint": entrypoint,
        "args": list(args),
        "kwargs": dict(kwargs),
        "source_b64": _b64(source.encode("utf-8")) if source else None,
        "source_filename": filename,
    }


def dumps(spec: Dict[str, Any]) -> str:
    return json.dumps(spec, separators=(",", ":"))


def loads(blob: str) -> Dict[str, Any]:
    try:
        return json.loads(blob)
    except (TypeError, ValueError) as exc:
        raise SerializationError(f"corrupt call spec: {exc}") from exc


# ---- results ------------------------------------------------------------------


def pack_result(value: Any) -> Dict[str, Any]:
    """Pack a return value, JSON if possible else pickle."""
    if _json_safe(value):
        return {"kind": "json", "value": value}
    try:
        import cloudpickle  # type: ignore
        return {"kind": "pickle", "value": _b64(cloudpickle.dumps(value))}
    except ImportError:  # pragma: no cover
        return {"kind": "repr", "value": repr(value)}


def unpack_result(packed: Dict[str, Any]) -> Any:
    kind = packed.get("kind")
    if kind == "json":
        return packed["value"]
    if kind == "pickle":
        import cloudpickle  # type: ignore
        return cloudpickle.loads(_unb64(packed["value"]))
    if kind == "repr":
        return packed["value"]
    raise SerializationError(f"unknown result kind: {kind!r}")


# sentinels the in-sandbox wrapper prints on stdout, parsed by the runner.
RESULT_SENTINEL = _RESULT_SENTINEL
ERROR_SENTINEL = _ERROR_SENTINEL
b64encode = _b64
b64decode = _unb64
