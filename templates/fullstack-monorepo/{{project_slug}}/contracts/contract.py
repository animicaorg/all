# Animica VM (Python subset) — Sample Counter Contract
#
# This file is a tiny, production-minded starter you can keep or replace.
# It follows the deterministic subset guidelines (no I/O, no randomness,
# no floating point, no network access, no non-deterministic libs).
#
# The VM is expected to inject a minimal `ctx` object with:
#   - ctx.sender: bytes          # caller address
#   - ctx.storage_get(key: bytes) -> bytes | None
#   - ctx.storage_set(key: bytes, value: bytes) -> None
#   - ctx.emit(event_name: str, data: bytes) -> None     # optional
#
# If your VM bindings differ, adapt the 3 storage helpers at the bottom.
# Keep types primitive: ints, bytes, and fixed-length abi-compatible encodings.

from typing import Final, Optional, Tuple


# -------------------------
# Storage keys (namespaced)
# -------------------------
_NS: Final = b"counter:"
KEY_OWNER: Final = _NS + b"owner"
KEY_VALUE: Final = _NS + b"value"
KEY_VERSION: Final = _NS + b"version"


# -------------------------
# ABI (docstring-based)
# -------------------------
def init(ctx, owner: Optional[bytes] = None, version: int = 1) -> None:
    """
    Initialize the contract.

    Args:
        owner: bytes (optional) — owner address. If omitted, defaults to ctx.sender.
        version: uint32 — semantic version for migrations/clients.

    Effects:
        - Sets owner if not yet initialized.
        - Initializes counter to 0 if unset.
        - Writes version.

    Reverts:
        - If already initialized (owner is present).

    ABI:
        function init(owner: bytes32?, version: uint32)
    """
    if _exists(ctx, KEY_OWNER):
        _revert("already-initialized")

    _set_owner(ctx, owner if owner is not None else ctx.sender)
    _set_value(ctx, 0)
    _set_version(ctx, version)

    # Optional event (no-op if ctx.emit unavailable)
    _emit(ctx, "Initialized", _abi_pack_owner_version(_get_owner(ctx), version))


def inc(ctx, delta: int) -> int:
    """
    Increment the counter by `delta` and return the new value.

    Args:
        delta: uint64 — positive increment.

    Returns:
        value: uint64 — new counter value.

    Reverts:
        - If delta <= 0.

    ABI:
        function inc(delta: uint64) returns (value: uint64)
    """
    if delta <= 0:
        _revert("delta-must-be-positive")

    v = _get_value(ctx)
    new_v = v + delta
    _set_value(ctx, new_v)

    _emit(ctx, "Incremented", _abi_pack_u64_pair(delta, new_v))
    return new_v


def get(ctx) -> int:
    """
    Read the current counter value.

    Returns:
        value: uint64

    ABI:
        function get() returns (value: uint64)
    """
    return _get_value(ctx)


def reset(ctx, value: int) -> int:
    """
    Owner-only: set the counter to an exact value.

    Args:
        value: uint64

    Returns:
        value: uint64 — the value that was set.

    Reverts:
        - If caller is not owner.

    ABI:
        function reset(value: uint64) returns (value: uint64)
    """
    _only_owner(ctx)
    if value < 0:
        _revert("value-negative")
    _set_value(ctx, value)
    _emit(ctx, "Reset", _abi_pack_u64(value))
    return value


def owner(ctx) -> bytes:
    """
    Read the owner address.

    Returns:
        owner: bytes32

    ABI:
        function owner() returns (owner: bytes32)
    """
    return _get_owner(ctx)


def transfer_ownership(ctx, new_owner: bytes) -> bytes:
    """
    Owner-only: transfer ownership.

    Args:
        new_owner: bytes32

    Returns:
        owner: bytes32 — the new owner.

    Reverts:
        - If caller is not owner.
        - If new_owner is empty (len 0).

    ABI:
        function transfer_ownership(new_owner: bytes32) returns (owner: bytes32)
    """
    _only_owner(ctx)
    if not isinstance(new_owner, (bytes, bytearray)) or len(new_owner) == 0:
        _revert("bad-new-owner")

    _set_owner(ctx, new_owner)
    _emit(ctx, "OwnershipTransferred", _abi_pack_owner_pair(ctx.sender, new_owner))
    return new_owner


