from __future__ import annotations

"""
HELLO handshake message:
- Authenticates the peer's static identity (Dilithium3 / SPHINCS+)
- Binds to the PQ KEM transcript hash (Kyber768 + HKDF) to prevent replay/mitM
- Communicates chain_id, alg-policy root, and capability blob

This module only encodes/decodes and verifies HELLO messages.
Transport/session code is responsible for exchanging the bytes and enforcing timeouts.

Wire shape (CBOR/msgpack via msgspec):
{
  "vmaj": int,              # protocol major (must match local)
  "wire": int,              # wire schema version (must match local)
  "alg": int,               # signature alg_id (per pq/alg_ids.yaml)
  "pk":  bytes,             # node identity public key (raw)
  "pid": str,               # peer-id (sha3-256 over derivation), hex
  "cid": int,               # chain_id
  "apr": bytes,             # alg-policy Merkle root (SHA3-512 digest)
  "caps": dict,             # HelloCaps (see p2p.protocol.__init__.py)
  "th":  bytes,             # transcript hash from PQ handshake (binds sessions)
  "sig": bytes,             # signature over domain|th|hash(caps+header-without-sig)
}
"""

import binascii
from dataclasses import dataclass
from typing import Any, Dict, Optional

import msgspec

from p2p.protocol import (
    PROTOCOL_MAJOR,
    WIRE_SCHEMA_VERSION,
    HelloCaps,
    ProtocolError,
    build_hello_caps,
    validate_hello_caps,
)
# Prefer the canonical peer-id helper; fall back to a local implementation if unavailable.
try:
    from p2p.crypto.peer_id import derive_peer_id as _derive_peer_id
except Exception:  # pragma: no cover
    _derive_peer_id = None  # type: ignore

# Hashing/sign/verify primitives
try:
    from core.utils.hash import sha3_256
except Exception:  # pragma: no cover
    import hashlib

    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()

from pq.py.sign import sign as pq_sign
from pq.py.verify import verify as pq_verify


HELLO_SIGN_DOMAIN = b"animica/p2p/HELLO/v1"

# --------- Wire structs ---------


class _HelloStruct(msgspec.Struct, omit_defaults=True):
    vmaj: int
    wire: int
    alg: int
    pk: bytes
    pid: str
    cid: int
    apr: bytes
    caps: Dict[str, Any]
    th: bytes
    sig: bytes | None = None  # excluded from sign-bytes


_encoder = msgspec.msgpack.Encoder()  # canonical order given by field order
_decoder = msgspec.msgpack.Decoder(type=_HelloStruct)


# --------- Helpers ---------


def _peer_id_from_pubkey(alg_id: int, pubkey: bytes) -> str:
    """
    Peer-id derivation rule:
      pid = hex( sha3_256( b"animica/peer-id/v1" | u16be(alg_id) | pubkey ) )
    """
    if callable(_derive_peer_id):  # use the shared helper if present
        return _derive_peer_id(alg_id=alg_id, pubkey=pubkey)

    alg_be = alg_id.to_bytes(2, "big", signed=False)
    h = sha3_256(b"animica/peer-id/v1" + alg_be + pubkey)
    return h.hex()


def _sign_bytes(blob_wo_sig: _HelloStruct) -> bytes:
    tmp = _HelloStruct(
        vmaj=blob_wo_sig.vmaj,
        wire=blob_wo_sig.wire,
        alg=blob_wo_sig.alg,
        pk=blob_wo_sig.pk,
        pid=blob_wo_sig.pid,
        cid=blob_wo_sig.cid,
        apr=blob_wo_sig.apr,
        caps=blob_wo_sig.caps,
        th=blob_wo_sig.th,
        sig=None,
    )
    body = _encoder.encode(tmp)
    return sha3_256(HELLO_SIGN_DOMAIN + tmp.th + body)


# --------- Public API ---------


@dataclass(frozen=True)
class VerifiedHello:
    peer_id: str
    alg_id: int
    pubkey: bytes
    chain_id: int
    alg_policy_root: bytes
    caps: HelloCaps
    transcript_hash: bytes


