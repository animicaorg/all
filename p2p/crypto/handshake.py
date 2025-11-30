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

import asyncio
import hashlib
import os
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Tuple

# PQ primitives & helpers (from the pq package)
from pq.py.utils.hkdf import hkdf_expand, hkdf_extract

# Prefer the dedicated KEM wrapper (thin shim over oqs / fallback)
try:
    # Our canonical wrappers
    from pq.py.kem import decapsulate as kyber_decapsulate  # type: ignore
    from pq.py.kem import encapsulate as kyber_encapsulate  # type: ignore
    from pq.py.kem import \
        generate_keypair as kyber_generate_keypair  # type: ignore
except Exception:  # pragma: no cover - fallback to algs.kyber768
    from pq.py.algs.kyber768 import decapsulate as kyber_decapsulate
    from pq.py.algs.kyber768 import encapsulate as kyber_encapsulate
    from pq.py.algs.kyber768 import \
        generate_keypair as kyber_generate_keypair  # type: ignore

# Local helpers for AEAD defaults & peer-id (lazy from __init__)
from ..transport.base import ConnInfo, HandshakeError
from . import get_default_aead_name
from .aead import AEAD_DOMAIN_TAG, derive_nonce

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
    shared_secret: (
        bytes  # raw KEM ss (not strictly needed after HKDF, kept for debugging)
    )


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


def _th_init():
    h = hashlib.sha3_256()
    h.update(TRANSCRIPT_DOMAIN)
    return h


def _th_update(h, label: bytes, blob: bytes) -> None:
    """
    Frame into transcript as: len(label)||label || len(blob)||blob
    This prevents ambiguity and binds both order and content.
    """
    h.update(_le32(len(label)))
    h.update(label)
    h.update(_le32(len(blob)))
    h.update(blob)


def _derive_keys(
    role: Role, ss: bytes, transcript_hash: bytes, aead: str
) -> HandshakeKeys:
    """HKDF-SHA3-256 extract/expand into client/server key material and map by role."""
    prk = hkdf_extract(salt=transcript_hash, ikm=ss)
    out_len = (AEAD_KEY_SIZE * 2) + (NONCE_BASE_SIZE * 2)
    okm = hkdf_expand(prk=prk, info=KEY_SCHEDULE_INFO, length=out_len)
    c_wk = okm[0:AEAD_KEY_SIZE]
    s_wk = okm[AEAD_KEY_SIZE : AEAD_KEY_SIZE * 2]
    c_nb = okm[AEAD_KEY_SIZE * 2 : AEAD_KEY_SIZE * 2 + NONCE_BASE_SIZE]
    s_nb = okm[
        AEAD_KEY_SIZE * 2 + NONCE_BASE_SIZE : AEAD_KEY_SIZE * 2 + NONCE_BASE_SIZE * 2
    ]
    if role is Role.INITIATOR:
        send_key, recv_key = c_wk, s_wk
        send_nb, recv_nb = c_nb, s_nb
    else:  # RESPONDER
        send_key, recv_key = s_wk, c_wk
        send_nb, recv_nb = s_nb, c_nb
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


def initiator_begin(
    hello_i_bytes: bytes, *, aead: str | None = None
) -> Tuple[InitiatorState, bytes]:
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
    return (
        InitiatorState(
            hello_i_bytes=hello_i_bytes, kyber_sk=sk, kyber_pk=pk, aead=aead_name
        ),
        pk,
    )


def initiator_complete(
    state: InitiatorState, hello_r_bytes: bytes, kyber_ct: bytes
) -> HandshakeKeys:
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


def responder_respond(
    hello_i_bytes: bytes,
    kyber_pk_i: bytes,
    hello_r_bytes: bytes,
    *,
    aead: str | None = None,
) -> Tuple[bytes, HandshakeKeys]:
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


def simulate_two_flight(
    hello_i_bytes: bytes, hello_r_bytes: bytes, *, aead: str | None = None
) -> Tuple[bytes, HandshakeKeys, HandshakeKeys]:
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
    assert (
        keys_i.send_key == keys_r.recv_key and keys_i.recv_key == keys_r.send_key
    ), "key mismatch"
    assert (
        keys_i.send_nonce_base == keys_r.recv_nonce_base
        and keys_i.recv_nonce_base == keys_r.send_nonce_base
    ), "nonce base mismatch"
    return ct_r, keys_r, keys_i


# ---------------------------------------------------------------------------
# Minimal TCP handshake adapter (devnet-friendly)
# ---------------------------------------------------------------------------


