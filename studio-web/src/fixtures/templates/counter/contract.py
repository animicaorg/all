# Counter contract (Python-VM)
# Deterministic, no external I/O. Uses stdlib storage/events/abi helpers.

from stdlib import storage, events, abi

# Storage key
KEY_COUNT = b"count"

def _read() -> int:
    """Internal: read current counter value (defaults to 0)."""
    return storage.get_int(KEY_COUNT, 0)

def get() -> int:
    """
    Return the current counter value.

    Returns:
        int: Current stored counter value.
    """
    return _read()

def inc(by: int = 1) -> int:
    """
    Increment the counter by `by` (must be > 0) and return the new value.

    Args:
        by (int): Positive increment amount. Defaults to 1.

    Returns:
        int: New counter value after increment.
    """
    # Deterministic precondition check
    abi.require(by > 0, b"INC_NEGATIVE_OR_ZERO")

    current = _read()
    new_value = current + by

    storage.set_int(KEY_COUNT, new_value)
    # Emit a deterministic event for indexers / UI
    events.emit(b"Inc", {b"by": by, b"value": new_value})

    return new_value

def reset(to: int = 0) -> int:
    """
    Reset the counter to an explicit non-negative value.

    Args:
        to (int): Target value (>= 0). Default 0.

    Returns:
        int: The value written.
    """
    abi.require(to >= 0, b"RESET_NEGATIVE")

    storage.set_int(KEY_COUNT, to)
    events.emit(b"Reset", {b"value": to})
    return to
