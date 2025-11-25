# -*- coding: utf-8 -*-
"""
Deterministic Counter contract for Animica Python-VM.

ABI (see tests/fixtures/abi/counter.json):
- get() -> int
- inc(delta: int) -> int
- reset(value: int) -> None

Storage layout:
- key b"\x00counter" holds a signed 256-bit big-endian integer.

Notes:
- No wall-clock, randomness, or non-deterministic I/O.
- Bounds are clamped to signed 256-bit; out-of-range reverts with "Overflow".
"""
from __future__ import annotations

# Contract stdlib (provided by vm_py runtime)
from stdlib import storage, events, abi  # type: ignore


# ---- constants ---------------------------------------------------------------

_KEY = b"\x00counter"
_MIN_I256 = -(1 << 255)
_MAX_I256 = (1 << 255) - 1


# ---- helpers ----------------------------------------------------------------

def _i256_to_bytes(x: int) -> bytes:
    """Encode signed int to 32-byte big-endian two's-complement."""
    if x < _MIN_I256 or x > _MAX_I256:
        abi.revert(b"Overflow")  # matches ABI error name
    return int(x).to_bytes(32, byteorder="big", signed=True)


def _bytes_to_i256(b: bytes) -> int:
    if not b:
        return 0
    # Accept any length up to 32; left-pad to 32 for signed decode.
    if len(b) < 32:
        b = (b"\xFF" if (b[0] & 0x80) else b"\x00") * (32 - len(b)) + b
    elif len(b) > 32:
        abi.revert(b"CorruptState")
    return int.from_bytes(b, byteorder="big", signed=True)


def _load() -> int:
    """Load current counter value (defaults to 0 if unset)."""
    raw = storage.get(_KEY)  # returns bytes or b""
    return _bytes_to_i256(raw)


def _store(v: int) -> None:
    storage.set(_KEY, _i256_to_bytes(v))


# ---- public ABI functions ----------------------------------------------------

def get() -> int:
    """
    Return the current counter value.
    """
    return _load()


def inc(delta: int) -> int:
    """
    Increment the counter by `delta`, return the new value.
    Emits: Incremented(by=delta, value=new)
    """
    # Basic type/shape checks
    abi.require(isinstance(delta, int), b"BadArg")

    current = _load()
    new = current + delta
    # Enforce 256-bit signed range
    if new < _MIN_I256 or new > _MAX_I256:
        abi.revert(b"Overflow")

    _store(new)
    # Event keys/values are bytes/int only for determinism
    events.emit(b"Incremented", {b"by": int(delta), b"value": int(new)})
    return new


def reset(value: int) -> None:
    """
    Set the counter to `value` (within signed 256-bit bounds).
    """
    abi.require(isinstance(value, int), b"BadArg")
    _store(value)
