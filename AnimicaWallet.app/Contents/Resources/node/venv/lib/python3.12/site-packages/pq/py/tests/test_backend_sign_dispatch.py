import sys
import types

sys.modules.setdefault(
    "oqs",
    types.SimpleNamespace(
        Signature=None,
        get_enabled_sig_mechanisms=lambda: [],
        get_enabled_mechanisms=lambda: [],
    ),
)

import pq.py.sign as sign


def test_backend_sign_calls_with_positional_args(monkeypatch):
    called = {}

    def fake_sign(sk: bytes, msg: bytes) -> bytes:
        called["args"] = (sk, msg)
        return b"ok"

    fake_backend = types.SimpleNamespace(sign=fake_sign, __name__="fake_dilithium3")
    
    # Mock _resolve_backend to return our fake backend
    def mock_resolve(alg_name):
        if alg_name == "dilithium3":
            return fake_backend
        raise NotImplementedError(f"No backend for {alg_name}")
    
    monkeypatch.setattr(sign, "_resolve_backend", mock_resolve)

    # Use a canonical 4000-byte key (dilithium3 normalization expects this)
    sk_canonical = b"s" * 4000
    sig = sign._backend_sign("dilithium3", sk_canonical, b"message")

    assert sig == b"ok"
    # Backend should receive the normalized key and message
    assert called["args"] == (sk_canonical, b"message")