class _TcpAead:
    """
    Tiny adapter with the seal/open interface expected by transports.
    """

    def __init__(self, alg: str, key: bytes, nonce_base: bytes):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import (
                AESGCM, ChaCha20Poly1305)
        except Exception as exc:  # pragma: no cover - optional dependency
            raise HandshakeError(f"cryptography AEAD unavailable: {exc}") from exc

        self.alg = alg
        self.key = key
        self.nonce_base = nonce_base
        if alg == "aes-256-gcm":
            self.impl = AESGCM(key)
        else:
            self.impl = ChaCha20Poly1305(key)

    def seal(self, plaintext: bytes, *, aad: bytes, nonce: int) -> bytes:
        nonce_bytes = derive_nonce(self.nonce_base, nonce)
        aad_eff = AEAD_DOMAIN_TAG + (aad or b"")
        return self.impl.encrypt(nonce_bytes, plaintext, aad_eff)

    def open(self, ciphertext: bytes, *, aad: bytes, nonce: int) -> bytes:
        nonce_bytes = derive_nonce(self.nonce_base, nonce)
        aad_eff = AEAD_DOMAIN_TAG + (aad or b"")
        return self.impl.decrypt(nonce_bytes, ciphertext, aad_eff)


async def perform_handshake_tcp(
    reader,
    writer,
    *,
    is_outbound: bool,
    prologue: bytes = b"animica/tcp/1",
    chain_id: int | None = None,
    timeout: Optional[float] = None,
):
    """
    Minimal, symmetric TCP handshake for devnet.

    The goal here is to provide a deterministic AEAD key schedule so the TCP
    transport can seal/open frames. Security is intentionally lightweight: we
    exchange one random seed per side, bind it to a transcript hash that
    includes a small prologue, and HKDF-derive opposing send/recv keys using
    the existing Kyber handshake helper.

    Returns (tx_aead, rx_aead, ConnInfo)
    where tx_aead/rx_aead expose seal/open(plaintext|ciphertext, aad=..., nonce=int).
    """

    async def _do_handshake():
        magic = b"ANIMICA/TCP/HS/V0"
        pro = prologue or b""
        if chain_id is not None:
            pro = pro + b"|cid=" + int(chain_id).to_bytes(4, "big", signed=False)
        if len(pro) > 255:
            pro = pro[:255]

        def _make_msg(seed: bytes) -> bytes:
            return magic + bytes([len(pro)]) + pro + seed

        async def _read_peer_msg():
            header = await reader.readexactly(len(magic) + 1)
            if header[: len(magic)] != magic:
                raise HandshakeError("invalid handshake magic")

            peer_pro_len = header[len(magic)]
            body = await reader.readexactly(peer_pro_len + 32)
            peer_prologue = body[:peer_pro_len]
            peer_seed = body[-32:]
            return peer_prologue, peer_seed

        # local seed
        seed_local = os.urandom(32)

        # Send/recv order depends on role for deterministic transcript
        if is_outbound:
            writer.write(_make_msg(seed_local))
            await writer.drain()
            peer_prologue, peer_seed = await _read_peer_msg()
        else:
            peer_prologue, peer_seed = await _read_peer_msg()
            writer.write(_make_msg(seed_local))
            await writer.drain()

        if peer_prologue != pro:
            raise HandshakeError("prologue/chain-id mismatch")
        seed_remote = peer_seed

        role = Role.INITIATOR if is_outbound else Role.RESPONDER
        seed_i = seed_local if role is Role.INITIATOR else seed_remote
        seed_r = seed_remote if role is Role.INITIATOR else seed_local

        th = hashlib.sha3_256()
        th.update(magic)
        th.update(bytes([len(pro)]))
        th.update(pro)
        th.update(bytes([len(peer_prologue)]))
        th.update(peer_prologue)
        th.update(seed_i)
        th.update(seed_r)
        transcript_hash = th.digest()

        shared_secret = hashlib.sha3_256(seed_i + seed_r).digest()
        aead_name = get_default_aead_name()
        keys = _derive_keys(role, shared_secret, transcript_hash, aead_name)

        tx_aead = _TcpAead(keys.aead, keys.send_key, keys.send_nonce_base)
        rx_aead = _TcpAead(keys.aead, keys.recv_key, keys.recv_nonce_base)

        info = ConnInfo()
        info.tx_key_sha256 = hashlib.sha256(keys.send_key).digest()
        info.rx_key_sha256 = hashlib.sha256(keys.recv_key).digest()
        info.alpn = "animica/tcp"

        return tx_aead, rx_aead, info

    try:
        if timeout is not None:
            return await asyncio.wait_for(_do_handshake(), timeout)
        return await _do_handshake()
    except asyncio.TimeoutError as exc:  # pragma: no cover - network dependent
        raise HandshakeError("tcp handshake timeout") from exc
    except Exception as exc:
        raise HandshakeError(f"tcp handshake failed: {exc}") from exc
