from __future__ import annotations

from typing import Final

from stdlib import abi, events, storage

K_COUNTER: Final[bytes] = b"counter:value"


def _load() -> int:
    raw = storage.get(K_COUNTER)
    if not raw:
        return 0
    return int.from_bytes(raw, byteorder="big", signed=True)


def _store(value: int) -> None:
    abi.require(isinstance(value, int), b"value must be int")
    storage.set(K_COUNTER, int(value).to_bytes(32, byteorder="big", signed=True))


def get() -> int:
    return _load()


def inc(by: int = 1) -> int:
    abi.require(isinstance(by, int), b"increment must be int")
    abi.require(by > 0, b"increment must be positive")
    new_value = _load() + by
    _store(new_value)
    events.emit(b"Counter.Incremented", {b"by": by, b"value": new_value})
    return new_value
