from __future__ import annotations

"""
kem.py — Kyber-768 KEM wrappers and key-schedule helpers for Animica.

This module provides a stable facade over the Kyber768 KEM backend (usually via liboqs
bindings exposed in pq.py.algs.kyber768). It also includes a deterministic HKDF-SHA3-256
key schedule used by higher-level protocols (e.g., P2P handshake).

Public API
----------
- keygen(seed: bytes|None = None) -> (pk: bytes, sk: bytes)
- encapsulate(pk: bytes, *, context: bytes = b"", salt: bytes|None = None) -> (ct: bytes, ss: bytes)
- decapsulate(sk: bytes, ct: bytes, *, context: bytes = b"", salt: bytes|None = None) -> bytes
- derive_symmetric_keys(ss: bytes, *, our_pub: bytes = b"", peer_pub: bytes = b"",
                        transcript: bytes = b"", n_keys: int = 2, key_len: int = 32,
                        salt: bytes|None = None) -> list[bytes]

Notes
-----
- `context` and `transcript` are optional binding materials for the HKDF "info" field.
  They DO NOT change the KEM primitive; they bind the exported symmetric keys to protocol
  context (ALPN, versions, feature flags) and to the handshake transcript hash.
- We use SHA3-256 for HKDF extract/expand, consistent with Animica's SHA3-first policy.
- The info structure is length-prefixed and order-stable to thwart reflection attacks.
"""

from dataclasses import dataclass
from typing import Optional, List

from pq.py.registry import ALG_ID, ALG_NAME
from pq.py.utils import rng as rng_utils

