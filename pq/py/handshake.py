from __future__ import annotations

"""
handshake.py — Post-quantum P2P handshake (Kyber768 KEM + HKDF-SHA3 + PQ identity auth).

This module provides building blocks for Animica's P2P handshake. It is network-agnostic:
you pass bytes around using your transport; we give you deterministic encoders/decoders
and key-derivation routines.

Design (Noise-like, PQ-first)
-----------------------------
Roles: Initiator (I) and Responder (R).

1) Each side has a long-term *signature* identity (Dilithium3 or SPHINCS+-SHAKE-128s).
2) Each side generates an *ephemeral* Kyber768 KEM keypair (E = (epk, esk)).
3) I -> R: HELLO_I = encode_hello(sig_alg_id, sig_pub, kem_alg_id=kyber768, epk_I, nonce_I, features)
4) R -> I: HELLO_R = encode_hello(..., epk_R, nonce_R, ...)
   Transcript hash th = H("animica/p2p/hello-v1" || LP(HELLO_I) || LP(HELLO_R))
5) I <-> R: AUTH signatures over th
   - sig_I = Sign(th, domain="animica/p2p/auth-v1") with I's long-term signing key
   - sig_R = Sign(th, domain="animica/p2p/auth-v1") with R's long-term signing key
6) I <-> R: KEM
   - I encapsulates to epk_R → (ct_I, ss_I)
   - R encapsulates to epk_I → (ct_R, ss_R)
7) Key schedule:
   - s_mix = H( LP(ss_I) || LP(ss_R) )    # order doesn't matter after LP; both sides do the same
   - keys = HKDF_SHA3_256(s_mix, info = KDF_INFO(ordered_epks, th))
   - Derive n=2 AEAD keys of 32 bytes: k0, k1
   - Initiator uses (send=k0, recv=k1); Responder uses (send=k1, recv=k0).

Anti-reflection & binding:
- HELLO messages are ordered by role: (HELLO_I, HELLO_R) inside the transcript.
- KDF binds both ephemeral pubkeys (order-stable) plus the transcript hash.
- AUTH signs the transcript; peers verify with the long-term PQ public key from HELLO.

This file does not perform AEAD; it only yields symmetric keys suitable for AEAD.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from pq.py import address as pq_addr
from pq.py import kem as pq_kem
from pq.py import sign as pq_sign
from pq.py import verify as pq_verify
from pq.py.registry import ALG_ID, ALG_NAME
from pq.py.utils import bech32 as _b32
from pq.py.utils.hash import sha3_256, sha3_512
from pq.py.utils.rng import os_random

Role = Literal["initiator", "responder"]

HELLO_MAGIC = b"ANM1HELLO"
AUTH_MAGIC = b"ANM1AUTH"
DOMAIN_HELLO = b"animica/p2p/hello-v1"
DOMAIN_AUTH = b"animica/p2p/auth-v1"
KDF_LABEL = b"animica/pq/kyber768/kdf/v1"

KEM_ALG_NAME = "kyber768"
KEM_ALG_ID = ALG_ID[KEM_ALG_NAME]

# --------------------------------------------------------------------------------------
# Errors & dataclasses
# --------------------------------------------------------------------------------------


class HandshakeError(Exception):
    pass


@dataclass(frozen=True)
class Hello:
    sig_alg_id: int
    sig_pub: bytes
    kem_alg_id: int
    kem_ephemeral_pub: bytes
    nonce: bytes  # 32 B
    features_json: bytes  # canonical (sorted-keys) UTF-8 JSON
    bech32_addr: str  # anim1... derived from (alg_id || sha3_256(sig_pub))

    def encode(self) -> bytes:
        return encode_hello(
            self.sig_alg_id,
            self.sig_pub,
            self.kem_alg_id,
            self.kem_ephemeral_pub,
            self.nonce,
            self.features_json,
            self.bech32_addr,
        )


@dataclass(frozen=True)
class Auth:
    sig_alg_id: int
    signature: bytes  # signature over transcript hash with DOMAIN_AUTH

    def encode(self) -> bytes:
        return encode_auth(self.sig_alg_id, self.signature)


@dataclass(frozen=True)
class HandshakeResult:
    role: Role
    peer_sig_alg_id: int
    peer_sig_pub: bytes
    peer_addr: str
    transcript_hash: bytes
    send_key: bytes  # 32B
    recv_key: bytes  # 32B
    our_ct: bytes  # ciphertext we sent
    peer_ct: bytes  # ciphertext we received


# --------------------------------------------------------------------------------------
# Canonical length-prefix helpers (stable & dependency-free)
# --------------------------------------------------------------------------------------


def _lp(b: bytes) -> bytes:
    if len(b) > 0xFFFF:
        raise ValueError("Field too large for 2-byte length prefix")
    return len(b).to_bytes(2, "big") + b


def _lp_u16(v: int) -> bytes:
    if not (0 <= v <= 0xFFFF):
        raise ValueError("u16 out of range")
    return v.to_bytes(2, "big")


def _canon_json(obj: Dict[str, Any]) -> bytes:
    """
    Deterministic JSON encoder: sorted keys, no spaces, UTF-8 bytes.
    """
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------------------
# Encoders/decoders for HELLO and AUTH frames
# --------------------------------------------------------------------------------------


def encode_hello(
    sig_alg_id: int,
    sig_pub: bytes,
    kem_alg_id: int,
    kem_ephemeral_pub: bytes,
    nonce: bytes,
    features_json: bytes,
    bech32_addr: str,
) -> bytes:
    """
    Deterministic HELLO encoding:
        HELLO_MAGIC ||
        u16(sig_alg) || LP(sig_pub) ||
        u16(kem_alg) || LP(kem_ephemeral_pub) ||
        LP(nonce) || LP(features_json) || LP(bech32_addr_utf8)
    """
    if len(nonce) != 32:
        raise HandshakeError("nonce must be 32 bytes")
    baddr = bech32_addr.encode("utf-8")
    return (
        HELLO_MAGIC
        + _lp_u16(sig_alg_id)
        + _lp(sig_pub)
        + _lp_u16(kem_alg_id)
        + _lp(kem_ephemeral_pub)
        + _lp(nonce)
        + _lp(features_json)
        + _lp(baddr)
    )


def decode_hello(buf: bytes) -> Hello:
    if not buf.startswith(HELLO_MAGIC):
        raise HandshakeError("HELLO magic mismatch")
    i = len(HELLO_MAGIC)

    def take(n: int) -> bytes:
        nonlocal i
        if i + n > len(buf):
            raise HandshakeError("truncated HELLO")
        out = buf[i : i + n]
        i += n
        return out

    def take_lp() -> bytes:
        l = int.from_bytes(take(2), "big")
        return take(l)

    sig_alg_id = int.from_bytes(take(2), "big")
    sig_pub = take_lp()
    kem_alg_id = int.from_bytes(take(2), "big")
    kem_epk = take_lp()
    nonce = take_lp()
    features = take_lp()
    baddr = take_lp().decode("utf-8")

    return Hello(
        sig_alg_id=sig_alg_id,
        sig_pub=sig_pub,
        kem_alg_id=kem_alg_id,
        kem_ephemeral_pub=kem_epk,
        nonce=nonce,
        features_json=features,
        bech32_addr=baddr,
    )


def encode_auth(sig_alg_id: int, signature: bytes) -> bytes:
    return AUTH_MAGIC + _lp_u16(sig_alg_id) + _lp(signature)


def decode_auth(buf: bytes) -> Auth:
    if not buf.startswith(AUTH_MAGIC):
        raise HandshakeError("AUTH magic mismatch")
    i = len(AUTH_MAGIC)
    if i + 2 > len(buf):
        raise HandshakeError("truncated AUTH")
    sig_alg_id = int.from_bytes(buf[i : i + 2], "big")
    i += 2
    if i + 2 > len(buf):
        raise HandshakeError("truncated AUTH (lp)")
    l = int.from_bytes(buf[i : i + 2], "big")
    i += 2
    if i + l > len(buf):
        raise HandshakeError("truncated AUTH (sig)")
    sig = buf[i : i + l]
    return Auth(sig_alg_id=sig_alg_id, signature=sig)


# --------------------------------------------------------------------------------------
# Transcript & KDF helpers
# --------------------------------------------------------------------------------------


def transcript_hash(hello_i: bytes, hello_r: bytes) -> bytes:
    """
    H(DOMAIN_HELLO || LP(hello_i) || LP(hello_r))
    """
    return sha3_256(DOMAIN_HELLO + _lp(hello_i) + _lp(hello_r))


def kdf_info(our_epk: bytes, peer_epk: bytes, th: bytes) -> bytes:
    """
    Order-stable info:
      KDF_LABEL || LP(min(epk_a, epk_b)) || LP(max(epk_a, epk_b)) || LP(th)
    """
    a, b = (our_epk, peer_epk) if our_epk <= peer_epk else (peer_epk, our_epk)
    return KDF_LABEL + _lp(a) + _lp(b) + _lp(th)


def mix_shared_secrets(ss_a: bytes, ss_b: bytes) -> bytes:
    """
    s_mix = H( LP(ss_a) || LP(ss_b) ); commutative fold.
    """
    return sha3_256(_lp(ss_a) + _lp(ss_b))


# --------------------------------------------------------------------------------------
# Address & identity helpers
# --------------------------------------------------------------------------------------


def derive_address(sig_alg_id: int, sig_pub: bytes) -> str:
    """
    anim1... bech32m address per Animica rule:
       payload = u16(sig_alg_id) || sha3_256(sig_pub)
       addr = bech32m("anim", payload)
    """
    payload = _lp_u16(sig_alg_id) + sha3_256(sig_pub)
    return _b32.bech32m_encode(hrp="anim", data=payload)


# --------------------------------------------------------------------------------------
# High-level handshake building blocks
# --------------------------------------------------------------------------------------


@dataclass
class LocalIdentity:
    sig_alg: str  # "dilithium3" | "sphincs_shake_128s"
    sig_pk: bytes
    sig_sk: bytes


@dataclass
class EphemeralKem:
    pk: bytes
    sk: bytes


def make_ephemeral_kem(seed: Optional[bytes] = None) -> EphemeralKem:
    epk, esk = pq_kem.keygen(seed=seed)
    return EphemeralKem(pk=epk, sk=esk)


def build_hello(
    identity: LocalIdentity,
    e: EphemeralKem,
    *,
    features: Optional[Dict[str, Any]] = None,
    nonce: Optional[bytes] = None,
) -> Tuple[Hello, bytes]:
    """
    Construct a HELLO object and its encoded bytes.
    """
    if features is None:
        features = {}
    features_json = _canon_json(features)
    if nonce is None:
        nonce = os_random(32)
    sig_alg_id = ALG_ID[identity.sig_alg]
    b32 = derive_address(sig_alg_id, identity.sig_pk)
    hello = Hello(
        sig_alg_id=sig_alg_id,
        sig_pub=identity.sig_pk,
        kem_alg_id=KEM_ALG_ID,
        kem_ephemeral_pub=e.pk,
        nonce=nonce,
        features_json=features_json,
        bech32_addr=b32,
    )
    return hello, hello.encode()


def sign_auth(identity: LocalIdentity, th: bytes) -> Auth:
    sig = pq_sign.sign(
        identity.sig_alg, identity.sig_sk, message=th, domain=DOMAIN_AUTH
    )
    return Auth(sig_alg_id=ALG_ID[identity.sig_alg], signature=sig)


def verify_auth(peer_hello: Hello, auth: Auth, th: bytes) -> None:
    if auth.sig_alg_id != peer_hello.sig_alg_id:
        raise HandshakeError("peer AUTH sig_alg_id mismatch with HELLO")
    name = ALG_NAME[auth.sig_alg_id]
    ok = pq_verify.verify(
        name,
        peer_hello.sig_pub,
        message=th,
        signature=auth.signature,
        domain=DOMAIN_AUTH,
    )
    if not ok:
        raise HandshakeError("peer AUTH signature invalid")


# --------------------------------------------------------------------------------------
# Orchestrated flows (role-specific)
# --------------------------------------------------------------------------------------


def initiator_handshake(
    local: LocalIdentity,
    peer_hello_bytes: Optional[bytes] = None,
    *,
    features: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    One-shot initiator flow helper for higher layers.

    Returns a dict with:
      {
        "hello_bytes": bytes,
        # After you receive peer hello:
        "respond(peer_hello_bytes) -> dict"  # closure: returns out messages & key result
      }
    """
    e = make_ephemeral_kem()
    hello_obj, hello_bytes = build_hello(local, e, features=features)

    def respond(peer_hello_bytes_: bytes) -> Dict[str, Any]:
        peer_hello = decode_hello(peer_hello_bytes_)
        if peer_hello.kem_alg_id != KEM_ALG_ID:
            raise HandshakeError("peer uses unsupported KEM")
        # Transcript hash ordered by role
        th = transcript_hash(hello_bytes, peer_hello_bytes_)
        # Auth (initiator)
        auth_i = sign_auth(local, th).encode()
        # KEM: I encapsulates to R
        ct_i, ss_i = pq_kem.encapsulate(peer_hello.kem_ephemeral_pub)

        # Wait for peer's AUTH and ct, then verify/decapsulate
        def finalize(peer_auth_bytes: bytes, peer_ct: bytes) -> HandshakeResult:
            auth_r = decode_auth(peer_auth_bytes)
            verify_auth(peer_hello, auth_r, th)
            ss_r = pq_kem.decapsulate(e.sk, peer_ct)
            s_mix = mix_shared_secrets(ss_i, ss_r)
            info = kdf_info(
                hello_obj.kem_ephemeral_pub, peer_hello.kem_ephemeral_pub, th
            )
            k0, k1 = pq_kem.derive_symmetric_keys(
                s_mix,
                our_pub=hello_obj.kem_ephemeral_pub,
                peer_pub=peer_hello.kem_ephemeral_pub,
                transcript=th,
                n_keys=2,
                key_len=32,
            )
            # Initiator mapping
            send_key, recv_key = k0, k1
            return HandshakeResult(
                role="initiator",
                peer_sig_alg_id=peer_hello.sig_alg_id,
                peer_sig_pub=peer_hello.sig_pub,
                peer_addr=peer_hello.bech32_addr,
                transcript_hash=th,
                send_key=send_key,
                recv_key=recv_key,
                our_ct=ct_i,
                peer_ct=peer_ct,
            )

        return {
            "auth_bytes": auth_i,
            "ct_bytes": ct_i,
            "finalize": finalize,
            "peer": {
                "addr": peer_hello.bech32_addr,
                "sig_alg_id": peer_hello.sig_alg_id,
                "sig_alg": ALG_NAME[peer_hello.sig_alg_id],
            },
            "transcript_hash": th,
        }

    out = {"hello_bytes": hello_bytes, "respond": respond}
    if peer_hello_bytes is not None:
        # Convenience: if caller passed peer hello immediately, return the respond() payload
        return {"hello_bytes": hello_bytes, **respond(peer_hello_bytes)}
    return out


