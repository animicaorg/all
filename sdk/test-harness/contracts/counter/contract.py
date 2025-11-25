# Counter — canonical sample contract for Animica Python-VM
#
# API:
#   get() -> int
#       Return the current counter value (defaults to 0).
#
#   inc(n: int = 1) -> int
#       Increment the counter by n (default 1), emit an "Incremented" event,
#       and return the new value.
#
# Notes:
# - Uses only the VM stdlib (deterministic, no external I/O).
# - Stores the value under a single key as fixed-length big-endian bytes for
#   stable hashing and repeatability across executions.

from stdlib import storage, events  # provided by the VM at runtime

_KEY_VALUE = b"counter:value"
_INT_BYTES = 32  # fixed width for deterministic storage layout


def _load() -> int:
    raw = storage.get(_KEY_VALUE)
    if raw is None:
        return 0
    # Defensive: accept variable length; treat empty as 0.
    if len(raw) == 0:
        return 0
    return int.from_bytes(raw, "big", signed=False)


def _save(value: int) -> None:
    if value < 0:
        # Keep state non-negative and deterministic.
        value = 0
    storage.set(_KEY_VALUE, int(value).to_bytes(_INT_BYTES, "big", signed=False))


def get() -> int:
    """Return the current counter value."""
    return _load()


def inc(n: int = 1) -> int:
    """
    Increment the counter by n (default 1) and return the new value.
    Emits: events.emit(b"Incremented", {"by": n, "value": new_value})
    """
    try:
        step = int(n)
    except Exception:
        step = 1

    if step < 0:
        # Disallow negative steps to keep semantics simple.
        step = 0

    current = _load()
    new_value = current + step
    _save(new_value)

    # Emit a small, deterministic event with scalar fields.
    events.emit(b"Incremented", {"by": step, "value": new_value})
    return new_value
