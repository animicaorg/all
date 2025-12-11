import pytest

from pq.py import keygen


@pytest.fixture(autouse=True)
def enable_dev_fallback(monkeypatch):
    """Allow dev-only PQ backend so tests run without native liboqs bindings."""

    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")


def test_keypair_sig_matches_structured_result():
    expected = keygen.keygen_sig("dilithium3", seed="sdk-compat-dili")

    sk, pk = keygen.keypair_sig("dilithium3", seed="sdk-compat-dili")

    assert sk == expected.secret_key
    assert pk == expected.public_key


def test_keypair_dispatch_accepts_sig_algorithms():
    expected = keygen.keygen_sig("sphincs_shake_128s", seed="sdk-compat-sphincs")

    sk1, pk1 = keygen.keypair("sphincs_shake_128s", seed="sdk-compat-sphincs")
    sk2, pk2 = keygen.keypair(
        "sphincs_shake_128s", seed="sdk-compat-sphincs", kind="sig"
    )

    assert (sk1, pk1) == (expected.secret_key, expected.public_key)
    assert (sk2, pk2) == (expected.secret_key, expected.public_key)


def test_keypair_rejects_kind_mismatch():
    with pytest.raises(ValueError):
        keygen.keypair("sphincs_shake_128s", kind="kem")
