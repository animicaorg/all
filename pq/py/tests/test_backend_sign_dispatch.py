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

    fake_backend = types.SimpleNamespace(sign=fake_sign)
    monkeypatch.setitem(sys.modules, "pq.py.algs.dilithium3", fake_backend)
    import pq.py.algs as algs  # noqa: WPS433 -- imported inside test for monkeypatch

    monkeypatch.setattr(algs, "dilithium3", fake_backend)

    sig = sign._backend_sign("dilithium3", b"secret", b"message")

    assert sig == b"ok"
    assert called["args"] == (b"secret", b"message")
