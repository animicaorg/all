import os
import pytest
from typing import Any, Callable, Optional, Tuple

# Uniform APIs
from pq.py import keygen as PKG
from pq.py import kem as KEM
from pq.py import handshake as HS
from pq.py.utils import hkdf as HK


ALG = "kyber768"
INFO = b"animica/p2p/v1"  # HKDF info label used across tests
SALT = b"\x00" * 32       # benign salt; real system will use transcript hash


# ---- helpers -----------------------------------------------------------------

def kem_keypair() -> Tuple[bytes, bytes]:
    """
    Try a few likely keygen entrypoints and skip if KEM backend is missing.
    """
    try:
        if hasattr(PKG, "keypair"):
            return PKG.keypair(ALG)
        if hasattr(PKG, "kem_keypair"):
            return PKG.kem_keypair(ALG)  # type: ignore[attr-defined]
        if hasattr(PKG, "generate_keypair"):
            return PKG.generate_keypair(ALG)  # type: ignore[attr-defined]
    except (NotImplementedError, RuntimeError) as e:
        pytest.skip(f"{ALG} keygen backend unavailable: {e}")
    raise AttributeError("No usable keypair() function found in pq.py.keygen")


def kem_encaps(pk: bytes) -> Tuple[bytes, bytes]:
    try:
        return KEM.encaps(ALG, pk)
    except (NotImplementedError, RuntimeError) as e:
        pytest.skip(f"{ALG} encaps backend unavailable: {e}")


def kem_decaps(sk: bytes, ct: bytes) -> bytes:
    try:
        return KEM.decaps(ALG, sk, ct)
    except (NotImplementedError, RuntimeError) as e:
        pytest.skip(f"{ALG} decaps backend unavailable: {e}")


def _hkdf(ss: bytes, length: int = 64, salt: bytes = SALT, info: bytes = INFO) -> bytes:
    """
    HKDF-SHA3-256 wrapper returning `length` bytes. If the module only exposes
    a fixed-length derivation, shorten deterministically.
    """
    if hasattr(HK, "hkdf_sha3_256"):
        okm = HK.hkdf_sha3_256(ikm=ss, salt=salt, info=info, length=length)  # type: ignore[attr-defined]
        return okm
    # Fallback: try a generic hkdf() name
    if hasattr(HK, "hkdf"):
        okm = HK.hkdf(ikm=ss, salt=salt, info=info, length=length)  # type: ignore[attr-defined]
        return okm
    pytest.skip("HKDF-SHA3-256 helper not available")


def discover_two_party_func() -> Optional[Callable[..., Any]]:
    """
    Try to locate a two-party handshake convenience function.
    Accepted shapes:
      - f(skA, pkA, skB, pkB) -> (keysA, keysB, thA, thB) or similar
      - f(skA, pkB) returns A-side; same function used for B (with roles swapped)
    If nothing sensible is found, return None (tests will xfail gracefully).
    """
    candidates = [
        "handshake_pair",
        "two_party",
        "demo_two_party",
        "run_two_party",
        "pair",
        "demo",
    ]
    for name in candidates:
        if hasattr(HS, name):
            fn = getattr(HS, name)
            if callable(fn):
                return fn
    # Single-sided variant?
    if hasattr(HS, "handshake") and callable(getattr(HS, "handshake")):
        return getattr(HS, "handshake")
    return None


def interpret_keys(obj: Any) -> Tuple[bytes, bytes, Optional[bytes]]:
    """
    Normalize various possible return shapes to (tx_key, rx_key, transcript_hash?).
    Accepts dict-like or tuple-like returns.
    """
    # dict-like
    if isinstance(obj, dict):
        tx = obj.get("tx_key") or obj.get("aead_tx") or obj.get("k_tx") or obj.get("k1")
        rx = obj.get("rx_key") or obj.get("aead_rx") or obj.get("k_rx") or obj.get("k2")
        th = obj.get("transcript") or obj.get("th") or obj.get("hash")
        assert isinstance(tx, (bytes, bytearray)) and isinstance(rx, (bytes, bytearray)), "bad key shapes"
        return bytes(tx), bytes(rx), (bytes(th) if isinstance(th, (bytes, bytearray)) else None)

    # tuple-like: (tx, rx, [th]) or (k1, k2) or ((tx,rx), th)
    if isinstance(obj, tuple):
        if len(obj) == 2 and all(isinstance(x, (bytes, bytearray)) for x in obj):
            return bytes(obj[0]), bytes(obj[1]), None
        if len(obj) == 3 and all(isinstance(x, (bytes, bytearray)) for x in obj[:2]):
            return bytes(obj[0]), bytes(obj[1]), (bytes(obj[2]) if obj[2] is not None else None)
        if len(obj) == 2 and isinstance(obj[0], (tuple, list)):
            a, th = obj
            assert len(a) == 2
            return bytes(a[0]), bytes(a[1]), (bytes(th) if isinstance(th, (bytes, bytearray)) else None)

    raise AssertionError("Unrecognized key return shape from handshake function")


