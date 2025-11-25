# -*- coding: utf-8 -*-
"""
contracts.stdlib.capabilities.randomness
========================================

Contract-friendly helpers for interacting with the chain randomness beacon and
for constructing commit/reveal preimages deterministically.

This module has two goals:

1) **Read the beacon** from inside contracts in a deterministic way (via the VM
   syscall surface when available). We never synthesize or emulate the beacon
   here—if the host does not expose it, we revert with a clear reason so tests
   catch misconfiguration.

2) **Build commit/reveal materials** in a way that matches the on-chain beacon
   design. Contracts can *prepare* and *verify* commitments and publish helpful
   events, while the actual `rand.commit` / `rand.reveal` transactions are
   typically submitted by externally owned accounts (EOAs) through the node RPC.

Design notes
------------
- Commitments use a fixed domain separator and exact byte layout to avoid any
  ambiguity across implementations:
    C = sha3-256(DOMAIN_COMMIT || participant || salt || payload)

- We keep strict, deterministic guards for types and lengths. Violations revert:
    * b"RAND:TYPE"  — wrong Python type for an argument
    * b"RAND:LEN"   — payload/salt/address length invalid
    * b"RAND:BEACON"— beacon syscall not available / returned invalid
    * b"RAND:TAG"   — tag invalid (empty or > MAX_TAG_LEN)
    * b"RAND:NOTAG" — tag not found in storage

- Small, structured events are emitted for indexers:
    * b"CAP:RAND:BeaconRead" — {b"beacon", b"round"?}
    * b"CAP:RAND:CommitBuilt"— {b"participant", b"salt", b"payload_hash", b"commitment"}
    * b"CAP:RAND:RevealReady"— {b"participant", b"salt", b"payload_hash", b"commitment"}
    * b"CAP:RAND:TagSaved"   — {b"tag", b"commitment"}
    * b"CAP:RAND:TagCleared" — {b"tag"}

- Storage helpers let you keep commitments under small tags, mirroring the DA
  helpers for parity in developer ergonomics.

Examples
--------
>>> from contracts.stdlib.capabilities import randomness as rand
>>> beacon = rand.beacon()  # bytes
>>> C = rand.build_commitment(participant=b"\xaa"*32,
...                           salt=rand.random_salt_from(beacon),
...                           payload=b"my payload")
>>> ok = rand.verify_reveal(C, participant=b"\xaa"*32,
...                         salt=..., payload=...)

If you want to remember a commitment for later reveal:

>>> rand.save_commitment(b"mytag", C)
>>> assert rand.load_commitment(b"mytag") == C
>>> rand.clear_commitment(b"mytag")
"""
from __future__ import annotations

from typing import Final, Optional, Tuple, Union, Dict, Any

from stdlib import abi, events, storage, hash as _hash  # type: ignore

# The VM provides a syscalls surface in runtime; we try to import it.
try:  # pragma: no cover - import path depends on the runtime
    from stdlib import syscalls as _syscalls  # type: ignore
except Exception:  # pragma: no cover
    _syscalls = None  # type: ignore


# -----------------------------------------------------------------------------
# Constants & bounds (keep conservative and deterministic)
# -----------------------------------------------------------------------------

# Domain separator for commitment hashing (keep stable once deployed)
DOMAIN_COMMIT: Final[bytes] = b"RAND:COMMIT:v1"

# Contract-visible bounds / encoding sizes
ADDRESS_LEN: Final[int] = 32       # addresses encoded as 32 bytes (canonical)
SALT_LEN: Final[int] = 32          # 32 bytes salt (producer-chosen)
MAX_PAYLOAD_LEN: Final[int] = 1024 # hard cap to keep commit bodies small

# Optional local storage namespace (for commitment tags)
_TAG_PREFIX: Final[bytes] = b"cap:rand:"
MAX_TAG_LEN: Final[int] = 32


# -----------------------------------------------------------------------------
# Internal helpers — deterministic guards & utilities
# -----------------------------------------------------------------------------

def _ensure_bytes(x: object) -> bytes:
    if not isinstance(x, (bytes, bytearray)):
        abi.revert(b"RAND:TYPE")
    return bytes(x)


def _ensure_len(name: bytes, x: bytes, *, exact: Optional[int] = None,
                max_len: Optional[int] = None) -> None:
    if exact is not None:
        if len(x) != exact:
            abi.revert(b"RAND:LEN")
        return
    if max_len is not None:
        if len(x) == 0 or len(x) > max_len:
            abi.revert(b"RAND:LEN")


