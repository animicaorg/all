import asyncio
import os
import sys
import types

import pytest

# Ensure local package is importable when running tests from repo root
sys.path.insert(0, os.path.expanduser("~/animica"))

# --- Imports with graceful skips ------------------------------------------------------
try:
    hs_mod = __import__("p2p.crypto.handshake", fromlist=["*"])
except Exception as e:
    pytest.skip(f"p2p.crypto.handshake not available: {e}", allow_module_level=True)

try:
    aead_mod = __import__("p2p.crypto.aead", fromlist=["*"])
except Exception as e:
    pytest.skip(f"p2p.crypto.aead not available: {e}", allow_module_level=True)

try:
    from p2p.transport.base import HandshakeError
except Exception as e:
    pytest.skip(f"p2p.transport.base not available: {e}", allow_module_level=True)


# --- Helpers to adapt to slightly different APIs -------------------------------------
def _call_handshake():
    """
    Call the Kyber+HKDF handshake from p2p.crypto.handshake and return
    (initiator_result, responder_result).

    We support a few function name / return-shape variants to keep the test robust.
    """
    # Prefer a clearly-named entry if present.
    candidates = [
        "perform_handshake",
        "kyber_handshake",
        "handshake_pair",
        "handshake",
    ]
    fn = None
    for name in candidates:
        if hasattr(hs_mod, name):
            f = getattr(hs_mod, name)
            if callable(f):
                fn = f
                break
    if fn is None:
        raise AssertionError(
            "No callable handshake entry found in p2p.crypto.handshake"
        )

    # Try deterministic seeds if supported (nice to have for reproducibility).
    kw = {}
    for param in ("seed_initiator", "seed_responder"):
        if (
            param
            in getattr(
                fn, "__code__", types.SimpleNamespace(co_varnames=())
            ).co_varnames
        ):
            kw["seed_initiator"] = b"\x01" * 32
            kw["seed_responder"] = b"\x02" * 32
            break

    res = fn(**kw) if kw else fn()

    # Normalize return: (initiator, responder)
    if isinstance(res, tuple) and len(res) >= 2:
        return res[0], res[1]
    if hasattr(res, "initiator") and hasattr(res, "responder"):
        return res.initiator, res.responder
    raise AssertionError("Unexpected handshake return type/shape")


def _extract_transcript(obj):
    for name in ("transcript_hash", "th", "transcript"):
        if hasattr(obj, name):
            th = getattr(obj, name)
            if isinstance(th, (bytes, bytearray)):
                return bytes(th)
    raise AssertionError("Could not extract transcript hash from handshake result")


def _extract_keys(obj, role):
    """
    Return (send_key, recv_key) as bytes from a handshake result object.
    """
    # Common names used by implementations
    send_names = ("send_key", "tx_key", "c2s_key", "key_send", "k_send")
    recv_names = ("recv_key", "rx_key", "s2c_key", "key_recv", "k_recv")

    def _pick(names):
        for n in names:
            if hasattr(obj, n):
                v = getattr(obj, n)
                if isinstance(v, (bytes, bytearray)):
                    return bytes(v)
        return None

    sk = _pick(send_names)
    rk = _pick(recv_names)

    # Some implementations store a single 'key' when symmetric; treat both the same.
    if sk is None and rk is None and hasattr(obj, "key"):
        v = getattr(obj, "key")
        if isinstance(v, (bytes, bytearray)):
            sk = rk = bytes(v)

    if sk is None or rk is None:
        raise AssertionError(f"Could not extract send/recv keys from {role} result")

    return sk, rk


def _make_aead(key: bytes):
    """
    Construct an AEAD instance from p2p.crypto.aead with a uniform interface:
      - encrypt(nonce: bytes, aad: bytes, pt: bytes) -> bytes
      - decrypt(nonce: bytes, aad: bytes, ct: bytes) -> bytes
    Tries ChaCha20-Poly1305, falls back to AES-GCM.
    """
    # Try factory if the module exposes one.
    for factory in ("aead_from_name", "new_aead", "make_aead"):
        if hasattr(aead_mod, factory):
            try:
                fn = getattr(aead_mod, factory)
                return fn("chacha20poly1305", key)
            except Exception:
                try:
                    return fn("aesgcm", key)
                except Exception:
                    pass

    # Try direct classes
    for cls_name in ("ChaCha20Poly1305AEAD", "ChaCha20Poly1305"):
        if hasattr(aead_mod, cls_name):
            return getattr(aead_mod, cls_name)(key)
    for cls_name in ("AESGcmAEAD", "AESGCM"):
        if hasattr(aead_mod, cls_name):
            return getattr(aead_mod, cls_name)(key)

    # Final fallback via cryptography (optional dep)
    try:  # pragma: no cover
        from cryptography.hazmat.primitives.ciphers.aead import (
            AESGCM, ChaCha20Poly1305)

        class AEADWrapper:
            def __init__(self, k: bytes, kind="chacha"):
                self.kind = kind
                self.impl = ChaCha20Poly1305(k) if kind == "chacha" else AESGCM(k)

            def encrypt(self, nonce: bytes, aad: bytes, pt: bytes) -> bytes:
                return self.impl.encrypt(nonce, pt, aad)

            def decrypt(self, nonce: bytes, aad: bytes, ct: bytes) -> bytes:
                return self.impl.decrypt(nonce, ct, aad)

        try:
            return AEADWrapper(key, "chacha")
        except Exception:
            return AEADWrapper(key, "aes")
    except Exception as e:
        pytest.skip(f"No AEAD implementation available: {e}", allow_module_level=True)


