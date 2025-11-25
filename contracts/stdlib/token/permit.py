# -*- coding: utf-8 -*-
"""
Permit-style off-chain approvals for Animica-20
===============================================

This module adds a meta-approval flow similar in spirit to EIP-2612, adapted to
Animica's PQ signature domains. Holders sign a structured "permit" message
off-chain; *any* relayer can submit the signed message on-chain to set an
allowance without the holder paying gas for a separate `approve` call.

Design goals
------------
- **PQ-first**: signatures are Dilithium3 / SPHINCS+ (alg_id carried alongside pubkey).
- **Typed domain**: deterministic SignBytes with explicit domain tag and chain_id.
- **Replays prevented**: per-owner monotonically increasing nonce is consumed on success.
- **Deadline**: optional epoch-seconds deadline; signature expires after that time.
- **Owner binding**: we derive the owner's address from (alg_id, pubkey) as
  `addr_payload = alg_id || sha3_256(pubkey)` and require equality with `owner`.

Host/runtime expectations
-------------------------
Contracts do not implement PQ verification; we rely on a host-provided syscall:

    bool verify_pq_signature(alg_id: bytes, pubkey: bytes, message: bytes, signature: bytes)

This is expected to be exported by the VM stdlib `abi` module in production
runtimes. If absent, `permit()` will revert with `PERMIT:VERIFY_UNAVAILABLE`.
Deadlines are checked against a deterministic block-time getter, if available:

    int block_timestamp()  # epoch seconds

If absent, the deadline check is skipped (pass 0 to disable deadline checks by design).

State & events
--------------
- Nonces:   storage key "tok:permit:nonce:<owner>" (u256, starts at 0)
- Approval: reuses the Animica-20 `EVT_APPROVAL` event from `fungible.py`
- Allowance storage layout is delegated to `fungible.py` (we import its helper).

Public API
----------
- nonces(owner)                -> int
- domain_separator(chain_id, token_addr) -> bytes32
- build_sign_bytes(chain_id, token_addr, owner, spender, value, nonce, deadline) -> bytes
- permit(caller, owner, spender, value, deadline, alg_id, pubkey, signature, chain_id, token_addr) -> bool
  (consumes nonce and sets allowance on success)

Utilities
---------
- address_from_pubkey(alg_id, pubkey) -> bytes  (payload used for equality with `owner`)

Security notes
--------------
- Make sure relayers pass the **exact** `token_addr` (this contract's address) and
  correct `chain_id`; both are part of the domain. If your runtime does not let a
  contract discover its own address, keep it in the manifest/ABI and have clients
  pass it in. Mismatched domains invalidate signatures.
- Set sensible deadlines; a value of 0 disables the deadline check (by policy).
"""

from __future__ import annotations

from typing import Final

from stdlib import events, abi, storage  # type: ignore
try:  # hash helpers for domain separation
    from stdlib.hash import sha3_256  # type: ignore
except Exception as _exc:  # pragma: no cover
    # Minimal fallback: raise at runtime if hashing is not available
    def sha3_256(data: bytes) -> bytes:  # type: ignore
        abi.revert(b"PERMIT:SHA3_UNAVAILABLE")
        return b""  # unreachable

# Import the base token internals so we update allowance in the canonical layout
from .fungible import (  # type: ignore
    key_allowance,      # (owner, spender) -> storage key
    _set_u256,          # storage setter for u256 (big-endian)
    EVT_APPROVAL,       # event tag for Approval(owner, spender, value)
)
from ..token import fungible as _tok  # type: ignore  # for address/amount guards if needed

# Optional contextual clock for deadline enforcement (epoch seconds)
try:
    from stdlib.abi import block_timestamp as _block_timestamp  # type: ignore
except Exception:  # pragma: no cover
    _block_timestamp = None  # type: ignore

