from __future__ import annotations

"""
Animica P2P Handshake (v1)
==========================

Goal
----
Establish AEAD send/recv keys using a post-quantum KEM (Kyber-768) and an
HKDF-SHA3-256 key schedule **bound to an authenticated transcript**. The
transcript includes the exact bytes of the initiator/responder HELLO frames
(as they go on the wire) plus the KEM artifacts, making the handshake
channel-binding and resistant to downgrade/replay across networks.

This module only does the *cryptographic* parts:
  • Generates the initiator’s ephemeral Kyber keypair.
  • Responder encapsulates to that Kyber public key → (ct, ss).
  • Initiator decapsulates ct using its Kyber secret key → ss.
  • Transcript hash H = SHA3-256( domain || IHELLO || RHELLO || KEM pk || KEM ct )
  • HKDF-Extract(salt=H, IKM=ss) → PRK; HKDF-Expand → client/server write keys
  • Return a HandshakeResult with send/recv keys chosen by role.

The wire shapes (CBOR/msgspec) of the HELLO frames are handled by
`p2p/protocol/hello.py`. You MUST pass the **exact** serialized bytes used on
the wire into the functions here so both sides hash the same transcript.

Key schedule
------------
- AEAD: chacha20-poly1305 (default) or aes-gcm (controlled by env P2P_AEAD)
- KEY_SIZE = 32, NONCE_BASE_SIZE = 12
- okm layout (client/server are *logical* roles):
    client_write_key | server_write_key | client_nonce_base | server_nonce_base
  Role→direction mapping:
    - INITIATOR sends with client_* and receives with server_*
    - RESPONDER sends with server_* and receives with client_*

Security notes
--------------
- The transcript must include all policy/version/network discriminators (e.g.
  chainId, protocol versions, PQ algorithm ids, alg-policy root). Put those
  into the HELLO payloads so they are covered by the transcript hash.
- Identity authentication (signing the transcript hash with Dilithium/SPHINCS+)
  is performed by the protocol layer; this module exposes `transcript_hash`
  precisely for that purpose.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Tuple

# PQ primitives & helpers (from the pq package)
from pq.py.utils.hash import sha3_256
from pq.py.utils.hkdf import hkdf_extract, hkdf_expand
# Prefer the dedicated KEM wrapper (thin shim over oqs / fallback)
try:
    # Our canonical wrappers
    from pq.py.kem import generate_keypair as kyber_generate_keypair  # type: ignore
    from pq.py.kem import encapsulate as kyber_encapsulate  # type: ignore
    from pq.py.kem import decapsulate as kyber_decapsulate  # type: ignore
except Exception:  # pragma: no cover - fallback to algs.kyber768
    from pq.py.algs.kyber768 import (  # type: ignore
        generate_keypair as kyber_generate_keypair,
        encapsulate as kyber_encapsulate,
        decapsulate as kyber_decapsulate,
    )

# Local helpers for AEAD defaults & peer-id (lazy from __init__)
from . import get_default_aead_name

# ---------------------------------------------------------------------------

class Role(Enum):
    INITIATOR = auto()
    RESPONDER = auto()

AEAD_KEY_SIZE = 32
NONCE_BASE_SIZE = 12
# Transcript domain tag (versioned)
TRANSCRIPT_DOMAIN = b"animica/p2p/hs/v1"
# HKDF info label (kept short and constant)
KEY_SCHEDULE_INFO = b"animica/p2p/hs/keys/v1"

@dataclass(frozen=True)
class HandshakeKeys:
    """Symmetric keys & bases for an established session."""
    aead: str
    send_key: bytes
    recv_key: bytes
    send_nonce_base: bytes
    recv_nonce_base: bytes
    transcript_hash: bytes  # 32 bytes (SHA3-256)
    shared_secret: bytes    # raw KEM ss (not strictly needed after HKDF, kept for debugging)

@dataclass
class InitiatorState:
    """Ephemeral state maintained between flight1 and flight2 on the initiator."""
    hello_i_bytes: bytes
    kyber_sk: bytes
    kyber_pk: bytes
    aead: str

# ---------------------------------------------------------------------------

def _le32(n: int) -> bytes:
    return n.to_bytes(4, "big")

def _th_init() -> sha3_256:
    h = sha3_256()
    h.update(TRANSCRIPT_DOMAIN)
    return h

def _th_update(h, label: bytes, blob: bytes) -> None:
    """
    Frame into transcript as: len(label)||label || len(blob)||blob
    This prevents ambiguity and binds both order and content.
    """
    h.update(_le32(len(label))); h.update(label)
    h.update(_le32(len(blob)));  h.update(blob)

def _derive_keys(role: Role, ss: bytes, transcript_hash: bytes, aead: str) -> HandshakeKeys:
    """HKDF-SHA3-256 extract/expand into client/server key material and map by role."""
    prk = hkdf_extract(salt=transcript_hash, ikm=ss)
    out_len = (AEAD_KEY_SIZE * 2) + (NONCE_BASE_SIZE * 2)
    okm = hkdf_expand(prk=prk, info=KEY_SCHEDULE_INFO, length=out_len)
    c_wk = okm[0:AEAD_KEY_SIZE]
    s_wk = okm[AEAD_KEY_SIZE:AEAD_KEY_SIZE*2]
    c_nb = okm[AEAD_KEY_SIZE*2:AEAD_KEY_SIZE*2 + NONCE_BASE_SIZE]
    s_nb = okm[AEAD_KEY_SIZE*2 + NONCE_BASE_SIZE: AEAD_KEY_SIZE*2 + NONCE_BASE_SIZE*2]
    if role is Role.INITIATOR:
        send_key, recv_key = c_wk, s_wk
        send_nb,  recv_nb  = c_nb, s_nb
    else:  # RESPONDER
        send_key, recv_key = s_wk, c_wk
        send_nb,  recv_nb  = s_nb, c_nb
    return HandshakeKeys(
        aead=aead,
        send_key=send_key,
        recv_key=recv_key,
        send_nonce_base=send_nb,
        recv_nonce_base=recv_nb,
        transcript_hash=transcript_hash,
        shared_secret=ss,
    )

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def initiator_begin(hello_i_bytes: bytes, *, aead: str | None = None) -> Tuple[InitiatorState, bytes]:
    """
    Initiator flight-1:
      - Generate ephemeral Kyber-768 keypair (pk_i, sk_i).
      - Return `InitiatorState` and pk_i (to be embedded into the HELLO-I frame by the caller).

    The caller MUST include `hello_i_bytes` as the EXACT bytes sent on the wire
    (including the pk_i field and any identity/policy/version fields) when
    calling `initiator_complete`, so the transcript hash matches.

    Returns:
        (state, kyber_pk_bytes)
    """
    aead_name = aead or get_default_aead_name()
    pk, sk = kyber_generate_keypair()
    return InitiatorState(hello_i_bytes=hello_i_bytes, kyber_sk=sk, kyber_pk=pk, aead=aead_name), pk


def initiator_complete(state: InitiatorState, hello_r_bytes: bytes, kyber_ct: bytes) -> HandshakeKeys:
    """
    Initiator flight-2 (after receiving responder’s HELLO-R with kyber_ct):
      - Decapsulate Kyber ct with our sk_i → ss
      - Compute transcript hash over (domain, HELLO-I, HELLO-R, pk_i, ct_r)
      - HKDF derive AEAD send/recv keys according to Role.INITIATOR
    """
    ss = kyber_decapsulate(state.kyber_sk, kyber_ct)

    th = _th_init()
    _th_update(th, b"IHELLO", state.hello_i_bytes)
    _th_update(th, b"RHELLO", hello_r_bytes)
    _th_update(th, b"KEM-PK-I", state.kyber_pk)
    _th_update(th, b"KEM-CT-R", kyber_ct)
    transcript_hash = th.digest()

    return _derive_keys(Role.INITIATOR, ss, transcript_hash, state.aead)


def responder_respond(hello_i_bytes: bytes, kyber_pk_i: bytes, hello_r_bytes: bytes, *, aead: str | None = None) -> Tuple[bytes, HandshakeKeys]:
    """
    Responder single-shot:
      - Encapsulate to initiator’s Kyber pk → (ct, ss)
      - Compute transcript hash over (domain, HELLO-I, HELLO-R, pk_i, ct_r)
      - HKDF derive AEAD send/recv keys according to Role.RESPONDER

    Returns:
        (kyber_ct, keys)
    """
    aead_name = aead or get_default_aead_name()
    ct, ss = kyber_encapsulate(kyber_pk_i)

    th = _th_init()
    _th_update(th, b"IHELLO", hello_i_bytes)
    _th_update(th, b"RHELLO", hello_r_bytes)
    _th_update(th, b"KEM-PK-I", kyber_pk_i)
    _th_update(th, b"KEM-CT-R", ct)
    transcript_hash = th.digest()

    keys = _derive_keys(Role.RESPONDER, ss, transcript_hash, aead_name)
    return ct, keys

# ---------------------------------------------------------------------------
# Optional convenience: all-in-one two-flight simulation (useful for tests)
# ---------------------------------------------------------------------------

def simulate_two_flight(hello_i_bytes: bytes, hello_r_bytes: bytes, *, aead: str | None = None) -> Tuple[bytes, HandshakeKeys, HandshakeKeys]:
    """
    Simulate a full handshake in-process:
      - I: generate (pk_i, sk_i)
      - R: encapsulate → ct, keys_R
      - I: decapsulate → keys_I
    Returns:
      (kyber_ct, responder_keys, initiator_keys)
    """
    aead_name = aead or get_default_aead_name()
    state, pk_i = initiator_begin(hello_i_bytes, aead=aead_name)
    ct_r, keys_r = responder_respond(hello_i_bytes, pk_i, hello_r_bytes, aead=aead_name)
    keys_i = initiator_complete(state, hello_r_bytes, ct_r)
    # Sanity: both sides derived identical transcript hash and opposite directions
    assert keys_i.transcript_hash == keys_r.transcript_hash, "transcript mismatch"
    assert keys_i.send_key == keys_r.recv_key and keys_i.recv_key == keys_r.send_key, "key mismatch"
    assert keys_i.send_nonce_base == keys_r.recv_nonce_base and keys_i.recv_nonce_base == keys_r.send_nonce_base, "nonce base mismatch"
    return ct_r, keys_r, keys_i