def _ensure_tag(tag: object) -> bytes:
    t = _ensure_bytes(tag)
    if len(t) == 0 or len(t) > MAX_TAG_LEN:
        abi.revert(b"RAND:TAG")
    return t


def _key_for(tag: bytes) -> bytes:
    return _TAG_PREFIX + tag


def _maybe_round_from(obj: Any) -> Optional[int]:
    # Accept dicts from different host versions; keys may be bytes or str
    if isinstance(obj, dict):
        for k in (b"round", "round", b"round_id", "round_id"):
            if k in obj:
                v = obj[k]  # type: ignore[index]
                if isinstance(v, int) and v >= 0:
                    return v
    return None


def _try_beacon_syscall() -> Tuple[bytes, Optional[int]]:
    """
    Attempt to read the beacon from the runtime syscall surface.

    We try a few name variants for forward/backward compatibility:
    - random_beacon() -> bytes | {"beacon": bytes, "round": int}
    - rand_beacon()   -> same
    - beacon()        -> same

    Returns (beacon_bytes, maybe_round_id) or reverts if unavailable/invalid.
    """
    names = ("random_beacon", "rand_beacon", "beacon")
    if _syscalls is None:  # no syscall surface at all
        abi.revert(b"RAND:BEACON")

    for nm in names:
        fn = getattr(_syscalls, nm, None)  # type: ignore[attr-defined]
        if fn is None:
            continue
        try:
            res = fn()  # type: ignore[misc]
        except Exception:
            continue

        # Accept raw bytes directly
        if isinstance(res, (bytes, bytearray)) and len(res) > 0:
            return (bytes(res), None)

        # Or a dict-like with "beacon" field + optional round id
        if isinstance(res, dict):
            bkey = b"beacon" if b"beacon" in res else ("beacon" if "beacon" in res else None)
            if bkey is not None:
                beacon_val = res[bkey]  # type: ignore[index]
                if isinstance(beacon_val, (bytes, bytearray)) and len(beacon_val) > 0:
                    return (bytes(beacon_val), _maybe_round_from(res))

    # If we get here, we couldn't obtain a valid beacon
    abi.revert(b"RAND:BEACON")
    raise RuntimeError("unreachable")  # pragma: no cover


# -----------------------------------------------------------------------------
# Public API — Beacon read
# -----------------------------------------------------------------------------

def beacon(*, emit_event: bool = True) -> bytes:
    """
    Read the latest finalized beacon bytes from the host.

    Parameters
    ----------
    emit_event : bool
        If True (default), emit a small event with the beacon (and round id if
        available). The event carries no PII and helps indexers/test tooling.

    Returns
    -------
    bytes
        The beacon output (post-aggregation + VDF verify; optionally post-QRNG mix).

    Emits
    -----
    b"CAP:RAND:BeaconRead" — {b"beacon", b"round"?}
    """
    b, round_id = _try_beacon_syscall()
    if emit_event:
        fields: Dict[bytes, bytes] = {b"beacon": b}
        if round_id is not None:
            fields[b"round"] = int(round_id).to_bytes(8, "big", signed=False)
        events.emit(b"CAP:RAND:BeaconRead", fields)
    return b


# -----------------------------------------------------------------------------
# Public API — Commit/Reveal helpers (pure, deterministic)
# -----------------------------------------------------------------------------

def build_commitment(*, participant: bytes, salt: bytes, payload: bytes) -> bytes:
    """
    Construct the canonical commitment digest for (participant, salt, payload).

    C = sha3-256(DOMAIN_COMMIT || participant || salt || payload)

    Parameters
    ----------
    participant : bytes
        32-byte participant identifier (e.g., address or commitment-owner key).
    salt : bytes
        32-byte uniform salt. Use :func:`random_salt_from` to derive a salt
        deterministically from the beacon when appropriate.
    payload : bytes
        Opaque payload bound into the commitment. Bounded to MAX_PAYLOAD_LEN.

    Returns
    -------
    bytes
        Commitment digest (32 bytes).

    Emits
    -----
    b"CAP:RAND:CommitBuilt" — {b"participant", b"salt", b"payload_hash", b"commitment"}
    """
    p = _ensure_bytes(participant)
    s = _ensure_bytes(salt)
    m = _ensure_bytes(payload)
    _ensure_len(b"participant", p, exact=ADDRESS_LEN)
    _ensure_len(b"salt", s, exact=SALT_LEN)
    _ensure_len(b"payload", m, max_len=MAX_PAYLOAD_LEN)

    commitment = _hash.sha3_256(DOMAIN_COMMIT + p + s + m)

    events.emit(
        b"CAP:RAND:CommitBuilt",
        {
            b"participant": p,
            b"salt": s,
            b"payload_hash": _hash.sha3_256(m),
            b"commitment": commitment,
        },
    )
    return commitment