# Optional PQ verifier syscall (host-provided)
def _verify_unavailable(*_a: bytes) -> bool:  # pragma: no cover
    abi.revert(b"PERMIT:VERIFY_UNAVAILABLE")
    return False

try:
    # Signature is validated against the **hash** of SignBytes (see _hash_sign_bytes)
    from stdlib.abi import verify_pq_signature as _verify_pq_signature  # type: ignore
except Exception:  # pragma: no cover
    _verify_pq_signature = _verify_unavailable  # type: ignore


# ------------------------------------------------------------------------------
# Constants & storage keys
# ------------------------------------------------------------------------------

DOMAIN_TAG: Final[bytes] = b"animica.permit/v1"
K_NONCE_PREFIX: Final[bytes] = b"tok:permit:nonce:"  # + owner


# ------------------------------------------------------------------------------
# Encoding helpers (deterministic, network byte order)
# ------------------------------------------------------------------------------

def _u64(n: int) -> bytes:
    if n < 0 or n > (1 << 64) - 1:
        abi.revert(b"PERMIT:U64_RANGE")
    return int(n).to_bytes(8, "big")


def _u256(n: int) -> bytes:
    if n < 0 or n > (1 << 256) - 1:
        abi.revert(b"PERMIT:U256_RANGE")
    return int(n).to_bytes(32, "big")


def _bytes32(b: bytes) -> bytes:
    if not isinstance(b, (bytes, bytearray)) or len(b) != 32:
        abi.revert(b"PERMIT:BYTES32_REQUIRED")
    return bytes(b)


# ------------------------------------------------------------------------------
# Address derivation (PQ)
# ------------------------------------------------------------------------------

def address_from_pubkey(alg_id: bytes, pubkey: bytes) -> bytes:
    """
    Deterministic address payload = alg_id || sha3_256(pubkey).
    The exact bech32m encoding/representation is outside the contract;
    here we store and compare the raw payload bytes.
    """
    if not isinstance(alg_id, (bytes, bytearray)) or len(alg_id) == 0:
        abi.revert(b"PERMIT:BAD_ALG_ID")
    if not isinstance(pubkey, (bytes, bytearray)) or len(pubkey) == 0:
        abi.revert(b"PERMIT:BAD_PUBKEY")
    return bytes(alg_id) + sha3_256(bytes(pubkey))


# ------------------------------------------------------------------------------
# Nonces
# ------------------------------------------------------------------------------

def _k_nonce(owner: bytes) -> bytes:
    if not owner:
        abi.revert(b"PERMIT:ZERO_OWNER")
    return K_NONCE_PREFIX + owner


def nonces(owner: bytes) -> int:
    """
    Read the current (unused) nonce for `owner`.
    """
    cur = storage.get(_k_nonce(owner))
    if not cur:
        return 0
    if len(cur) != 32:
        abi.revert(b"PERMIT:NONCE_CORRUPT")
    return int.from_bytes(cur, "big")


def _consume_nonce(owner: bytes) -> int:
    """
    Atomically returns the current nonce for `owner` and increments it by 1.
    """
    n = nonces(owner)
    nxt = n + 1
    storage.set(_k_nonce(owner), _u256(nxt))
    return n


# ------------------------------------------------------------------------------
# Domain & SignBytes
# ------------------------------------------------------------------------------

def domain_separator(chain_id: int, token_addr: bytes) -> bytes:
    """
    Compute a 32-byte domain separator bound to (domain tag, chain_id, token_addr).
    """
    if not token_addr:
        abi.revert(b"PERMIT:ZERO_TOKEN_ADDR")
    preimage = DOMAIN_TAG + _u64(chain_id) + token_addr
    return sha3_256(preimage)


