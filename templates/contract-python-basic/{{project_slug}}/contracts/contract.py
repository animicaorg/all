# -*- coding: utf-8 -*-
"""
{{project_name}} — Minimal deterministic counter contract (template)

This file is the starting point for a Python-VM smart contract in Animica.
It is intentionally small, strictly deterministic, and uses only the allowed
stdlib surface exposed by the VM at runtime:

    from stdlib import storage, events, abi

Design goals
------------
- Determinism first: no wall-clock, no randomness, no I/O, no network.
- Simple storage model: key→bytes with explicit encoding via abi helpers.
- Clear ABI: docstrings describe functions, inputs/outputs, and mutability.
- Safe math: bounded to 63-bit signed range in this template (customize as needed).

How to use
----------
1) Adapt keys/state layout if needed.
2) Add/modify functions, keeping the ABI docstrings in-sync.
3) Build/package/deploy with the provided Makefile targets in this template.

Storage layout (keys)
---------------------
- b"init"      : presence indicates contract was initialized
- b"owner"     : 32-byte address blob (optional owner concept for demo)
- b"counter"   : big-endian encoded integer (via abi.encode_int)
"""
from __future__ import annotations

from stdlib import storage, events, abi  # provided by the VM at runtime

VERSION = b"0.1.0"

# Storage keys (bytes)
KEY_INIT = b"init"
KEY_OWNER = b"owner"
KEY_COUNTER = b"counter"

# Bounds for "safe" arithmetic in this template (customize as needed).
I63_MIN = -(2**62)            # generous negative lower bound for demo
I63_MAX = (2**62) - 1         # keep large enough while avoiding edge overflow


# ---------------------------------------------------------------------------
# Internal helpers (bytes <-> int) with explicit ABI encoding/decoding
# ---------------------------------------------------------------------------

def _load_int(key: bytes, default: int = 0) -> int:
    """Read an integer from storage at `key`. If absent, return `default`."""
    data = storage.get(key)  # returns bytes or None
    if data is None:
        return default
    return abi.decode_int(data)


def _store_int(key: bytes, value: int) -> None:
    """Store an integer (ABI-encoded) at `key`."""
    storage.set(key, abi.encode_int(value))


def _clamp_i63(x: int) -> int:
    """Clamp integer to the template's i63 range."""
    if x < I63_MIN:
        return I63_MIN
    if x > I63_MAX:
        return I63_MAX
    return x


def _require_initialized() -> None:
    if storage.get(KEY_INIT) is None:
        abi.revert(b"not-initialized")


# ---------------------------------------------------------------------------
# Constructor / initialization
# ---------------------------------------------------------------------------

def init(initial: int = 0, owner: bytes | None = None) -> int:
    """
    Initialize the contract once. Sets the counter and optionally an owner.

    ABI:
      name: init
      inputs:
        - name: initial
          type: int
        - name: owner
          type: bytes   # optional owner for admin operations (may be null/empty)
      outputs:
        - name: value
          type: int
      mutability: write

    Notes:
      - Calling `init` again reverts.
      - If `owner` is omitted or empty, admin-only calls will require a matching
        explicit `who` argument equal to empty bytes (discouraged). Prefer to set.
    """
    if storage.get(KEY_INIT) is not None:
        abi.revert(b"already-initialized")

    # Normalize owner (allow None/empty)
    owner_bytes = owner or b""
    storage.set(KEY_OWNER, owner_bytes)

    # Clamp and store initial counter
    _store_int(KEY_COUNTER, _clamp_i63(int(initial)))

    # Mark initialized
    storage.set(KEY_INIT, b"1")

    # Emit Init event (purely informational)
    events.emit(
        b"Init",
        {
            b"owner": owner_bytes,
            b"value": _load_int(KEY_COUNTER, 0),
            b"version": VERSION,
        },
    )
    return _load_int(KEY_COUNTER, 0)


# ---------------------------------------------------------------------------
# Read-only views
# ---------------------------------------------------------------------------

def version() -> bytes:
    """
    Return the semantic version of this contract template.

    ABI:
      name: version
      inputs: []
      outputs:
        - name: semver
          type: bytes
      mutability: read
    """
    return VERSION


