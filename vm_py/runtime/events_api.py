from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence

from .error import VmError

# Basic bounds (kept generous; tests only check that we *validate*).
MAX_EVENT_NAME_BYTES = 64
MAX_KEY_LEN = 64
MAX_BYTES_LEN = 4096
MAX_INT_BITS = 256  # signed range ~[-2^255, 2^255-1]

# Keys must be identifier-like: letters/underscore, then letters/digits/underscore.
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ArgValue = Any  # constrained at runtime


@dataclass
class Event:
    """In-VM representation of an emitted event."""

    name: bytes
    args: Dict[str, ArgValue]


@dataclass
class CanonicalEvent:
    """
    Canonical event representation for receipts:

        name: "0x" + hex-encoded event name bytes
        args: sequence of {"k", "t", "v"} dicts
              t="b" => bytes encoded as 0x-prefixed hex
              t="i" => integer
              t="z" => boolean
    """

    name: str
    args: Sequence[Mapping[str, Any]]


class _EventSink:
    def __init__(self) -> None:
        self._events: List[Event] = []

    # --- Validation helpers -------------------------------------------------

    def _check_name(self, name: Any) -> bytes:
        if not isinstance(name, (bytes, bytearray)):
            raise VmError(
                "event name must be bytes",
                code="event_invalid",
                context={"where": "name_type"},
            )
        b = bytes(name)
        if len(b) == 0:
            raise VmError(
                "event name must be non-empty",
                code="event_invalid",
                context={"where": "name_empty"},
            )
        if len(b) > MAX_EVENT_NAME_BYTES:
            raise VmError(
                "event name too long",
                code="event_invalid",
                context={"where": "name_length", "len": len(b)},
            )
        return b

    def _check_key(self, key: Any) -> str:
        if not isinstance(key, str):
            raise VmError(
                "event key must be str",
                code="event_invalid",
                context={"where": "key_type"},
            )
        if len(key) == 0:
            raise VmError(
                "event key must be non-empty",
                code="event_invalid",
                context={"where": "key_empty"},
            )
        if len(key) > MAX_KEY_LEN:
            raise VmError(
                "event key too long",
                code="event_invalid",
                context={"where": "key_length", "len": len(key)},
            )
        if not _KEY_RE.match(key):
            # Fail for characters like '-', '!', spaces, etc.
            raise VmError(
                "event key has invalid characters",
                code="event_invalid",
                context={"where": "key_grammar", "key": key},
            )
        return key

    def _check_value(self, value: Any) -> ArgValue:
        if isinstance(value, (bytes, bytearray)):
            b = bytes(value)
            if len(b) > MAX_BYTES_LEN:
                raise VmError(
                    "event bytes arg too long",
                    code="event_invalid",
                    context={"where": "value_bytes_length", "len": len(b)},
                )
            return b

        if isinstance(value, bool):
            # bool is a subclass of int, so check it before int.
            return value

        if isinstance(value, int):
            if value.bit_length() > MAX_INT_BITS:
                raise VmError(
                    "event int arg out of range",
                    code="event_invalid",
                    context={"where": "value_int_bits", "bits": value.bit_length()},
                )
            return int(value)

        raise VmError(
            "unsupported event arg type",
            code="event_invalid",
            context={"where": "value_type", "py_type": type(value).__name__},
        )

    # --- Core sink operations -----------------------------------------------

    def clear(self) -> None:
        self._events.clear()

    def emit(self, name: bytes, args: Mapping[Any, Any]) -> None:
        bname = self._check_name(name)

        if not isinstance(args, Mapping):
            raise VmError(
                "event args must be a mapping",
                code="event_invalid",
                context={"where": "args_type"},
            )

        checked_args: Dict[str, ArgValue] = {}
        for raw_k, raw_v in args.items():
            k = self._check_key(raw_k)
            v = self._check_value(raw_v)
            checked_args[k] = v

        self._events.append(Event(bname, checked_args))

    def iter_events(self) -> Iterable[Event]:
        # Expose a stable snapshot
        return tuple(self._events)


# Global in-memory sink used by the current interpreter.
_sink = _EventSink()


# --- Public API -------------------------------------------------------------


def emit(name: bytes, args: Mapping[Any, Any]) -> None:
    _sink.emit(name, args)


def get_events() -> List[Event]:
    return list(_sink.iter_events())


def events_for_receipt() -> List[CanonicalEvent]:
    """
    Convert the current event sink into canonical receipt events.
    """
    out: List[CanonicalEvent] = []
    for ev in _sink.iter_events():
        enc_args: List[Dict[str, Any]] = []
        for k, v in ev.args.items():
            if isinstance(v, (bytes, bytearray)):
                enc_args.append({"k": k, "t": "b", "v": "0x" + bytes(v).hex()})
            elif isinstance(v, bool):
                enc_args.append({"k": k, "t": "z", "v": v})
            elif isinstance(v, int):
                enc_args.append({"k": k, "t": "i", "v": int(v)})
            else:
                # Should not happen if _check_value did its job, but keep
                # the guard for robustness.
                raise VmError(
                    "unsupported event arg type in receipt",
                    code="event_invalid",
                    context={
                        "where": "receipt_value_type",
                        "py_type": type(v).__name__,
                    },
                )

        out.append(
            CanonicalEvent(
                name="0x" + ev.name.hex(),
                args=tuple(enc_args),
            )
        )
    return out


def clear_events() -> None:
    _sink.clear()


def _reset_events() -> None:
    """
    Test helper: fully clear the global event sink.
    """
    clear_events()


# Some tests import VmError from this module directly.
VmError = VmError  # re-export

__all__ = [
    "Event",
    "CanonicalEvent",
    "emit",
    "get_events",
    "events_for_receipt",
    "clear_events",
    "_reset_events",
    "VmError",
    "MAX_EVENT_NAME_BYTES",
    "MAX_KEY_LEN",
    "MAX_BYTES_LEN",
    "MAX_INT_BITS",
]
