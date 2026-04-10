from __future__ import annotations

import types

from omni_sdk.wallet import signer as signer_mod
from omni_sdk.wallet.signer import PQSigner


def test_uniform_keygen_accepts_structured_keypair_result(monkeypatch) -> None:
    class _FakeKeygen:
        @staticmethod
        def keygen_sig(*args, **kwargs):  # type: ignore[no-untyped-def]
            return types.SimpleNamespace(secret_key=b"fake-sk", public_key=b"fake-pk")

    monkeypatch.setattr(
        signer_mod,
        "_import_pq",
        lambda: (object(), _FakeKeygen(), object(), object()),
    )
    monkeypatch.setattr(signer_mod, "_load_module", lambda name: None)

    sk, pk = signer_mod._uniform_keygen("dilithium3", seed=b"\x01" * 32)
    assert sk == b"fake-sk"
    assert pk == b"fake-pk"


def test_uniform_keygen_supports_animica_sig_keygen_order(monkeypatch) -> None:
    class _FakeAnimicaPQ:
        @staticmethod
        def sig_keygen(seed=None):  # type: ignore[no-untyped-def]
            assert seed == b"\x02" * 32
            # animica.pq style is (public, secret)
            return b"fake-pk", b"fake-sk"

    def _raise_import_pq():  # type: ignore[no-untyped-def]
        raise RuntimeError("pq.py unavailable")

    def _fake_load(name: str):  # type: ignore[no-untyped-def]
        if name == "animica.pq":
            return _FakeAnimicaPQ()
        return None

    monkeypatch.setattr(signer_mod, "_import_pq", _raise_import_pq)
    monkeypatch.setattr(signer_mod, "_load_module", _fake_load)

    sk, pk = signer_mod._uniform_keygen("dilithium3", seed=b"\x02" * 32)
    assert sk == b"fake-sk"
    assert pk == b"fake-pk"


def test_pq_signer_from_seed_sphincs_shake_128s(monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")

    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes(range(32)))
    sig = signer.sign_tx(b"animica-signer-compat", chain_id=1, fork_id=0)

    assert signer.alg_name == "sphincs_shake_128s"
    assert isinstance(signer.public_key, bytes) and len(signer.public_key) > 0
    assert isinstance(signer.secret_key, bytes) and len(signer.secret_key) > 0
    assert isinstance(sig, bytes) and len(sig) > 0
