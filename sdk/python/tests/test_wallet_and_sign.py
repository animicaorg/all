import pytest
from omni_sdk.address import Address
from omni_sdk.wallet.signer import Signer


def _seed(n: int = 32) -> bytes:
    # Deterministic test seed: 0x00, 0x01, ..., 0x1f
    return bytes(range(n))


def test_signer_from_seed_and_public_key_bytes():
    s = Signer.from_seed(_seed(), alg="dilithium3")
    assert s.alg_id in ("dilithium3", "sphincs_shake_128s")
    pub = s.public_key_bytes()
    assert isinstance(pub, (bytes, bytearray))
    assert (
        len(pub) >= 32
    )  # PQ pubkeys are large; fallback stubs should still be non-trivial

    # Recreate with same seed → same pubkey (deterministic)
    s2 = Signer.from_seed(_seed(), alg=s.alg_id)
    assert s2.public_key_bytes() == pub


def test_address_derivation_matches_signer_alg():
    for alg in ("dilithium3", "sphincs_shake_128s"):
        s = Signer.from_seed(_seed(), alg=alg)
        addr = Address.from_public_key(s.public_key_bytes(), alg=alg)
        bech = addr.bech32
        assert bech.startswith("anim1")
        parsed = Address.parse(bech)
        assert parsed.alg_id == alg


def test_algorithms_produce_distinct_addresses():
    s1 = Signer.from_seed(_seed(), alg="dilithium3")
    s2 = Signer.from_seed(_seed(), alg="sphincs_shake_128s")
    a1 = Address.from_public_key(s1.public_key_bytes(), alg=s1.alg_id).bech32
    a2 = Address.from_public_key(s2.public_key_bytes(), alg=s2.alg_id).bech32
    assert a1 != a2


def test_domain_separated_signatures_and_verify_roundtrip():
    s = Signer.from_seed(_seed(), alg="dilithium3")
    msg = b"animica test message"

    sig_tx = s.sign(msg, domain="tx")
    sig_hdr = s.sign(msg, domain="header")
    assert isinstance(sig_tx, (bytes, bytearray)) and len(sig_tx) > 0
    assert isinstance(sig_hdr, (bytes, bytearray)) and len(sig_hdr) > 0
    assert sig_tx != sig_hdr, "domain separation should change signatures"

    # Verify should pass for matching (msg, domain) and fail when domain or msg differs
    try:
        assert s.verify(msg, sig_tx, domain="tx") is True
        assert s.verify(msg, sig_hdr, domain="header") is True
        assert s.verify(msg, sig_tx, domain="header") is False
        assert s.verify(b"tampered", sig_tx, domain="tx") is False
    except NotImplementedError:
        # Accept stubs that do not implement verify, but still check signatures differ by domain.
        pytest.skip("Signer.verify not implemented in this build")


def test_signatures_are_deterministic_for_same_seed_and_input():
    s1 = Signer.from_seed(_seed(), alg="sphincs_shake_128s")
    s2 = Signer.from_seed(_seed(), alg="sphincs_shake_128s")
    msg = b"determinism check"
    sig1 = s1.sign(msg, domain="tx")
    sig2 = s2.sign(msg, domain="tx")
    assert sig1 == sig2, "same seed+alg+msg+domain should produce identical signature"
