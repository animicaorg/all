from animica.tx.crypto import verify


def test_verify_rejects_wrong_lengths_dilithium3():
    r = verify(alg_id=0x1001, msg=b"m", signature=b"x" * 10, pubkey=b"y" * 32, sign_hash=b"h" * 64)
    assert not r.ok
    assert r.reason == "invalid_signature"
    assert r.scheme_id == 0x1001


def test_verify_rejects_unknown_scheme():
    r = verify(alg_id=12345, msg=b"m", signature=b"", pubkey=b"", sign_hash=b"h" * 64)
    assert not r.ok
    assert r.reason == "invalid_signature"
