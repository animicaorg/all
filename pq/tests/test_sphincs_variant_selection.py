"""Tests for deterministic SPHINCS+ mechanism selection across liboqs versions."""

import importlib
import sys
from types import SimpleNamespace

import pytest


def _fake_oqs(enabled_names: list[str]):
    """Build a minimal fake oqs module exposing the mechanisms provided."""

    class FakeSignature:
        def __init__(self, mechanism=None, secret_key=None, public_key=None):
            self.mech = mechanism
            self.length_public_key = 32
            self.length_secret_key = 64
            self.length_signature = 7856

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def generate_keypair(self):
            return b"pk"

        def export_secret_key(self):
            return b"sk"

        def sign(self, message):  # pragma: no cover - unused in these tests
            return b"sig" + (self.mech or "").encode()

        def verify(self, message, sig):  # pragma: no cover - unused in these tests
            return True

    return SimpleNamespace(
        get_enabled_sig_mechanisms=lambda: enabled_names,
        Signature=FakeSignature,
    )


@pytest.fixture(autouse=True)
def _cleanup_module_cache(monkeypatch):
    """Ensure sphincs module is re-imported fresh for each test."""

    yield
    monkeypatch.setitem(sys.modules, "oqs", sys.modules.get("oqs", None))
    sys.modules.pop("pq.py.algs.sphincs_shake_128s", None)


def test_prefers_robust_variant_when_both_available(monkeypatch):
    """Default selection should choose robust to match older node builds."""

    fake = _fake_oqs([
        "SPHINCS+-SHAKE-128s-simple",
        "SPHINCS+-SHAKE-128s-robust",
    ])
    monkeypatch.setitem(sys.modules, "oqs", fake)
    monkeypatch.delenv("ANIMICA_SPHINCS_VARIANT", raising=False)
    sys.modules.pop("pq.py.algs.sphincs_shake_128s", None)

    mod = importlib.import_module("pq.py.algs.sphincs_shake_128s")

    assert mod._OQS_MECH == "SPHINCS+-SHAKE-128s-robust"
    assert mod.sizes["pk"] == 32


def test_env_can_force_simple_variant(monkeypatch):
    """ANIMICA_SPHINCS_VARIANT=simple should prefer the simple parameter set."""

    fake = _fake_oqs([
        "SPHINCS+-SHAKE-128s-robust",
        "SPHINCS+-SHAKE-128s-simple",
    ])
    monkeypatch.setitem(sys.modules, "oqs", fake)
    monkeypatch.setenv("ANIMICA_SPHINCS_VARIANT", "simple")
    sys.modules.pop("pq.py.algs.sphincs_shake_128s", None)

    mod = importlib.import_module("pq.py.algs.sphincs_shake_128s")

    assert mod._OQS_MECH == "SPHINCS+-SHAKE-128s-simple"
    assert mod.sizes["sig"] == 7856


def _oqs_backend_with(monkeypatch, enabled_names: list[str]):
    """Build an OQSBackend instance with a patched mechanism probe set."""

    from pq.py.algs import oqs_backend

    backend = oqs_backend.OQSBackend.__new__(oqs_backend.OQSBackend)

    def fake_probe(mech: bytes) -> bool:
        return mech.decode("ascii") in enabled_names

    monkeypatch.setattr(oqs_backend.OQSBackend, "_probe_sig_mechanism", staticmethod(fake_probe))
    return oqs_backend, backend


def test_oqs_backend_prefers_robust_when_both_available(monkeypatch):
    oqs_backend, backend = _oqs_backend_with(
        monkeypatch,
        ["SPHINCS+-SHAKE-128s-simple", "SPHINCS+-SHAKE-128s-robust"],
    )
    monkeypatch.delenv("ANIMICA_SPHINCS_VARIANT", raising=False)

    assert (
        backend._select_sphincs_mechanism()
        == oqs_backend.ALG_SPHINCS_SHAKE_128S_ROBUST
    )


def test_oqs_backend_env_forces_simple(monkeypatch):
    oqs_backend, backend = _oqs_backend_with(
        monkeypatch,
        ["SPHINCS+-SHAKE-128s-robust", "SPHINCS+-SHAKE-128s-simple"],
    )
    monkeypatch.setenv("ANIMICA_SPHINCS_VARIANT", "simple")

    assert backend._select_sphincs_mechanism() == oqs_backend.ALG_SPHINCS_SHAKE_128S
