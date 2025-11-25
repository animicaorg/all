from __future__ import annotations

from typing import Any, Mapping, Optional

from .error import VmError


def _to_message(msg: Any) -> str:
    if isinstance(msg, (bytes, bytearray)):
        return msg.decode("utf-8", errors="replace")
    return str(msg)


def require(
    condition: bool,
    message: Any = "abi.require failed",
    *,
    code: str = "abi.require_failed",
    context: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    Simple assertion helper for contracts.

    Usage in contracts (as in the Counter example):

        abi.require(new <= 0xFFFFFFFFFFFFFFFF, b"counter: overflow u64")
        abi.require(isinstance(n, int), b"counter: bad type")
        abi.require(n >= 0, b"counter: negative")
    """
    if condition:
        return

    msg_str = _to_message(message)
    raise VmError(
        msg_str,
        code=code,
        context=dict(context or {}),
    )


__all__ = ["require"]
