"""ANM-M07 / ANM-C01 regression: forward-only gated block-import signature check.

At/after the pq_hardening activation height, block import verifies every
non-coinbase tx signature (fail-closed), closing the hostile-miner drain that
bypasses mempool verification (the executor derives the debited account from the
tx's signature pubkey but never checks the signature). Below the height, history
is grandfathered and the scanner never runs.

These tests cover the gating/wiring logic deterministically. The signature
correctness itself is delegated to the exact P2P/mempool verify path
(rpc.methods.tx._verify_pq_signature), which is already exercised by the p2p and
mempool test suites; shadow mode provides the operational pre-activation check.
"""
import sys
import types

import pytest

from core.chain import block_import as bi


class _FakeBlock:
    def __init__(self, txs):
        self.txs = txs


MAINNET = 1
H = 37000  # mainnet pq_hardening activation height (core.network_params)


def test_grandfathered_below_activation_height(monkeypatch):
    def _boom(block, chain_id):
        raise AssertionError("scanner ran below activation height (should be grandfathered)")

    monkeypatch.setattr(bi, "_scan_block_tx_signatures", _boom)
    assert bi._verify_block_tx_signatures_gated(_FakeBlock([]), H - 1, MAINNET) is None


def test_enforced_at_activation_height(monkeypatch):
    monkeypatch.setattr(bi, "_scan_block_tx_signatures", lambda b, c: "tx_sig_invalid[0]:X")
    assert (
        bi._verify_block_tx_signatures_gated(_FakeBlock([]), H, MAINNET)
        == "tx_sig_invalid[0]:X"
    )


def test_valid_block_accepted_at_height(monkeypatch):
    monkeypatch.setattr(bi, "_scan_block_tx_signatures", lambda b, c: None)
    assert bi._verify_block_tx_signatures_gated(_FakeBlock([]), H, MAINNET) is None


def test_shadow_mode_observes_only(monkeypatch):
    monkeypatch.setenv("ANIMICA_PQ_HARDENING_SHADOW", "1")
    monkeypatch.setattr(bi, "_scan_block_tx_signatures", lambda b, c: "tx_sig_invalid")
    # Would reject, but shadow mode logs and accepts.
    assert bi._verify_block_tx_signatures_gated(_FakeBlock([]), H, MAINNET) is None


def test_unknown_chain_never_enforced(monkeypatch):
    monkeypatch.setattr(bi, "_scan_block_tx_signatures", lambda b, c: "should_not_run")
    # Unknown chain_id -> fork never active -> no new rejection (forward-safe).
    assert bi._verify_block_tx_signatures_gated(_FakeBlock([]), 10**9, 999) is None


def test_scanner_fails_closed_when_backend_missing(monkeypatch):
    """A missing PQ verify backend must reject, never silently admit unverified."""
    fake = types.ModuleType("rpc.methods.tx")
    fake._pq_verify = None
    fake._pq_verify_tx = None
    monkeypatch.setitem(sys.modules, "rpc.methods.tx", fake)
    try:
        import rpc.methods as rpc_methods
    except Exception:
        pytest.skip("rpc.methods not importable in this environment")
    monkeypatch.setattr(rpc_methods, "tx", fake, raising=False)
    assert bi._scan_block_tx_signatures(_FakeBlock([]), MAINNET) == "pq_verify_backend_missing"
