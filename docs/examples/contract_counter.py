"""
Counter Contract (Python-VM example)

Matches docs/examples/abi_counter.json:

- get() -> int
- inc(by: int) -> int
- reset(to: int) -> int

Deterministic, storage-backed counter with tiny overflow guards and canonical
event emission. Uses only the VM stdlib surface (storage/events/abi).

Storage layout
--------------
- key b"counter" : signed 256-bit big-endian integer

Events
------
- b"Incremented" with args {"by": int, "value": int}
- b"Reset"       with args {"value": int}

Notes
-----
- Integers are clamped to signed 256-bit range to remain portable.
- The contract is intentionally minimal: no constructor, no access control.
"""

from typing import Dict, Any

from stdlib import storage, events, abi  # Provided by the Animica Python-VM

# Signed 256-bit bounds (portable deterministic range)
INT256_MIN = -(1 << 255)
INT256_MAX = (1 << 255) - 1

# Canonical storage key
KEY_COUNTER = b"counter"


# ---------- Internal helpers ----------

def _clamp_int256(x: int) -> int:
    if x < INT256_MIN or x > INT256_MAX:
        # Mirror ABI error name "Overflow"/"Underflow" conceptually
        abi.revert(b"INT256_OUT_OF_RANGE")
    return x


def _to_bytes_i256(x: int) -> bytes:
    """Encode signed 256-bit big-endian (fixed 32 bytes)."""
    x = _clamp_int256(x)
    return int(x).to_bytes(32, byteorder="big", signed=True)


def _from_bytes_i256(b: bytes) -> int:
    if not b:
        return 0
    if len(b) != 32:
        # Defensive: migrate unknown sizes by parsing as signed big-endian anyway
        # but disallow pathological lengths to keep determinism/simple gas bounds.
        abi.revert(b"BAD_ENCODED_INT")
    return int.from_bytes(b, byteorder="big", signed=True)


def _load_counter() -> int:
    return _from_bytes_i256(storage.get(KEY_COUNTER))


def _save_counter(v: int) -> None:
    storage.set(KEY_COUNTER, _to_bytes_i256(v))


# ---------- Public contract API ----------

def get() -> int:
    """
    Return the current counter value.

    ABI:
      name: get
      inputs: []
      outputs: [int]
      stateMutability: view
    """
    return _load_counter()


def inc(by: int) -> int:
    """
    Increase the counter by `by` (must be >= 1). Emits Incremented(by, value).

    ABI:
      name: inc
      inputs: [by: int]
      outputs: [newValue: int]
      eventsEmitted: ["Incremented"]
      stateMutability: nonpayable
    """
    # Validate input
    if not isinstance(by, int):
        abi.revert(b"TYPE_INT_REQUIRED")
    if by < 1:
        abi.revert(b"BY_MUST_BE_POSITIVE")

    current = _load_counter()
    # Overflow-safe add in the defined range
    new_value = _clamp_int256(current + by)

    _save_counter(new_value)
    events.emit(b"Incremented", {"by": by, "value": new_value})
    return new_value


def reset(to: int) -> int:
    """
    Set the counter to an exact value. Emits Reset(value).

    ABI:
      name: reset
      inputs: [to: int]
      outputs: [value: int]
      eventsEmitted: ["Reset"]
      stateMutability: nonpayable

    NOTE: This sample is intentionally permissionless to keep the example small.
    A production variant could gate this on an owner address stored at deploy.
    """
    if not isinstance(to, int):
        abi.revert(b"TYPE_INT_REQUIRED")

    value = _clamp_int256(to)
    _save_counter(value)
    events.emit(b"Reset", {"value": value})
    return value


# ---------- Optional: metadata helpers (no-op for VM, useful for tooling) ----------

def __abi__() -> Dict[str, Any]:
    """Optional hint for off-chain tooling/tests; the chain uses the manifest/ABI file."""
    return {
        "name": "Counter",
        "version": "1.0.0",
        "functions": [
            {"name": "get", "inputs": [], "outputs": ["int"], "stateMutability": "view"},
            {"name": "inc", "inputs": ["int"], "outputs": ["int"], "stateMutability": "nonpayable"},
            {"name": "reset", "inputs": ["int"], "outputs": ["int"], "stateMutability": "nonpayable"},
        ],
        "events": [
            {"name": "Incremented", "inputs": ["int", "int"]},
            {"name": "Reset", "inputs": ["int"]},
        ],
    }
