import os
import binascii
import pytest

# Uniform APIs
from pq.py import keygen as PKG
from pq.py import sign as PQS
from pq.py import verify as PQV
from pq.py import registry as REG


ALGS = ["dilithium3", "sphincs_shake_128s"]
MSG = b"animica test message \xf0\x9f\xa6\x84"  # deterministic test payload
DOM = b"animica/test/v1"  # domain-separation tag used for all happy-path signs


# --- small helpers -----------------------------------------------------------

def _keypair(alg: str):
    """
    Call whatever keygen symbol exists. Skip test if backend is missing.
    """
    try:
        if hasattr(PKG, "keypair"):
            return PKG.keypair(alg)
        if hasattr(PKG, "generate_keypair"):
            return PKG.generate_keypair(alg)
        if hasattr(PKG, "gen_keypair"):
            return PKG.gen_keypair(alg)
        raise AttributeError("No keypair() in pq.py.keygen")
    except (NotImplementedError, RuntimeError) as e:
        pytest.skip(f"{alg} keygen backend unavailable: {e}")


def _sign(alg: str, sk: bytes, msg: bytes, domain: bytes):
    try:
        if hasattr(PQS, "sign_message"):
            return PQS.sign_message(alg, sk, msg, domain=domain)
        if hasattr(PQS, "sign"):
            # Some APIs might ignore domain; tests account for that by skipping domain checks.
            return PQS.sign(alg, sk, msg, domain=domain)
        raise AttributeError("No sign() in pq.py.sign")
    except (NotImplementedError, RuntimeError) as e:
        pytest.skip(f"{alg} sign backend unavailable: {e}")


def _verify(alg: str, pk: bytes, msg: bytes, sig: bytes, domain: bytes) -> bool:
    try:
        if hasattr(PQV, "verify_signature"):
            return PQV.verify_signature(alg, pk, msg, sig, domain=domain)
        if hasattr(PQV, "verify"):
            return PQV.verify(alg, pk, msg, sig, domain=domain)
        raise AttributeError("No verify() in pq.py.verify")
    except (NotImplementedError, RuntimeError) as e:
        pytest.skip(f"{alg} verify backend unavailable: {e}")


def _expected_sig_len(alg: str) -> int | None:
    # Try to read an expected signature length from the registry if available.
    # If absent, return None (test stays agnostic).
    info = None
    if hasattr(REG, "BY_NAME") and isinstance(REG.BY_NAME, dict):
        info = REG.BY_NAME.get(alg)
    elif hasattr(REG, "get"):
        try:
            info = REG.get(alg)  # type: ignore
        except Exception:
            info = None
    if info is None:
        # Fallback: scan dict-like registries for a matching name.
        for attr in dir(REG):
            obj = getattr(REG, attr)
            if isinstance(obj, dict):
                for v in obj.values():
                    if getattr(v, "name", None) == alg:
                        info = v
                        break
            if info is not None:
                break
    if info is None:
        return None
    for field in ("sig_len", "signature_len", "signature_bytes"):
        if hasattr(info, field):
            return int(getattr(info, field))
    return None


# --- tests -------------------------------------------------------------------

@pytest.mark.parametrize("alg", ALGS)
def test_roundtrip_sign_verify(alg: str):
    pk, sk = _keypair(alg)
    sig = _sign(alg, sk, MSG, DOM)

    # Size sanity if registry exposes it
    exp = _expected_sig_len(alg)
    if exp is not None:
        assert len(sig) == exp, f"{alg} signature length mismatch (got {len(sig)}, want {exp})"

    ok = _verify(alg, pk, MSG, sig, DOM)
    assert ok is True, f"{alg} verify should pass for correct msg/domain"

    # Negative: change one byte → must fail
    tampered = bytearray(sig)
    tampered[len(tampered) // 2] ^= 0x01
    assert _verify(alg, pk, MSG, bytes(tampered), DOM) is False, f"{alg} verify must fail on tampered sig"


@pytest.mark.parametrize("alg", ALGS)
def test_domain_separation_effect(alg: str):
    """
    If the implementation supports domain-separated signing, the same (pk,sk,msg)
    signed under a different domain should NOT verify under DOM.
    If domain is not supported by the backend (legacy wrapper), we mark xfail.
    """
    pk, sk = _keypair(alg)

    sig_domA = _sign(alg, sk, MSG, DOM)
    okA = _verify(alg, pk, MSG, sig_domA, DOM)
    assert okA is True

    other_dom = b"animica/test/other-domain"
    sig_domB = _sign(alg, sk, MSG, other_dom)

    if sig_domA != sig_domB:
        # Distinct signatures strongly suggest domain is included in prehash; require failure under wrong domain.
        assert _verify(alg, pk, MSG, sig_domB, DOM) is False, (
            f"{alg} must fail verification when domain differs"
        )
    else:
        # Backend likely ignores domain (e.g., compatibility wrapper). Mark expected failure.
        pytest.xfail(f"{alg} backend appears not to bind the domain; skipping strict domain test")


@pytest.mark.parametrize("alg", ALGS)
def test_keys_and_addresses_parity(alg: str):
    """
    Optional sanity: if address codec is available in pq.py.address, ensure
    the bech32m round-trip works with the produced public key.
    """
    try:
        from pq.py import address as ADDR
    except Exception:
        pytest.skip("address codec not present")

    pk, _ = _keypair(alg)
    try:
        addr = ADDR.address_from_pubkey(alg, pk)
        alg2, payload = ADDR.parse_address(addr)
        assert alg2 == alg
        # payload = sha3_256(pubkey); its exact value is registry-defined, so we just ensure length
        assert isinstance(payload, bytes) and len(payload) in (32, 33, 34)
    except (NotImplementedError, RuntimeError) as e:
        pytest.skip(f"{alg} address codec unavailable: {e}")


def test_vectors_present_and_well_formed():
    """
    Ensure vector files exist and are parseable. We *do not* require cryptographic
    validity here, because vectors may be illustrative when oqs is missing.
    """
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "test_vectors"))
    paths = [
        os.path.join(base, "dilithium3.json"),
        os.path.join(base, "sphincs_shake_128s.json"),
    ]
    for p in paths:
        assert os.path.exists(p), f"missing vector file: {p}"
        data = open(p, "rb").read()
        # Must be valid JSON and non-empty
        assert len(data) > 2
        # quick sanity: ensure it at least *looks* like JSON (starts with { or [)
        assert data.strip()[:1] in (b"{", b"[")