# -------------------------
# Internal helpers (storage)
# -------------------------
def _exists(ctx, key: bytes) -> bool:
    return ctx.storage_get(key) is not None


def _get_owner(ctx) -> bytes:
    raw = ctx.storage_get(KEY_OWNER)
    if raw is None:
        _revert("not-initialized")
    return raw


def _set_owner(ctx, owner: bytes) -> None:
    if not isinstance(owner, (bytes, bytearray)) or len(owner) == 0:
        _revert("owner-empty")
    ctx.storage_set(KEY_OWNER, bytes(owner))


def _get_value(ctx) -> int:
    raw = ctx.storage_get(KEY_VALUE)
    if raw is None:
        return 0
    return _decode_u64(raw)


def _set_value(ctx, value: int) -> None:
    if value < 0:
        _revert("value-negative")
    ctx.storage_set(KEY_VALUE, _encode_u64(value))


def _set_version(ctx, version: int) -> None:
    if version < 0 or version > 0xFFFFFFFF:
        _revert("bad-version")
    ctx.storage_set(KEY_VERSION, _encode_u32(version))


# -------------------------
# Encoders / Decoders (ABI-lite)
# Keep these minimal and deterministic. Little-endian for u32/u64.
# -------------------------
def _encode_u32(x: int) -> bytes:
    return bytes((
        x & 0xFF,
        (x >> 8) & 0xFF,
        (x >> 16) & 0xFF,
        (x >> 24) & 0xFF,
    ))


def _encode_u64(x: int) -> bytes:
    # 8 bytes little-endian
    return bytes((
        x & 0xFF,
        (x >> 8) & 0xFF,
        (x >> 16) & 0xFF,
        (x >> 24) & 0xFF,
        (x >> 32) & 0xFF,
        (x >> 40) & 0xFF,
        (x >> 48) & 0xFF,
        (x >> 56) & 0xFF,
    ))


def _decode_u64(b: bytes) -> int:
    if len(b) != 8:
        _revert("bad-u64")
    return (
        b[0]
        | (b[1] << 8)
        | (b[2] << 16)
        | (b[3] << 24)
        | (b[4] << 32)
        | (b[5] << 40)
        | (b[6] << 48)
        | (b[7] << 56)
    )


def _abi_pack_u64(x: int) -> bytes:
    return _encode_u64(x)


def _abi_pack_u64_pair(a: int, b: int) -> bytes:
    return _encode_u64(a) + _encode_u64(b)


def _abi_pack_owner_version(owner: bytes, version: int) -> bytes:
    return _pad_to_32(owner) + _encode_u32(version)


def _abi_pack_owner_pair(old_owner: bytes, new_owner: bytes) -> bytes:
    return _pad_to_32(old_owner) + _pad_to_32(new_owner)


def _pad_to_32(bv: bytes) -> bytes:
    # Pads/truncates to 32 bytes (left-trim if longer, right-pad with zeros if shorter).
    if len(bv) >= 32:
        return bv[:32]
    return bv + bytes(32 - len(bv))


# -------------------------
# Access control
# -------------------------
def _only_owner(ctx) -> None:
    if ctx.sender != _get_owner(ctx):
        _revert("only-owner")


# -------------------------
# Events (optional hook)
# -------------------------
def _emit(ctx, name: str, data: bytes) -> None:
    # If runtime supports events, this will succeed. Otherwise, it's a no-op.
    try:
        ctx.emit(name, data)
    except AttributeError:
        # Event system not available in this runtime.
        return


# -------------------------
# Deterministic revert
# -------------------------
def _revert(reason: str) -> None:
    # Avoid raising arbitrary exceptions to keep semantics clean in different runtimes.
    # The VM should intercept this and surface the message.
    raise Exception(reason)
