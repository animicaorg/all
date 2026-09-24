"""Regression (11.2.3): mainnet nodes on 11.1.0-11.2.2 restart-looped because the
SPHINCS+ self-test raised when ANIMICA_ALLOW_PQ_PURE_FALLBACK was unset."""
import os

from coretx.crypto import assert_required_pq_for_chain
from pq.py.algs import pure_python_fallbacks, sphincs_shake_128s


def test_sphincs_roundtrip_without_env_flag(monkeypatch):
    monkeypatch.delenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", raising=False)
    sk, pk = sphincs_shake_128s.keypair()
    sig = sphincs_shake_128s.sign(sk, b"m")
    assert sphincs_shake_128s.verify(pk, b"m", sig)
    # The opt-in is per call; it must never leak into the process env, or the
    # mainnet wallet-keygen guard refuses every `animica wallet new`.
    assert "ANIMICA_ALLOW_PQ_PURE_FALLBACK" not in os.environ


def test_other_fallbacks_still_gated(monkeypatch):
    monkeypatch.delenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", raising=False)
    try:
        pure_python_fallbacks.fallback_sig_keypair("dilithium3")
    except NotImplementedError:
        return
    raise AssertionError("dilithium3 fallback ran without the opt-in flag")


def test_mainnet_rpc_startup_check_passes(monkeypatch):
    monkeypatch.delenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", raising=False)
    assert_required_pq_for_chain(chain_id=1, is_mainnet_rpc=True)
