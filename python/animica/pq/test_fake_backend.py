import importlib

def _reload_module(monkeypatch):
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    import animica.pq as pq  # type: ignore  # noqa: F401

    return importlib.reload(pq)


def test_fake_backend_round_trip(monkeypatch):
    pq = _reload_module(monkeypatch)

    backend, label = pq.get_backend()
    assert label == "fake"

    pk, sk = backend.keygen()
    message = b"animica pq fake signature"

    sig = backend.sign(sk, message)
    assert backend.verify(pk, message, sig)

    # Tampering the signature should fail verification
    bad_sig = bytearray(sig)
    bad_sig[-1] ^= 0x01
    assert not backend.verify(pk, message, bytes(bad_sig))