# Try to use the shared HKDF utilities; fall back to a local implementation if needed.
try:
    # Preferred unified interface
    from pq.py.utils.hkdf import hkdf as _hkdf
    def _hkdf_bytes(ikm: bytes, *, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
        return _hkdf(ikm, salt=salt, info=info, length=length)
except Exception:  # pragma: no cover - defensive fallback
    try:
        from pq.py.utils.hkdf import hkdf_extract, hkdf_expand  # type: ignore
        def _hkdf_bytes(ikm: bytes, *, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
            prk = hkdf_extract(salt, ikm)
            return hkdf_expand(prk, info, length)
    except Exception:
        # Local minimal HKDF-SHA3-256 (RFC 5869-style) fallback.
        import hashlib, math
        def _hkdf_bytes(ikm: bytes, *, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:  # pragma: no cover
            if not salt:
                salt = b"\x00" * 32
            prk = hashlib.sha3_256(salt + ikm).digest()
            n = math.ceil(length / 32)
            okm = b""
            t = b""
            for i in range(1, n + 1):
                t = hashlib.sha3_256(t + info + bytes([i])).digest()
                okm += t
            return okm[:length]

__all__ = [
    "KEM_ALG_NAME",
    "KEM_ALG_ID",
    "keygen",
    "encapsulate",
    "decapsulate",
    "derive_symmetric_keys",
]

KEM_ALG_NAME: str = "kyber768"
KEM_ALG_ID: int = ALG_ID[KEM_ALG_NAME]  # canonical id from registry


# --------------------------------------------------------------------------------------
# Backend loader
# --------------------------------------------------------------------------------------

def _backend():
    """
    Load the Kyber768 backend module.

    Backends are expected to implement:
        - keypair(seed: Optional[bytes]) -> (pk: bytes, sk: bytes)
        - encapsulate(pk: bytes) -> (ct: bytes, ss: bytes)
        - decapsulate(sk: bytes, ct: bytes) -> ss: bytes
    """
    try:
        from pq.py.algs import kyber768 as kyb
    except Exception as e:  # pragma: no cover - import failure path
        raise NotImplementedError(
            "Kyber768 backend not available. Ensure pq.py.algs.kyber768 can be imported "
            "(e.g., build/install liboqs and the Python wrapper)."
        ) from e
    for fn in ("keypair", "encapsulate", "decapsulate"):
        if not hasattr(kyb, fn):
            raise NotImplementedError(f"Kyber768 backend missing required function: {fn}")
    return kyb


# --------------------------------------------------------------------------------------
# KEM primitives
# --------------------------------------------------------------------------------------

def keygen(seed: Optional[bytes] = None) -> tuple[bytes, bytes]:
    """
    Generate a Kyber768 keypair.

    If `seed` is provided, and the backend supports deterministic keygen, it will be used.
    Otherwise, OS RNG will be used for entropy.
    """
    kyb = _backend()
    if seed is not None:
        return kyb.keypair(seed=seed)  # type: ignore[call-arg]
    # Use OS RNG to source entropy if backend supports implicit RNG.
    # Some wrappers accept `seed=None`; others expect zero args.
    try:
        return kyb.keypair()  # type: ignore[call-arg]
    except TypeError:
        rnd = rng_utils.os_random(48)  # generous seed; backend may KDF internally
        return kyb.keypair(seed=rnd)  # type: ignore[call-arg]


def encapsulate(
    pk: bytes,
    *,
    context: bytes = b"",
    salt: Optional[bytes] = None,
) -> tuple[bytes, bytes]:
    """
    Perform Kyber768 encapsulation against `pk`.

    Returns (ct, ss_raw), where `ss_raw` is the KEM shared secret. Most callers will feed
    `ss_raw` into `derive_symmetric_keys` to obtain AEAD keys bound to the protocol context.

    Parameters
    ----------
    pk : bytes
        Peer public key (Kyber768).
    context : bytes
        Optional binding string (e.g., b"animica/p2p/handshake/v1"). Used only in
        the subsequent HKDF key schedule; does not affect encapsulation itself.
    salt : Optional[bytes]
        Optional salt for HKDF; if provided, it will be mixed when deriving AEAD keys.
    """
    kyb = _backend()
    ct, ss_raw = kyb.encapsulate(pk)  # type: ignore[arg-type]
    # We do NOT apply HKDF here to keep the raw primitive available for advanced users.
    return ct, ss_raw


def decapsulate(
    sk: bytes,
    ct: bytes,
    *,
    context: bytes = b"",
    salt: Optional[bytes] = None,
) -> bytes:
    """
    Perform Kyber768 decapsulation of `ct` using secret key `sk`.

    Returns the raw KEM shared secret `ss_raw`. To derive symmetric keys for encryption,
    feed the result into `derive_symmetric_keys`.
    """
    kyb = _backend()
    ss_raw = kyb.decapsulate(sk, ct)  # type: ignore[arg-type]
    return ss_raw


# --------------------------------------------------------------------------------------
# Key schedule (HKDF-SHA3-256)
# --------------------------------------------------------------------------------------

def _lp(x: bytes) -> bytes:
    """2-byte big-endian length prefix helper (length ≤ 65535 enforced by callers)."""
    if len(x) > 0xFFFF:
        raise ValueError("context/transcript/public key too large for length-prefix")
    return len(x).to_bytes(2, "big") + x


def derive_symmetric_keys(
    ss: bytes,
    *,
    our_pub: bytes = b"",
    peer_pub: bytes = b"",
    transcript: bytes = b"",
    n_keys: int = 2,
    key_len: int = 32,
    salt: Optional[bytes] = None,
    context_label: bytes = b"animica/pq/kyber768/kdf/v1",
) -> List[bytes]:
    """
    Derive `n_keys` symmetric keys from a raw KEM shared secret using HKDF-SHA3-256.

    Binding order (info field):
        info := "animica/pq/kyber768/kdf/v1" ||
                LP(min(our_pub, peer_pub)) || LP(max(our_pub, peer_pub)) ||
                LP(transcript)

    - Sorting the pubkeys removes reflection/origin ambiguity (initiator/responder).
    - `transcript` should be a running SHA3-256 transcript of handshake parameters.

    Returns
    -------
    list[bytes] : [k0, k1, ...] each of length `key_len`.
    """
    if n_keys <= 0 or key_len <= 0:
        raise ValueError("n_keys and key_len must be positive")

    # Order-stable peer binding
    a, b = (our_pub, peer_pub) if our_pub <= peer_pub else (peer_pub, our_pub)

    info = context_label + _lp(a) + _lp(b) + _lp(transcript)
    salt_b = salt if salt is not None else b""

    out: List[bytes] = []
    # Expand sequentially with distinct "info" labels for each key to avoid reusing the same stream.
    for idx in range(n_keys):
        ki = _hkdf_bytes(ss, salt=salt_b, info=info + bytes([idx]), length=key_len)
        out.append(ki)
    return out


# --------------------------------------------------------------------------------------
# CLI (dev helper)
# --------------------------------------------------------------------------------------

def _hex(b: bytes) -> str:
    return b.hex()

def _parse_hex(arg: str) -> bytes:
    s = arg.strip().lower()
    if s.startswith("hex:"):
        s = s[4:]
    return bytes.fromhex(s.replace("_", "").replace(" ", ""))

def _print_usage() -> None:
    print(
        "Kyber768 KEM demo\n"
        "Usage:\n"
        "  python -m pq.py.kem gen\n"
        "  python -m pq.py.kem enc <hex:pk>\n"
        "  python -m pq.py.kem dec <hex:sk> <hex:ct>\n"
        "\n"
        "Notes:\n"
        "  - Outputs are hex.\n"
        "  - This CLI demonstrates primitives only; real protocols should use a transcript and\n"
        "    derive AEAD keys with derive_symmetric_keys().\n"
    )

def _main() -> None:
    import sys
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        _print_usage()
        sys.exit(0)

    cmd = args[0]
    if cmd == "gen":
        pk, sk = keygen()
        print("pk=", _hex(pk))
        print("sk=", _hex(sk))
        sys.exit(0)

    if cmd == "enc":
        if len(args) < 2:
            _print_usage()
            sys.exit(2)
        pk = _parse_hex(args[1])
        ct, ss = encapsulate(pk)
        print("ct=", _hex(ct))
        print("ss=", _hex(ss))
        sys.exit(0)

    if cmd == "dec":
        if len(args) < 3:
            _print_usage()
            sys.exit(2)
        sk = _parse_hex(args[1])
        ct = _parse_hex(args[2])
        ss = decapsulate(sk, ct)
        print("ss=", _hex(ss))
        sys.exit(0)

    _print_usage()
    sys.exit(2)

if __name__ == "__main__":
    _main()
