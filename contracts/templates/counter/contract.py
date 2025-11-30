"""
Counter (deterministic)

A tiny, production-leaning template that demonstrates the Animica Python-VM contract style:
- deterministic storage (u128 encoding)
- bounded arguments and explicit checks
- canonical events
- zero external I/O, no wall-clock time, no randomness

Public ABI:
- get() -> int
- inc(by: int = 1) -> int
"""

from stdlib import abi, events, storage

# --- Storage keys (bytes for determinism) ---
KEY_COUNT = b"counter:value"

# --- Numeric bounds (deterministic, gas-friendly) ---
U128_MAX = (1 << 128) - 1
MAX_INC = 1_000_000  # defend against silly inputs; adjust as needed


def _load_u128(key: bytes) -> int:
    """
    Read a u128 (big-endian) from storage; defaults to 0 if unset.
    """
    raw = storage.get(key)  # expected: bytes or None
    if raw is None or len(raw) == 0:
        return 0
    # Accept 1..16 bytes; left-pad to 16 for clarity if needed.
    if len(raw) > 16:
        abi.revert(b"BAD_STORED_LENGTH")
    return int.from_bytes(raw.rjust(16, b"\x00"), "big")


def _store_u128(key: bytes, value: int) -> None:
    """
    Write a u128 (big-endian) to storage.
    """
    abi.require(0 <= value <= U128_MAX, b"U128_OVERFLOW")
    storage.set(key, value.to_bytes(16, "big"))


def get() -> int:
    """
    @notice Return the current counter value.
    @return value Current u128 value.
    """
    return _load_u128(KEY_COUNT)


def inc(by: int = 1) -> int:
    """
    @notice Increase the counter by `by` and return the new value.
    @param by Amount to add (1..MAX_INC).
    @return new_value The updated counter value.
    """
    # Validate input deterministically.
    abi.require(by > 0, b"INC_MUST_BE_POSITIVE")
    abi.require(by <= MAX_INC, b"INC_TOO_LARGE")

    current = _load_u128(KEY_COUNT)
    new_value = current + by

    # Enforce u128 bound (no wraparound).
    abi.require(new_value <= U128_MAX, b"U128_OVERFLOW")

    _store_u128(KEY_COUNT, new_value)

    # Emit a canonical event; ABI encoder handles ints/bytes deterministically.
    events.emit(b"Inc", {b"by": by, b"value": new_value})

    return new_value
