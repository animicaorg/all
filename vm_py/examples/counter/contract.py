from __future__ import annotations

"""
Simple Counter example contract for the Animica Python VM.

Public functions:

    get() -> int
        Return the current counter value (defaults to 0).

    inc() -> None
        Increment the counter by 1 and emit Counter.Incremented.

    set(n: int) -> None
        Set the counter to n (must be >= 0) and emit Counter.Set.
"""

from typing import Final

from stdlib import storage, events, abi

# Storage key for the counter value
K_COUNTER: Final[bytes] = b"counter:value"

# For safety, keep within a very wide signed 256-bit-ish range.
MIN_VALUE: Final[int] = 0
MAX_VALUE: Final[int] = 2**255 - 1


def _load() -> int:
    """Load the current counter value from storage (default 0)."""
    raw = storage.get(K_COUNTER)
    if not raw:
        return 0
    # Interpret as big-endian signed integer
    return int.from_bytes(raw, byteorder="big", signed=True)


def _store(value: int) -> None:
    """Store the counter value as 32-byte big-endian signed integer."""
    abi.require(
        isinstance(value, int),
        b"counter: value must be int",
    )
    abi.require(
        MIN_VALUE <= value <= MAX_VALUE,
        b"counter: value out of range",
    )
    storage.set(K_COUNTER, int(value).to_bytes(32, byteorder="big", signed=True))


def get() -> int:
    """Return the current counter value."""
    return _load()


def inc() -> None:
    """
    Increment the counter by 1 and emit Counter.Incremented.

    Event payload (after stdlib.events adapts keys):
        name = b"Counter.Incremented"
        args = {"new": <int>}
    """
    cur = _load()
    new = cur + 1
    _store(new)

    # Note: keys are bytes here; stdlib.events converts them to str for runtime.
    events.emit(b"Counter.Incremented", {b"new": new})


def set(n: int) -> None:
    """
    Set the counter to n (must be non-negative) and emit Counter.Set.

    Event payload:
        name = b"Counter.Set"
        args = {"value": <int>}
    """
    abi.require(isinstance(n, int), b"counter: n must be int")
    abi.require(n >= 0, b"counter: negative")

    _store(n)
    events.emit(b"Counter.Set", {b"value": n})
