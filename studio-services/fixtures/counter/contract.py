"""
Canonical sample contract: Counter

This tiny, deterministic contract is used across tests and fixtures to verify:
- VM compile/run pipeline
- ABI encode/decode stability
- Event emission and log ordering
- Code-hash reproducibility

Functions
---------
- init() -> None
    Initializes the counter to zero if not present.

- get() -> int
    Returns the current counter value.

- inc(amount: int = 1) -> int
    Increments the counter by `amount` (must be >= 0), emits an `Inc` event,
    and returns the new value.

Storage layout
--------------
A single key `b"c"` holds an unsigned 64-bit big-endian integer.

Notes
-----
- Uses only the safe stdlib surface exposed by the Python VM (no I/O or imports).
- Deterministic: no time/random/network/FS access; strict integer bounds.
"""

from stdlib import storage, events, abi  # provided by vm runtime sandbox

_STORAGE_KEY = b"c"
_U64_MAX = (1 << 64) - 1


def _u64_to_bytes(x: int) -> bytes:
    if x < 0 or x > _U64_MAX:
        abi.revert(b"u64_out_of_range")
    return x.to_bytes(8, "big", signed=False)


def _bytes_to_u64(b: bytes) -> int:
    if len(b) == 0:
        return 0
    if len(b) != 8:
        abi.revert(b"bad_storage_len")
    return int.from_bytes(b, "big", signed=False)


def _read() -> int:
    raw = storage.get(_STORAGE_KEY)
    return _bytes_to_u64(raw)


def _write(v: int) -> None:
    storage.set(_STORAGE_KEY, _u64_to_bytes(v))


def init() -> None:
    """
    Initialize the counter to zero if not already set.
    Idempotent: safe to call multiple times.
    """
    raw = storage.get(_STORAGE_KEY)
    if len(raw) == 0:
        storage.set(_STORAGE_KEY, _u64_to_bytes(0))


def get() -> int:
    """Return the current counter value."""
    return _read()


def inc(amount: int = 1) -> int:
    """
    Increment the counter by `amount` (default 1). `amount` must be >= 0.
    Emits:
      events.emit(b"Inc", {b"by": amount, b"new": new_value})
    Returns:
      The new counter value as int.
    """
    if amount < 0:
        abi.revert(b"amount_negative")

    current = _read()
    new_value = current + amount
    if new_value > _U64_MAX:
        abi.revert(b"overflow")

    _write(new_value)
    events.emit(b"Inc", {b"by": amount, b"new": new_value})
    return new_value
