import pytest

from pq.py import keygen, sign, verify


@pytest.fixture(autouse=True)
def enable_dev_fallback(monkeypatch):
    """Allow dev-only PQ backend so tests run without native liboqs bindings."""
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")


def _sig_keypair(seed: str = "pq-signature-test"):
    try:
        return keygen.keygen_sig("dilithium3", seed=seed)
    except NotImplementedError as exc:
        pytest.skip(f"PQ signature backend unavailable: {exc}")


def test_sign_verify_roundtrip_and_mutations():
    keypair = _sig_keypair()
    payload = b"tx: send 1 ANM to bob"
    domain = "tx/sign"
    chain_id = 99

    signature = sign.sign_detached(
        payload,
        "dilithium3",
        keypair.secret_key,
        domain=domain,
        chain_id=chain_id,
    )

    assert verify.verify_detached(
        payload,
        signature,
        keypair.public_key,
        domain=domain,
        chain_id=chain_id,
    )

    mutated_payload = payload + b"!"
    assert (
        verify.verify_detached(
            mutated_payload,
            signature,
            keypair.public_key,
            domain=domain,
            chain_id=chain_id,
        )
        is False
    )

    other_keypair = _sig_keypair(seed="pq-signature-test-alt")
    assert (
        verify.verify_detached(
            payload,
            signature,
            other_keypair.public_key,
            domain=domain,
            chain_id=chain_id,
        )
        is False
    )
