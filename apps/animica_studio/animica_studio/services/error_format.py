"""Error formatting helpers — ensure UI always shows readable strings."""

from __future__ import annotations

import json
import traceback
from typing import Any


def safe_str(x: Any) -> str:
    """Convert *x* to a human-readable string — never ``[object Object]``.

    * ``str`` → returned as-is.
    * ``dict`` / ``list`` → pretty JSON (2-space indent, max 2 000 chars).
    * ``Exception`` → ``format_exception(x)``.
    * Anything else → ``repr(x)`` capped at 500 chars.
    """
    if isinstance(x, str):
        return x
    if isinstance(x, (dict, list)):
        try:
            text = json.dumps(x, indent=2, ensure_ascii=False, default=_json_default)
            return text[:2000] + ("…" if len(text) > 2000 else "")
        except Exception:  # noqa: BLE001
            return repr(x)[:500]
    if isinstance(x, Exception):
        return format_exception(x)
    return repr(x)[:500]


def _json_default(obj: Any) -> Any:
    """Custom JSON encoder default handler.

    Supports:
    * ``int`` (including large Python ints) → native JSON number
    * ``bytes`` → ``"0x<hex>"``
    * Any dataclass → dict via ``dataclasses.asdict``
    * ``Decimal`` → string
    """
    import dataclasses  # noqa: PLC0415
    from decimal import Decimal  # noqa: PLC0415

    if isinstance(obj, int):
        return obj
    if isinstance(obj, bytes):
        return "0x" + obj.hex()
    if isinstance(obj, Decimal):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def safe_json_dumps(obj: Any, **kwargs: Any) -> str:
    """``json.dumps`` that handles ``int``, ``bytes``, dataclasses, ``Decimal``.

    Never raises for well-formed Python objects.
    """
    kwargs.setdefault("default", _json_default)
    return json.dumps(obj, **kwargs)


def format_exception(exc: BaseException) -> str:
    """Return a readable single-line summary of *exc*."""
    name = type(exc).__name__
    msg = str(exc).strip()
    if not msg:
        return name
    # Avoid deeply nested noise for simple messages
    if "\n" not in msg and len(msg) < 300:
        return f"{name}: {msg}"
    # Multi-line: take first line
    first_line = msg.split("\n")[0].strip()
    return f"{name}: {first_line}"


def format_exception_verbose(exc: BaseException) -> str:
    """Return a full traceback string for *exc* (use in debug bundles)."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def format_rpc_error(err_obj: Any) -> str:
    """Format a JSON-RPC error (dict, RpcError, Exception, or str) for display."""
    if isinstance(err_obj, str):
        return err_obj or "Unknown RPC error"
    if isinstance(err_obj, Exception):
        # Check for RpcResponseError with rpc_error attribute
        rpc_err = getattr(err_obj, "rpc_error", None)
        if rpc_err is not None:
            return format_rpc_error(rpc_err)
        return format_exception(err_obj)
    if hasattr(err_obj, "code") and hasattr(err_obj, "message"):
        # RpcError dataclass or similar
        code = getattr(err_obj, "code", "?")
        msg = getattr(err_obj, "message", "")
        data = getattr(err_obj, "data", None)
        base = f"RPC error {code}: {msg}"
        if data is not None:
            data_str = safe_str(data)
            if data_str and data_str not in base:
                base += f" — {data_str[:200]}"
        return base
    if isinstance(err_obj, dict):
        code = err_obj.get("code", "?")
        msg = err_obj.get("message", "")
        data = err_obj.get("data")
        base = f"RPC error {code}: {msg}"
        if data is not None:
            data_str = safe_str(data)
            if data_str and data_str not in base:
                base += f" — {data_str[:200]}"
        return base
    return safe_str(err_obj)
