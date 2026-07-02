"""Regression: tx-send force-chain must NOT mint a local no-PoW instant block
when the node has connected peers.

Such a block never propagates (peers require valid PoW), so on a networked node
it forks the local head above the real chain (an "instant tower") that grows
with every tx send until the node believes it is ahead, stops syncing, and the
watchdog resets it (the 5.2.7 exchange-node "random reset"). With peers present
the tx is relayed and will land in a real block, so the local instant block is
skipped. Isolated / single-node setups (no peers) keep the legacy behaviour.
"""

import types

import rpc.methods.tx as txmod


def _fake_ctx(peer_count):
    class _Svc:
        def peer_count(self):
            return peer_count

    return types.SimpleNamespace(p2p_service=_Svc(), core_p2p_service=None)


def test_force_chain_skips_instant_block_when_peers(monkeypatch):
    monkeypatch.setattr(txmod, "_TX_SEND_FORCE_CHAIN", True)
    monkeypatch.setattr(txmod.deps, "get_ctx", lambda: _fake_ctx(3))
    monkeypatch.setattr(txmod, "_lookup_persisted_tx", lambda h: (None,))

    calls = {"mine": 0}
    monkeypatch.setattr(
        txmod.miner_methods,
        "miner_mine",
        lambda **k: calls.__setitem__("mine", calls["mine"] + 1),
    )

    ok, reason = txmod._ensure_tx_persisted_to_chain("aa" * 32)
    assert ok is False
    assert reason == "networked_relayed_pending_real_block"
    assert calls["mine"] == 0  # never minted a local instant (tower) block


def test_force_chain_reaches_mine_when_isolated(monkeypatch):
    # peer_count == 0 -> the gate must NOT skip; the legacy mine path runs.
    monkeypatch.setattr(txmod, "_TX_SEND_FORCE_CHAIN", True)
    monkeypatch.setattr(txmod.deps, "get_ctx", lambda: _fake_ctx(0))
    monkeypatch.setattr(txmod, "_lookup_persisted_tx", lambda h: (None,))
    monkeypatch.setattr(txmod.miner_methods, "_min_block_spacing_s", lambda: 0)

    def _boom(**k):
        raise RuntimeError("reached_mine")

    monkeypatch.setattr(txmod.miner_methods, "miner_mine", _boom)

    ok, reason = txmod._ensure_tx_persisted_to_chain("bb" * 32)
    assert ok is False
    assert reason == "reached_mine"  # gate did not skip; proceeded to mine


def test_force_chain_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(txmod, "_TX_SEND_FORCE_CHAIN", False)
    ok, reason = txmod._ensure_tx_persisted_to_chain("cc" * 32)
    assert ok is False and reason is None
