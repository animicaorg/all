from __future__ import annotations

"""
events_api — deterministic event capture for browser simulations.

Goals
-----
- Minimal, dependency-free event/log sink for the in-browser Python VM.
- Deterministic encoding (stable ordering, canonical hashing).
- Strict input validation to avoid nondeterminism across platforms.
- Friendly introspection helpers for UIs (hex-encode bytes, keep ints as ints).

Model
-----
Each emitted event is a record:
  - index:   sequential integer (0-based)
  - name:    bytes (non-empty, <= 64 bytes)
  - args:    dict[str, scalar] with stable key order
             scalar ∈ { int (0..2^256-1), bool, bytes (<= 64 KiB) }
  - topic:   sha3_256(name || 0x01 || canonical_args_json_bytes)

The "topic" is a deterministic digest to quickly group event types in the UI.
This is a simulator; no bloom filters or chain receipts are produced here.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from ..errors import ValidationError
from . import hash_api


# ---------------- Limits ----------------

MAX_NAME_LEN = 64
MAX_ARG_VALUE_BYTES = 64 * 1024  # 64 KiB
U256_MAX = (1 << 256) - 1
MAX_EVENTS_DEFAULT = 10_000


# ---------------- Helpers ----------------

def _ensure_name(name: bytes) -> bytes:
    if not isinstance(name, (bytes, bytearray)) or len(name) == 0:
        raise ValidationError("event name must be non-empty bytes")
    if len(name) > MAX_NAME_LEN:
        raise ValidationError(f"event name too long ({len(name)} > {MAX_NAME_LEN})")
    return bytes(name)


def _ensure_arg_key(k: str) -> str:
    if not isinstance(k, str) or not k:
        raise ValidationError("event arg key must be non-empty str")
    if len(k) > 128:
        raise ValidationError("event arg key too long")
    return k


def _ensure_arg_val(v: Any) -> Tuple[str, Any]:
    """
    Validate and normalize value to a JSON-friendly deterministic form:
      - int -> int (must be 0..2^256-1)
      - bool -> bool
      - bytes/bytearray -> hex string "0x..."
    Returns a pair (kind, normalized_value) where kind ∈ {"int","bool","bytes"}.
    """
    if isinstance(v, bool):  # bool is subclass of int; check first
        return "bool", v
    if isinstance(v, int):
        if v < 0 or v > U256_MAX:
            raise ValidationError("event int arg out of range (must fit u256)")
        return "int", v
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        if len(b) > MAX_ARG_VALUE_BYTES:
            raise ValidationError("event bytes arg too large")
        return "bytes", "0x" + b.hex()
    raise ValidationError(f"unsupported event arg type: {type(v)}")


def _canonical_args(obj: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """
    Return a list of (key, normalized_value) pairs sorted by key, where
    values are JSON-serializable and deterministic.
    """
    pairs: List[Tuple[str, Any]] = []
    for k, v in obj.items():
        k = _ensure_arg_key(k)
        _kind, nv = _ensure_arg_val(v)
        pairs.append((k, nv))
    pairs.sort(key=lambda kv: kv[0])
    return pairs


def _canonical_args_bytes(args_sorted: List[Tuple[str, Any]]) -> bytes:
    """
    Deterministic, dependency-free encoding for args:
      JSON with separators=(',',':') and the already-sorted list of pairs.
    Example shape: {"a":1,"b":"0x..","flag":true}
    """
    # Avoid importing json at module import time under Pyodide; import lazily.
    import json

    d = {k: v for (k, v) in args_sorted}
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


# ---------------- Data Types ----------------

@dataclass(slots=True)
class EventRecord:
    index: int
    name: bytes
    args_sorted: Tuple[Tuple[str, Any], ...]  # canonical (key, normalized_value)
    topic: bytes

    def to_dict(self) -> Dict[str, Any]:
        # Presentable for UIs
        return {
            "index": self.index,
            "name": _hex(self.name),
            "topic": _hex(self.topic),
            "args": {k: v for (k, v) in self.args_sorted},  # stable order preserved in Py>=3.7
        }


# ---------------- Sink ----------------

class EventSink:
    """
    In-memory deterministic event sink.

    API
    ---
    - emit(name: bytes, args: dict[str, scalar]) -> EventRecord
    - list() -> List[EventRecord] (copy)
    - clear() -> None
    - extend(records: Iterable[EventRecord]) -> None
    - __len__() -> int
    - to_dicts() -> List[dict]  # UI-friendly
    """

    __slots__ = ("_events", "_max_events")

    def __init__(self, *, max_events: int = MAX_EVENTS_DEFAULT) -> None:
        if not isinstance(max_events, int) or max_events <= 0:
            raise ValidationError("max_events must be positive int")
        self._events: List[EventRecord] = []
        self._max_events = int(max_events)

    # ---- Core ----

    def emit(self, name: bytes, args: Dict[str, Any]) -> EventRecord:
        if len(self._events) >= self._max_events:
            raise ValidationError("event sink is full")
        name_b = _ensure_name(name)
        args_sorted = tuple(_canonical_args(args))
        topic = hash_api.sha3_256(name_b + b"\x01" + _canonical_args_bytes(list(args_sorted)))
        rec = EventRecord(index=len(self._events), name=name_b, args_sorted=args_sorted, topic=topic)
        self._events.append(rec)
        return rec

    def list(self) -> List[EventRecord]:
        # Copy to avoid external mutation
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()

    def extend(self, records: Iterable[EventRecord]) -> None:
        for r in records:
            if not isinstance(r, EventRecord):
                raise ValidationError("extend() expects EventRecord elements")
            if len(self._events) >= self._max_events:
                raise ValidationError("event sink is full")
            # Reindex to keep monotonic sequence in this sink
            self._events.append(EventRecord(
                index=len(self._events),
                name=r.name,
                args_sorted=r.args_sorted,
                topic=r.topic,
            ))

    # ---- Introspection ----

    def __len__(self) -> int:
        return len(self._events)

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._events]


__all__ = ["EventRecord", "EventSink"]