def get() -> int:
    """
    Return the current counter value.

    ABI:
      name: get
      inputs: []
      outputs:
        - name: value
          type: int
      mutability: read
    """
    _require_initialized()
    return _load_int(KEY_COUNTER, 0)


def get_owner() -> bytes:
    """
    Return the configured owner bytes (may be empty).

    ABI:
      name: get_owner
      inputs: []
      outputs:
        - name: owner
          type: bytes
      mutability: read
    """
    _require_initialized()
    data = storage.get(KEY_OWNER)
    return data if data is not None else b""


# ---------------------------------------------------------------------------
# State-changing operations
# ---------------------------------------------------------------------------

def inc(delta: int = 1) -> int:
    """
    Increment the counter by `delta` (>= 0). Emits an `Inc` event.

    ABI:
      name: inc
      inputs:
        - name: delta
          type: int
      outputs:
        - name: value
          type: int
      mutability: write
      events:
        - Inc(before:int, after:int, delta:int)

    Safety:
      - Negative deltas revert.
      - Value is clamped to the i63 bounds of this template.
    """
    _require_initialized()
    if delta < 0:
        abi.revert(b"delta-must-be-nonnegative")

    before = _load_int(KEY_COUNTER, 0)
    after = _clamp_i63(before + int(delta))

    _store_int(KEY_COUNTER, after)

    events.emit(
        b"Inc",
        {b"before": before, b"after": after, b"delta": int(delta)},
    )
    return after


def dec(delta: int = 1) -> int:
    """
    Decrement the counter by `delta` (>= 0). Emits a `Dec` event.

    ABI:
      name: dec
      inputs:
        - name: delta
          type: int
      outputs:
        - name: value
          type: int
      mutability: write
      events:
        - Dec(before:int, after:int, delta:int)

    Safety:
      - Negative deltas revert.
      - Value is clamped to the i63 bounds of this template.
    """
    _require_initialized()
    if delta < 0:
        abi.revert(b"delta-must-be-nonnegative")

    before = _load_int(KEY_COUNTER, 0)
    after = _clamp_i63(before - int(delta))

    _store_int(KEY_COUNTER, after)

    events.emit(
        b"Dec",
        {b"before": before, b"after": after, b"delta": int(delta)},
    )
    return after


def set_value(value: int, who: bytes) -> int:
    """
    Set the counter to `value`. Requires `who` to match stored owner.

    ABI:
      name: set_value
      inputs:
        - name: value
          type: int
        - name: who
          type: bytes
      outputs:
        - name: new_value
          type: int
      mutability: write
      events:
        - Set(before:int, after:int, who:bytes)

    Notes:
      - In the chain VM, the caller/tx-sender is provided by the TxEnv.
        This template uses an explicit `who` parameter for clarity and
        to remain runnable in local VM simulations without account context.
    """
    _require_initialized()

    owner = get_owner()
    if owner != who:
        abi.revert(b"unauthorized")

    before = _load_int(KEY_COUNTER, 0)
    after = _clamp_i63(int(value))

    _store_int(KEY_COUNTER, after)
    events.emit(b"Set", {b"before": before, b"after": after, b"who": who})
    return after


# ---------------------------------------------------------------------------
# Optional: tiny metadata helper (useful in tooling & explorers)
# ---------------------------------------------------------------------------

def metadata() -> bytes:
    """
    Return a small JSON blob (bytes) with contract metadata.

    ABI:
      name: metadata
      inputs: []
      outputs:
        - name: json
          type: bytes
      mutability: read
    """
    # Construct a minimal JSON object without importing json (keeps stdlib tight).
    # Keys/values are ASCII-safe; concatenation is deterministic.
    owner_hex = abi.hex(get_owner())  # e.g. b"0x..."
    value = str(get()).encode("ascii")
    return (
        b'{"name":"Counter","version":"' + VERSION + b'","owner":"' +
        owner_hex + b'","value":' + value + b"}"
    )


# Public symbols (helps static tools)
__all__ = [
    "init",
    "version",
    "get",
    "get_owner",
    "inc",
    "dec",
    "set_value",
    "metadata",
]