def build_hello_message(
    *,
    alg_id: int,
    public_key: bytes,
    sign_key: Any,
    chain_id: int,
    alg_policy_root: bytes,
    caps: Optional[HelloCaps],
    transcript_hash: bytes,
) -> bytes:
    """
    Create a signed HELLO message.
    - sign_key: an object accepted by pq.py.sign.sign(alg_id, sk, msg) (e.g., secret key bytes)
    - caps: if None, a default capability set is built (tcp+ws, zstd)
    """
    if not isinstance(alg_policy_root, (bytes, bytearray)) or len(alg_policy_root) not in (32, 48, 64):
        # SHA3-512 is 64 bytes; allow flexibility for devnets.
        raise ProtocolError("alg_policy_root must be a digest-like bytes object")

    pid = _peer_id_from_pubkey(alg_id, public_key)

    hello = _HelloStruct(
        vmaj=PROTOCOL_MAJOR,
        wire=WIRE_SCHEMA_VERSION,
        alg=int(alg_id),
        pk=bytes(public_key),
        pid=pid,
        cid=int(chain_id),
        apr=bytes(alg_policy_root),
        caps=caps or build_hello_caps(chain_id=chain_id),
        th=bytes(transcript_hash),
        sig=None,
    )
    sb = _sign_bytes(hello)
    signature = pq_sign(alg_id, sign_key, sb)
    hello.sig = signature
    return _encoder.encode(hello)


def verify_hello_message(
    data: bytes,
    *,
    expected_chain_id: Optional[int],
    expected_transcript_hash: bytes,
    expected_alg_policy_root: Optional[bytes] = None,
) -> VerifiedHello:
    """
    Verify a HELLO message:
      • protocol major & wire schema match
      • transcript hash matches the active KEM session
      • chain_id (if provided) matches
      • peer-id derivation matches provided pubkey
      • signature verifies under the advertised alg_id/pubkey
      • alg-policy root (if provided) matches

    Returns a VerifiedHello with the parsed values.
    """
    hello = _decoder.decode(data)

    if hello.vmaj != PROTOCOL_MAJOR:
        raise ProtocolError(f"protocol major mismatch: remote={hello.vmaj} local={PROTOCOL_MAJOR}")
    if hello.wire != WIRE_SCHEMA_VERSION:
        raise ProtocolError(f"wire schema mismatch: remote={hello.wire} local={WIRE_SCHEMA_VERSION}")

    if bytes(hello.th) != bytes(expected_transcript_hash):
        raise ProtocolError("transcript hash mismatch")

    # Validate caps shape (raises on error)
    validate_hello_caps(hello.caps)

    if expected_chain_id is not None and int(hello.cid) != int(expected_chain_id):
        raise ProtocolError(f"chain_id mismatch: remote={hello.cid} expected={expected_chain_id}")

    # Recompute peer-id
    recomputed_pid = _peer_id_from_pubkey(hello.alg, hello.pk)
    if hello.pid != recomputed_pid:
        raise ProtocolError("peer-id does not match pubkey/alg_id")

    # Optional APR check
    if expected_alg_policy_root is not None and bytes(hello.apr) != bytes(expected_alg_policy_root):
        raise ProtocolError("alg-policy root mismatch")

    # Verify signature
    sb = _sign_bytes(_HelloStruct(**{**hello.__dict__, "sig": None}))
    if not pq_verify(hello.alg, hello.pk, sb, hello.sig or b""):
        raise ProtocolError("HELLO signature invalid")

    return VerifiedHello(
        peer_id=hello.pid,
        alg_id=int(hello.alg),
        pubkey=bytes(hello.pk),
        chain_id=int(hello.cid),
        alg_policy_root=bytes(hello.apr),
        caps=hello.caps,  # type: ignore
        transcript_hash=bytes(hello.th),
    )


# --------- Debug helpers ---------


def pretty_print_hello(data: bytes) -> str:
    """Human-friendly dump for logs/tests."""
    h = _decoder.decode(data)
    def hx(b: Optional[bytes]) -> str:
        return "<nil>" if b is None else binascii.hexlify(b).decode()
    return (
        f"HELLO[v{h.vmaj}/wire{h.wire}] pid={h.pid[:16]}… alg={h.alg} cid={h.cid} "
        f"apr={hx(h.apr)[:16]}… th={hx(h.th)[:16]}… sig={hx(h.sig)[:16]}… "
        f"caps.transports={h.caps.get('transports')} roles={h.caps.get('roles')}"
    )