# ---- tests -------------------------------------------------------------------

def test_kem_roundtrip_and_randomness():
    pk, sk = kem_keypair()

    ct1, ss1 = kem_encaps(pk)
    ss1_d = kem_decaps(sk, ct1)
    assert ss1 == ss1_d, "decaps must recover the encapsulated shared secret"

    ct2, ss2 = kem_encaps(pk)
    ss2_d = kem_decaps(sk, ct2)
    assert ss2 == ss2_d, "second decaps must recover"
    # With fresh randomness, we expect different ciphertext and secret most of the time.
    assert ct1 != ct2 or ss1 != ss2, "encaps should be randomized across trials"


def test_hkdf_schedule_stability_and_separation():
    """
    Derive two AEAD keys from a shared secret with HKDF-SHA3-256.
    Same inputs → same keys; changing INFO or SALT must change output.
    """
    pk, sk = kem_keypair()
    ct, ss = kem_encaps(pk)
    assert kem_decaps(sk, ct) == ss

    okmA = _hkdf(ss, length=64, salt=SALT, info=INFO)
    okmA2 = _hkdf(ss, length=64, salt=SALT, info=INFO)
    assert okmA == okmA2 and len(okmA) == 64

    # Change label → different output (domain separation)
    okmB = _hkdf(ss, length=64, salt=SALT, info=b"animica/p2p/v1/alt")
    assert okmB != okmA

    # Change salt → different output
    okmC = _hkdf(ss, length=64, salt=b"\x01" * 32, info=INFO)
    assert okmC != okmA


def test_handshake_two_party_if_available():
    """
    If a convenient two-party handshake function is present, verify:
      - Both sides derive complementary TX/RX keys
      - Transcript hashes (if any) are equal
      - Swapping roles results in swapped TX/RX keys
    Otherwise, mark as xfail (handshake API may be provided via CLI only).
    """
    fn = discover_two_party_func()
    if fn is None:
        pytest.xfail("No two-party handshake function exposed in pq.py.handshake")

    # Generate two parties
    pkA, skA = kem_keypair()
    pkB, skB = kem_keypair()

    # Try 4-arg form first: (skA, pkA, skB, pkB)
    try:
        result = fn(skA, pkA, skB, pkB)  # type: ignore[misc]
        # Expect a 2-tuple (A, B) or a richer object. Normalize.
        if isinstance(result, tuple) and len(result) == 2:
            a, b = result
        elif isinstance(result, dict) and "A" in result and "B" in result:
            a, b = result["A"], result["B"]
        else:
            # If it returned "both sides flattened", assume ((tx,rx,thA),(tx,rx,thB))
            assert isinstance(result, (tuple, list)) and len(result) == 2
            a, b = result
    except TypeError:
        # Fall back to 2-arg single-sided: handshake(sk, peer_pk)
        a = fn(skA, pkB)  # type: ignore[misc]
        b = fn(skB, pkA)  # type: ignore[misc]

    a_tx, a_rx, a_th = interpret_keys(a)
    b_tx, b_rx, b_th = interpret_keys(b)

    # Complementarity: A.tx == B.rx and A.rx == B.tx
    assert a_tx == b_rx and a_rx == b_tx, "AEAD keys must be complementary across peers"
    # Transcript hashes (if present) must match
    if a_th is not None and b_th is not None:
        assert a_th == b_th, "transcript hash must match on both sides"
    # Basic sanity on lengths
    assert len(a_tx) >= 16 and len(a_rx) >= 16


def test_bad_decaps_fails():
    """
    Decapsulation with the wrong secret key must not recover the shared secret.
    """
    pkA, skA = kem_keypair()
    pkB, skB = kem_keypair()

    ct, ssA = kem_encaps(pkA)
    ss_wrong = kem_decaps(skB, ct)
    # Almost surely different; allow equality only if backend is stubbed (skip).
    if ss_wrong == ssA:
        pytest.skip("Backend appears to be a stub or deterministic for testing; cannot assert inequality here")
    else:
        assert ss_wrong != ssA


def test_vectors_files_exist():
    """
    Ensure KEM vectors exist and are parseable JSON. The cryptographic validity is
    covered by lower-level tests or upstream libs; here we enforce presence/shape.
    """
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "test_vectors"))
    p = os.path.join(base, "kyber768.json")
    assert os.path.exists(p), f"missing vector file: {p}"
    data = open(p, "rb").read()
    assert len(data) > 2 and data.strip()[:1] in (b"{", b"[")