def build_sign_bytes(
    chain_id: int,
    token_addr: bytes,
    owner: bytes,
    spender: bytes,
    value: int,
    nonce: int,
    deadline: int,
) -> bytes:
    """
    Canonical SignBytes for a permit authorization.

    Sign over sha3_256(SignBytes). We expose the unhashed SignBytes for tooling,
    and the verifier hashes internally (_hash_sign_bytes).
    """
    if not owner or not spender:
        abi.revert(b"PERMIT:ZERO_ADDR")
    # basic numeric guards (exact ranges checked by _u* encoders)
    if value < 0 or nonce < 0 or deadline < 0:
        abi.revert(b"PERMIT:NEGATIVE_PARAM")

    ds = domain_separator(chain_id, token_addr)
    sb = (
        ds
        + owner
        + spender
        + _u256(value)
        + _u256(nonce)
        + _u64(deadline)
    )
    return sb


def _hash_sign_bytes(sign_bytes: bytes) -> bytes:
    return sha3_256(sign_bytes)


# ------------------------------------------------------------------------------
# Permit execution
# ------------------------------------------------------------------------------

def permit(
    caller: bytes,
    owner: bytes,
    spender: bytes,
    value: int,
    deadline: int,
    alg_id: bytes,
    pubkey: bytes,
    signature: bytes,
    chain_id: int,
    token_addr: bytes,
) -> bool:
    """
    Apply a signed permit:

    - Verifies the signature against `sha3_256(SignBytes)`.
    - Checks `owner == address_from_pubkey(alg_id, pubkey)`.
    - Enforces deadline if a block timestamp source exists (and deadline != 0).
    - Consumes the per-owner nonce on success.
    - Sets allowance and emits Approval(owner, spender, value).
    """
    # Basic shape checks
    if not caller:
        abi.revert(b"PERMIT:ZERO_CALLER")
    if not owner or not spender:
        abi.revert(b"PERMIT:ZERO_ADDR")
    if value < 0:
        abi.revert(b"PERMIT:VALUE_NEGATIVE")

    # Deadline (if time source available and deadline != 0)
    if deadline != 0 and _block_timestamp is not None:
        now = int(_block_timestamp())  # type: ignore
        if now > int(deadline):
            abi.revert(b"PERMIT:EXPIRED")

    # Owner binding (pubkey -> address)
    derived_owner = address_from_pubkey(alg_id, pubkey)
    if derived_owner != owner:
        abi.revert(b"PERMIT:OWNER_MISMATCH")

    # Consume the *current* nonce and build SignBytes
    nonce = _consume_nonce(owner)
    sign_bytes = build_sign_bytes(
        chain_id=chain_id,
        token_addr=token_addr,
        owner=owner,
        spender=spender,
        value=value,
        nonce=nonce,
        deadline=deadline,
    )
    msg_hash = _hash_sign_bytes(sign_bytes)

    # Verify signature
    ok = bool(_verify_pq_signature(alg_id, pubkey, msg_hash, signature))
    if not ok:
        abi.revert(b"PERMIT:BAD_SIG")

    # Set allowance in the canonical layout and emit Approval
    _set_u256(key_allowance(owner, spender), _u256(value))
    events.emit(EVT_APPROVAL, {b"owner": owner, b"spender": spender, b"value": value})
    return True


# ------------------------------------------------------------------------------
# Convenience: cancel next permit (bump nonce)
# ------------------------------------------------------------------------------

def cancel_next_permit(caller: bytes, owner: bytes) -> int:
    """
    Bump the nonce for `owner` (e.g., to invalidate a previously signed, unsubmitted permit).
    Only the `owner` themselves may cancel.
    Returns the new nonce value (after increment).
    """
    if not caller or not owner:
        abi.revert(b"PERMIT:ZERO_ADDR")
    if caller != owner:
        abi.revert(b"PERMIT:NOT_OWNER")
    # consume & return the *new* nonce
    prev = _consume_nonce(owner)
    return prev + 1


__all__ = [
    "DOMAIN_TAG",
    "nonces",
    "domain_separator",
    "build_sign_bytes",
    "address_from_pubkey",
    "permit",
    "cancel_next_permit",
]
