from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from vm_py.runtime import events_api as _rt


# Re-export types so tests and contracts can import them from stdlib.events
Event = _rt.Event
CanonicalEvent = _rt.CanonicalEvent

__all__ = [
    "Event",
    "CanonicalEvent",
    "emit",
    "get_events",
    "events_for_receipt",
    "clear_events",
]


def _to_str_key(k: Any) -> str:
    """
    stdlib-facing keys are usually bytes; runtime-facing keys must be str.
    We accept:
      * bytes / bytearray -> ASCII decode
      * str               -> pass through
    Anything else gets stringified.
    """
    if isinstance(k, str):
        return k
    if isinstance(k, (bytes, bytearray)):
        try:
            return k.decode("ascii")
        except UnicodeDecodeError:
            # Last resort: hex-encode if not ASCII
            return k.hex()
    return str(k)


def emit(name: bytes, args: Mapping[Any, Any]) -> None:
    """
    Contract-facing emit:

        emit(b"Counter.Incremented", {b"new": 1})

    Runtime-facing API (events_api.emit) expects keys to be str.
    """
    if not isinstance(name, (bytes, bytearray)):
        raise TypeError(f"event name must be bytes, got {type(name).__name__}")

    # Normalize keys to str but leave values as-is; runtime validates values.
    converted: Dict[str, Any] = {}
    for raw_k, v in args.items():
        k = _to_str_key(raw_k)
        converted[k] = v

    _rt.emit(bytes(name), converted)


def get_events() -> List[Event]:
    return _rt.get_events()


def events_for_receipt(logs: Iterable[Event]) -> List[CanonicalEvent]:
    return _rt.events_for_receipt(logs)


def clear_events() -> None:
    _rt.clear_events()