def responder_handshake(
    local: LocalIdentity,
    peer_hello_bytes: bytes,
    *,
    features: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Responder flow: take peer HELLO, return our HELLO + AUTH + ct, and a finalizer
    to accept the initiator's AUTH + ct.
    """
    peer_hello = decode_hello(peer_hello_bytes)
    if peer_hello.kem_alg_id != KEM_ALG_ID:
        raise HandshakeError("peer uses unsupported KEM")

    e = make_ephemeral_kem()
    hello_obj, hello_bytes = build_hello(local, e, features=features)

    th = transcript_hash(peer_hello_bytes, hello_bytes)  # ordered (I, R)
    # AUTH (responder)
    auth_r = sign_auth(local, th).encode()
    # KEM: R encapsulates to I
    ct_r, ss_r = pq_kem.encapsulate(peer_hello.kem_ephemeral_pub)

    def finalize(peer_auth_bytes: bytes, peer_ct: bytes) -> HandshakeResult:
        auth_i = decode_auth(peer_auth_bytes)
        verify_auth(peer_hello, auth_i, th)
        ss_i = pq_kem.decapsulate(e.sk, peer_ct)
        s_mix = mix_shared_secrets(ss_i, ss_r)
        info = kdf_info(
            hello_obj.kem_ephemeral_pub, peer_hello.kem_ephemeral_pub, th
        )  # unused, kept for clarity
        k0, k1 = pq_kem.derive_symmetric_keys(
            s_mix,
            our_pub=hello_obj.kem_ephemeral_pub,
            peer_pub=peer_hello.kem_ephemeral_pub,
            transcript=th,
            n_keys=2,
            key_len=32,
        )
        # Responder mapping
        send_key, recv_key = k1, k0
        return HandshakeResult(
            role="responder",
            peer_sig_alg_id=peer_hello.sig_alg_id,
            peer_sig_pub=peer_hello.sig_pub,
            peer_addr=peer_hello.bech32_addr,
            transcript_hash=th,
            send_key=send_key,
            recv_key=recv_key,
            our_ct=ct_r,
            peer_ct=peer_ct,
        )

    return {
        "hello_bytes": hello_bytes,
        "auth_bytes": auth_r,
        "ct_bytes": ct_r,
        "finalize": finalize,
        "peer": {
            "addr": peer_hello.bech32_addr,
            "sig_alg_id": peer_hello.sig_alg_id,
            "sig_alg": ALG_NAME[peer_hello.sig_alg_id],
        },
        "transcript_hash": th,
    }


# --------------------------------------------------------------------------------------
# Offline self-test (no I/O): simulate both sides
# --------------------------------------------------------------------------------------


def _self_test() -> None:  # pragma: no cover
    from pq.py import keygen as pq_keygen

    # Long-term identities
    skI, pkI = pq_keygen.keygen("dilithium3")
    skR, pkR = pq_keygen.keygen("dilithium3")
    I = LocalIdentity(sig_alg="dilithium3", sig_pk=pkI, sig_sk=skI)
    R = LocalIdentity(sig_alg="dilithium3", sig_pk=pkR, sig_sk=skR)

    # Initiator starts
    i0 = initiator_handshake(I)
    hello_I = i0["hello_bytes"]

    # Responder replies
    r0 = responder_handshake(R, hello_I)
    hello_R = r0["hello_bytes"]

    # Initiator processes responder hello, sends auth+ct
    i1 = i0["respond"](hello_R)
    auth_I, ct_I = i1["auth_bytes"], i1["ct_bytes"]

    # Responder finalizes with initiator's auth+ct; sends auth+ct back (already produced)
    resR: HandshakeResult = r0["finalize"](auth_I, ct_I)
    auth_R, ct_R = r0["auth_bytes"], r0["ct_bytes"]

    # Initiator finalizes with responder's auth+ct
    resI: HandshakeResult = i1["finalize"](auth_R, ct_R)

    assert resI.send_key and resI.recv_key
    assert resR.send_key and resR.recv_key
    # Cross-check keys
    assert resI.send_key == resR.recv_key
    assert resI.recv_key == resR.send_key
    # Transcript agreement
    assert resI.transcript_hash == r0["transcript_hash"]

    print("Self-test OK")
    print("I send =", resI.send_key.hex())
    print("I recv =", resI.recv_key.hex())
    print("R send =", resR.send_key.hex())
    print("R recv =", resR.recv_key.hex())


if __name__ == "__main__":  # pragma: no cover
    _self_test()