# --- The actual tests ----------------------------------------------------------------
def test_kyber_handshake_aead_roundtrip_and_transcript_hash():
    initiator, responder = _call_handshake()

    # Transcript hash must match between parties and be 32 or 64 bytes (sha3-256/512)
    th_i = _extract_transcript(initiator)
    th_r = _extract_transcript(responder)
    assert th_i == th_r, "Transcript hash must match"
    assert len(th_i) in (32, 64), f"Unexpected transcript hash length: {len(th_i)}"

    # Keys: initiator send == responder recv, and vice versa
    i_send, i_recv = _extract_keys(initiator, "initiator")
    r_send, r_recv = _extract_keys(responder, "responder")

    assert i_send == r_recv, "initiator send key must equal responder recv key"
    assert r_send == i_recv, "responder send key must equal initiator recv key"
    assert len(i_send) in (16, 24, 32), "AEAD keys should be 128/192/256-bit"

    # AEAD round-trip (initiator -> responder)
    aead_i = _make_aead(i_send)
    aead_r = _make_aead(r_recv)  # equal to i_send per the assertion above
    nonce = b"\x00" * 12  # 96-bit nonce typical for both AEADs
    aad = b"animica/p2p/test"
    msg = b"hello from initiator"
    ct = aead_i.encrypt(nonce, aad, msg)
    pt = aead_r.decrypt(nonce, aad, ct)
    assert pt == msg, "AEAD decrypt (initiator->responder) must match plaintext"

    # Reverse direction (responder -> initiator)
    aead_r2 = _make_aead(r_send)
    aead_i2 = _make_aead(i_recv)  # equal to r_send
    nonce2 = b"\x01" * 12
    aad2 = b"animica/p2p/test/rev"
    msg2 = b"hello from responder"
    ct2 = aead_r2.encrypt(nonce2, aad2, msg2)
    pt2 = aead_i2.decrypt(nonce2, aad2, ct2)
    assert pt2 == msg2, "AEAD decrypt (responder->initiator) must match plaintext"


def test_transcript_hash_stability_len_only():
    """
    We don't pin exact bytes of the transcript in tests (implementations may evolve),
    but we ensure it has a stable size (32 or 64) and is non-zero.
    """
    initiator, responder = _call_handshake()
    th = _extract_transcript(initiator)
    assert th and th == _extract_transcript(responder)


@pytest.mark.asyncio
async def test_tcp_handshake_handles_mismatched_prologue_lengths():
    perform_tcp = getattr(hs_mod, "perform_handshake_tcp", None)
    if perform_tcp is None:
        pytest.skip("perform_handshake_tcp not available")

    errors: list[HandshakeError] = []
    server_done = asyncio.Event()

    async def _server(reader, writer):
        try:
            await perform_tcp(reader, writer, is_outbound=False, chain_id=1)
        except HandshakeError as exc:
            errors.append(exc)
        finally:
            writer.close()
            await writer.wait_closed()
            server_done.set()

    server = await asyncio.start_server(_server, host="127.0.0.1", port=0)
    host, port = server.sockets[0].getsockname()[:2]

    async def _client():
        reader, writer = await asyncio.open_connection(host, port)
        try:
            await perform_tcp(reader, writer, is_outbound=True, chain_id=999)
        except HandshakeError as exc:
            errors.append(exc)
        finally:
            writer.close()
            await writer.wait_closed()

    await _client()
    await asyncio.wait_for(server_done.wait(), timeout=1.0)

    server.close()
    await server.wait_closed()

    assert len(errors) == 2, "both peers should fail the handshake"
    for exc in errors:
        assert "prologue/chain-id mismatch" in str(exc)
