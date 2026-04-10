"""
Animica • DA • Blob Receipt

A post (upload) receipt binds a blob commitment to:
- the namespace id,
- the size and optional MIME,
- the active PQ algorithm policy root,
- the chain id where the receipt is valid,
- and the signer (Animica bech32m address + alg_id).

The receipt is signed over *canonical CBOR* "SignBytes" with a domain tag,
so nodes and tools can verify integrity and policy binding before accepting
the blob as referenced by a header/transaction.

This module does **not** depend on a specific PQ implementation. Instead,
callers pass `sign_fn` and `verify_fn` closures so you can plug in the real
Dilithium/SPHINCS+ backends from pq/.
"""

from __future__ import annotations

import binascii
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from da.constants import MAX_BLOB_BYTES
# Local helpers
from da.utils.hash import sha3_256  # canonical object hash

try:
    import cbor2  # preferred for canonical CBOR
except Exception:  # pragma: no cover
    cbor2 = None  # we'll fall back to a tiny deterministic encoder

# --------------------------- Canonical encoding ----------------------------

_DOMAIN_TAG = "da_receipt_v1"  # appears inside SignBytes as key 1


def _cbor_dumps_canonical(obj: Any) -> bytes:
    """
    Deterministic CBOR encoding. Uses cbor2 in canonical mode when available,
    otherwise falls back to a tiny subset encoder for ints/bytes/str/dict with int keys.
    """
    if cbor2:
        return cbor2.dumps(obj, canonical=True)

    # Minimal fallback: only what we need for SignBytes/Receipt
    def enc(v) -> bytes:
        if isinstance(v, int):
            # major type 0 (unsigned) / 1 (negative) — only non-negative used here
            if v < 0:
                raise ValueError("negative ints not supported in fallback CBOR")
            # small ints shortcut
            if v <= 23:
                return bytes([0x00 | v])
            elif v <= 0xFF:
                return bytes([0x18, v])
            elif v <= 0xFFFF:
                return bytes([0x19]) + v.to_bytes(2, "big")
            elif v <= 0xFFFFFFFF:
                return bytes([0x1A]) + v.to_bytes(4, "big")
            else:
                return bytes([0x1B]) + v.to_bytes(8, "big")
        if isinstance(v, bytes):
            n = len(v)
            if n <= 23:
                hdr = bytes([0x40 | n])
            elif n <= 0xFF:
                hdr = bytes([0x58, n])
            elif n <= 0xFFFF:
                hdr = bytes([0x59]) + n.to_bytes(2, "big")
            elif n <= 0xFFFFFFFF:
                hdr = bytes([0x5A]) + n.to_bytes(4, "big")
            else:
                hdr = bytes([0x5B]) + n.to_bytes(8, "big")
            return hdr + v
        if isinstance(v, str):
            b = v.encode("utf-8")
            n = len(b)
            if n <= 23:
                hdr = bytes([0x60 | n])
            elif n <= 0xFF:
                hdr = bytes([0x78, n])
            elif n <= 0xFFFF:
                hdr = bytes([0x79]) + n.to_bytes(2, "big")
            elif n <= 0xFFFFFFFF:
                hdr = bytes([0x7A]) + n.to_bytes(4, "big")
            else:
                hdr = bytes([0x7B]) + n.to_bytes(8, "big")
            return hdr + b
        if isinstance(v, dict):
            # Only int keys; encode in key order (canonical)
            items = sorted(v.items(), key=lambda kv: kv[0])
            n = len(items)
            if n <= 23:
                out = bytes([0xA0 | n])
            elif n <= 0xFF:
                out = bytes([0xB8, n])
            elif n <= 0xFFFF:
                out = bytes([0xB9]) + n.to_bytes(2, "big")
            else:
                raise ValueError("map too large for fallback CBOR")
            for k, vv in items:
                if not isinstance(k, int):
                    raise ValueError("fallback CBOR only supports int keys")
                out += enc(k) + enc(vv)
            return out
        if v is None:
            return b"\xf6"
        raise TypeError(f"unsupported type in fallback CBOR: {type(v)}")

    return enc(obj)


def _cbor_loads(data: bytes) -> Any:
    if not cbor2:
        raise RuntimeError(
            "Decoding requires cbor2; install dependency or keep receipts in CBOR bytes."
        )
    return cbor2.loads(data)


# ----------------------------- Model --------------------------------------


