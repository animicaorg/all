"""
execution.types.result — ApplyResult container for transaction execution.

`ApplyResult` is the canonical, dependency-light return object produced by the
execution engine when applying a single transaction. It is intentionally minimal
and serializable to JSON-friendly structures without losing important details.

Fields
------
* status     : TxStatus — SUCCESS / REVERT / OOG
* gas_used   : int      — total gas consumed (charged)
* logs       : tuple[LogEvent, ...] — emitted events in order
* state_root : Optional[bytes] — post-state root if available (e.g., after block apply)
* receipt    : Optional[dict]  — opaque, schema owned by execution.receipts.*

Utilities
---------
* `.to_dict()` / `.from_dict()` for hex-friendly JSON conversion.
* `.is_success` convenience boolean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple, Union)

from .events import LogEvent
from .status import TxStatus

HexLike = Union[str, bytes, bytearray, memoryview]
Receipt = Dict[str, Any]  # Kept opaque here to avoid cross-module dependency.


# ------------------------------ hex helpers ---------------------------------


def _hex_to_bytes(v: HexLike) -> bytes:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)
    if isinstance(v, str):
        s = v.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if len(s) % 2:
            s = "0" + s  # tolerate odd-length hex
        try:
            return bytes.fromhex(s)
        except ValueError as e:
            raise ValueError(f"invalid hex string: {v!r}") from e
    raise TypeError(f"expected hex-like value, got {type(v).__name__}")


def _bytes_to_hex(b: Optional[bytes]) -> Optional[str]:
    if b is None:
        return None
    return "0x" + b.hex()


# ------------------------------- main type ----------------------------------


@dataclass(frozen=True)
class ApplyResult:
    """
    Result of executing a transaction.

    The dataclass is frozen for determinism. Use `.with_receipt(...)` to attach a
    receipt after the fact (returns a new instance).
    """

    status: TxStatus
    gas_used: int
    logs: Tuple[LogEvent, ...]
    state_root: Optional[bytes] = None
    receipt: Optional[Receipt] = None

    def __init__(
        self,
        *,
        status: TxStatus,
        gas_used: int,
        logs: Sequence[LogEvent] | Iterable[LogEvent] = (),
        state_root: Optional[HexLike] = None,
        receipt: Optional[Receipt] = None,
    ):
        if gas_used < 0:
            raise ValueError("gas_used must be >= 0")

        # Normalize logs to an immutable tuple
        if isinstance(logs, tuple):
            logs_t = logs
        else:
            logs_t = tuple(logs)

        # Basic sanity: ensure they are LogEvent instances
        for i, ev in enumerate(logs_t):
            if not isinstance(ev, LogEvent):
                raise TypeError(
                    f"logs[{i}] is not a LogEvent (got {type(ev).__name__})"
                )

        sr_b = (
            _hex_to_bytes(state_root)
            if isinstance(state_root, (str, bytes, bytearray, memoryview))
            else (bytes(state_root) if state_root is not None else None)
        )

        object.__setattr__(self, "status", status)
        object.__setattr__(self, "gas_used", int(gas_used))
        object.__setattr__(self, "logs", logs_t)
        object.__setattr__(self, "state_root", sr_b)
        object.__setattr__(self, "receipt", receipt)

    # ----------------------------- conveniences ------------------------------

    @property
    def is_success(self) -> bool:
        return self.status.is_success

    def with_receipt(self, receipt: Receipt) -> "ApplyResult":
        """Return a copy with `receipt` attached."""
        return ApplyResult(
            status=self.status,
            gas_used=self.gas_used,
            logs=self.logs,
            state_root=self.state_root,
            receipt=receipt,
        )

    # --------------------------- (de)serialization ---------------------------

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize to a JSON-friendly mapping.

        Example:
            {
              "status": "success",
              "gasUsed": 21000,
              "logs": [ {address/topics/data ...}, ... ],
              "stateRoot": "0x…",        # optional
              "receipt": {…}             # optional, schema owned elsewhere
            }
        """
        return {
            "status": str(self.status),
            "gasUsed": self.gas_used,
            "logs": [ev.to_dict() for ev in self.logs],
            "stateRoot": _bytes_to_hex(self.state_root),
            "receipt": self.receipt,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ApplyResult":
        status = TxStatus.from_str(str(d.get("status", "")))
        gas_used = int(d.get("gasUsed", 0))

        raw_logs = d.get("logs", [])
        if not isinstance(raw_logs, (list, tuple)):
            raise TypeError("logs must be a list/tuple")
        logs = tuple(
            LogEvent.from_dict(x) if not isinstance(x, LogEvent) else x
            for x in raw_logs
        )

        state_root = d.get("stateRoot")
        receipt = d.get("receipt")
        return cls(
            status=status,
            gas_used=gas_used,
            logs=logs,
            state_root=state_root,
            receipt=receipt,
        )

    # Pretty representation (short)
    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        sr = _bytes_to_hex(self.state_root)
        if sr and len(sr) > 18:
            sr = sr[:18] + "…"
        return (
            f"ApplyResult(status={self.status.code}, gas_used={self.gas_used}, "
            f"logs={len(self.logs)}, state_root={sr}, receipt={'yes' if self.receipt else 'no'})"
        )


__all__ = ["ApplyResult", "Receipt"]
