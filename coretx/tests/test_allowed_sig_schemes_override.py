from __future__ import annotations

import importlib


def test_allowed_sig_schemes_override_enables_only_listed(monkeypatch):
    monkeypatch.setenv("ANIMICA_ALLOWED_SIG_SCHEMES", "2")
    monkeypatch.delenv("ANIMICA_DISABLED_SIGNATURE_SCHEMES", raising=False)

    import coretx.crypto as crypto

    importlib.reload(crypto)

    scheme1 = crypto.get_scheme(1)
    scheme2 = crypto.get_scheme(2)

    assert scheme1 is not None and scheme1.enabled is False
    assert scheme2 is not None and scheme2.enabled_by_policy is True