def verify_reveal(commitment: bytes, *, participant: bytes, salt: bytes, payload: bytes) -> bool:
    """
    Check that (participant, salt, payload) opens the given commitment.

    Emits
    -----
    b"CAP:RAND:RevealReady" — {b"participant", b"salt", b"payload_hash", b"commitment"}
      (Emitted only if the verification passes.)
    """
    C = _ensure_bytes(commitment)
    built = build_commitment(participant=participant, salt=salt, payload=payload)
    ok = (built == C)
    if ok:
        events.emit(
            b"CAP:RAND:RevealReady",
            {
                b"participant": _ensure_bytes(participant),
                b"salt": _ensure_bytes(salt),
                b"payload_hash": _hash.sha3_256(_ensure_bytes(payload)),
                b"commitment": C,
            },
        )
    return ok


def random_salt_from(beacon_bytes: Optional[bytes] = None, *, context: Optional[bytes] = None) -> bytes:
    """
    Derive a 32-byte salt from the latest beacon (or provided bytes) and optional context.

    salt = sha3-256( b"RAND:SALT:v1" || beacon || context? ) [:32]

    Notes
    -----
    - This helper is **deterministic** and should be used only when binding the
      salt to the chain beacon is desired (e.g., to prevent precomputation).
    - Passing a per-call context (like a contract or participant id) is optional
      but recommended to avoid accidental reuse across domains.

    Returns
    -------
    bytes
        32-byte salt.
    """
    b = beacon_bytes if beacon_bytes is not None else beacon(emit_event=False)
    c = _ensure_bytes(context) if context is not None else b""
    return _hash.sha3_256(b"RAND:SALT:v1" + b + c)[:SALT_LEN]


# -----------------------------------------------------------------------------
# Optional — Store/load/clear commitments by tag (bounded)
# -----------------------------------------------------------------------------

def save_commitment(tag: bytes, commitment: bytes) -> None:
    """
    Save a commitment under a small tag for later retrieval.

    Emits
    -----
    b"CAP:RAND:TagSaved" — {b"tag", b"commitment"}
    """
    t = _ensure_tag(tag)
    C = _ensure_bytes(commitment)
    _ensure_len(b"commitment", C, exact=32)
    storage.set(_key_for(t), C)
    events.emit(b"CAP:RAND:TagSaved", {b"tag": t, b"commitment": C})


def load_commitment(tag: bytes) -> bytes:
    """
    Load a previously saved commitment by tag.

    Reverts
    -------
    b"RAND:NOTAG" — if tag has no stored commitment.
    """
    t = _ensure_tag(tag)
    k = _key_for(t)
    val = storage.get(k)
    if not isinstance(val, (bytes, bytearray)) or len(val) == 0:
        abi.revert(b"RAND:NOTAG")
    return bytes(val)


def clear_commitment(tag: bytes) -> None:
    """
    Clear a saved commitment (idempotent).

    Emits
    -----
    b"CAP:RAND:TagCleared" — {b"tag"} if a value was present.
    """
    t = _ensure_tag(tag)
    k = _key_for(t)
    val = storage.get(k)
    if isinstance(val, (bytes, bytearray)) and len(val) > 0:
        storage.set(k, b"")
        events.emit(b"CAP:RAND:TagCleared", {b"tag": t})


# -----------------------------------------------------------------------------
# Public exports
# -----------------------------------------------------------------------------

__all__ = [
    # Beacon
    "beacon",
    # Commit/Reveal
    "build_commitment",
    "verify_reveal",
    "random_salt_from",
    # Storage tags
    "save_commitment",
    "load_commitment",
    "clear_commitment",
    # Constants useful to callers/tests
    "DOMAIN_COMMIT",
    "ADDRESS_LEN",
    "SALT_LEN",
    "MAX_PAYLOAD_LEN",
    "MAX_TAG_LEN",
]