@dataclass(frozen=True)
class BlobReceipt:
    """
    Receipt for a posted blob.

    Fields
    ------
    commitment : bytes
        NMT root / commitment (usually 32 bytes).
    namespace : int
        Numeric namespace id the blob was posted under.
    size_bytes : int
        Full blob size in bytes (pre-erasure).
    chain_id : int
        CAIP-2 numeric id (e.g. 1 mainnet, 2 testnet, 1337 devnet for animica).
    policy_root : bytes
        SHA3-512 Merkle root of the active PQ alg-policy tree (spec/pq_policy and alg_policy.schema.json).
    alg_id : int
        Canonical signature algorithm id used by the signer (see pq/alg_ids.yaml).
    signer : str
        bech32m address (prefix 'anim1…'); address encodes alg_id in its payload in pq/.
    signature : bytes
        Signature over canonical SignBytes.
    timestamp : int
        Seconds since epoch when the receipt was issued (node clock).
    mime : Optional[str]
        Optional MIME-type hint for display. Not part of the commitment but included in SignBytes.
    """

    commitment: bytes
    namespace: int
    size_bytes: int
    chain_id: int
    policy_root: bytes
    alg_id: int
    signer: str
    signature: bytes
    timestamp: int
    mime: Optional[str] = None

    # ---- Canonical bytes used for signing ----
    def signbytes(self) -> bytes:
        """
        Canonical "SignBytes" CBOR map with int keys (stable ordering).
        """
        if self.size_bytes < 0 or self.size_bytes > MAX_BLOB_BYTES:
            raise ValueError("size_bytes out of bounds")
        m: Dict[int, Any] = {
            1: _DOMAIN_TAG,  # domain/version tag
            2: int(self.chain_id),  # chain binding
            3: bytes(self.commitment),  # commitment/root
            4: int(self.namespace),
            5: int(self.size_bytes),
            6: self.mime if self.mime is not None else None,
            7: bytes(self.policy_root),  # alg-policy root binding
            8: int(self.alg_id),
            9: self.signer.lower(),  # bech32m (lowercase for stability)
            10: int(self.timestamp),
        }
        return _cbor_dumps_canonical(m)

    # ---- Encoding ----
    def to_cbor(self) -> bytes:
        """
        Full receipt CBOR (SignBytes fields + signature under key 11).
        """
        m: Dict[int, Any] = {
            1: _DOMAIN_TAG,
            2: int(self.chain_id),
            3: bytes(self.commitment),
            4: int(self.namespace),
            5: int(self.size_bytes),
            6: self.mime if self.mime is not None else None,
            7: bytes(self.policy_root),
            8: int(self.alg_id),
            9: self.signer.lower(),
            10: int(self.timestamp),
            11: bytes(self.signature),
        }
        return _cbor_dumps_canonical(m)

    @staticmethod
    def from_cbor(data: bytes) -> "BlobReceipt":
        m = _cbor_loads(data)
        # minimal validation
        if not isinstance(m, dict):
            raise ValueError("invalid receipt: not a map")
        if m.get(1) != _DOMAIN_TAG:
            raise ValueError("invalid receipt: bad domain tag")
        return BlobReceipt(
            commitment=bytes(m[3]),
            namespace=int(m[4]),
            size_bytes=int(m[5]),
            chain_id=int(m[2]),
            policy_root=bytes(m[7]),
            alg_id=int(m[8]),
            signer=str(m[9]).lower(),
            signature=bytes(m[11]),
            timestamp=int(m[10]),
            mime=(str(m[6]) if m.get(6) is not None else None),
        )

    # ---- Hashing (object id) ----
    def object_id(self) -> bytes:
        """
        Stable identifier for the receipt object itself (not the blob!).
        """
        return sha3_256(self.to_cbor())


# --------------------------- Builders & Verify -----------------------------


SignFn = Callable[[int, str, bytes], bytes]
"""
sign_fn(alg_id, signer_address, signbytes) -> signature bytes
- alg_id: canonical PQ algorithm id (e.g., Dilithium3)
- signer_address: bech32m 'anim1…' string
- signbytes: canonical CBOR blob from BlobReceipt.signbytes()
"""

VerifyFn = Callable[[int, str, bytes, bytes], bool]
"""
verify_fn(alg_id, signer_address, signbytes, signature) -> bool
"""


def build_receipt(
    *,
    commitment: bytes,
    namespace: int,
    size_bytes: int,
    chain_id: int,
    policy_root: bytes,
    alg_id: int,
    signer_address: str,
    sign_fn: SignFn,
    mime: Optional[str] = None,
    timestamp: Optional[int] = None,
) -> BlobReceipt:
    """
    Construct and sign a BlobReceipt using the provided signing callback.
    """
    ts = int(timestamp if timestamp is not None else time.time())
    r = BlobReceipt(
        commitment=bytes(commitment),
        namespace=int(namespace),
        size_bytes=int(size_bytes),
        chain_id=int(chain_id),
        policy_root=bytes(policy_root),
        alg_id=int(alg_id),
        signer=str(signer_address).lower(),
        signature=b"",  # filled below
        timestamp=ts,
        mime=mime,
    )
    sig = sign_fn(r.alg_id, r.signer, r.signbytes())
    return BlobReceipt(**{**r.__dict__, "signature": bytes(sig)})


def verify_receipt(
    r: BlobReceipt,
    *,
    verify_fn: VerifyFn,
    expect_policy_root: Optional[bytes] = None,
    expect_chain_id: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Verify signature and (optionally) enforce policy root and chain binding.

    Returns (ok, error_message_if_any).
    """
    # Basic bindings
    if expect_chain_id is not None and int(r.chain_id) != int(expect_chain_id):
        return False, "chain_id mismatch"
    if expect_policy_root is not None and bytes(r.policy_root) != bytes(
        expect_policy_root
    ):
        return False, "policy_root mismatch"

    # Size sanity (not a proof; just local bounds)
    if r.size_bytes < 0 or r.size_bytes > MAX_BLOB_BYTES:
        return False, "size_bytes out of bounds"

    # Signature
    ok = bool(verify_fn(r.alg_id, r.signer, r.signbytes(), r.signature))
    if not ok:
        return False, "signature verification failed"

    return True, None


# ------------------------------- Utils ------------------------------------


def to_hex(b: bytes) -> str:
    return "0x" + binascii.hexlify(b).decode("ascii")


def summarize(r: BlobReceipt) -> str:
    """
    Human-oriented one-liner (for logs).
    """
    return (
        f"receipt(commit={to_hex(r.commitment)[:18]}…, ns={r.namespace}, size={r.size_bytes}, "
        f"chain={r.chain_id}, alg={r.alg_id}, signer={r.signer[:12]}…, ts={r.timestamp})"
    )


__all__ = [
    "BlobReceipt",
    "build_receipt",
    "verify_receipt",
    "SignFn",
    "VerifyFn",
    "summarize",
]
