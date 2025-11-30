"""
Counter — minimal deterministic example contract for the Animica Python VM.

Public functions:
  - inc(by: int = 1) -> int: increments the counter by `by` (must be >= 0).
  - get() -> int: returns the current counter value.

Determinism/constraints:
  - No I/O, time, randomness, or network.
  - Uses stdlib storage/events only.
  - Performs simple bounds checks via stdlib.abi.require.
"""

from stdlib.abi import require
from stdlib.events import emit
from stdlib.storage import get_int, set_int

# Storage key for the counter's value.
STORAGE_KEY = b"counter/value"

# Basic upper bound to keep arithmetic safe & testable across runtimes.
MAX_DELTA = 1_000_000_000
MAX_VALUE = 2**63 - 1  # conservative signed 64-bit ceiling for example


def _read() -> int:
    """Read current counter value (defaults to 0 if unset)."""
    v = get_int(STORAGE_KEY, default=0)
    require(0 <= v <= MAX_VALUE, "corrupt state")
    return v


def _write(v: int) -> None:
    """Persist new counter value."""
    require(0 <= v <= MAX_VALUE, "value overflow")
    set_int(STORAGE_KEY, v)


def inc(by: int = 1) -> int:
    """
    Increment the counter by `by` (non-negative).
    Returns the new value.
    """
    require(isinstance(by, int), "by must be int")
    require(0 <= by <= MAX_DELTA, "invalid 'by'")

    cur = _read()
    new = cur + by
    require(new >= cur, "overflow")  # monotonic sanity
    _write(new)

    emit(b"Inc", {"by": by, "new": new})
    return new


def get() -> int:
    """Return the current counter value."""
    return _read()
