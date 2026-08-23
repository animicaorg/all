"""On-chain settlement: the exactly-once and never-sign-garbage guarantees.

Both properties here were learned from a live failure. The first settlement attempt on
mainnet was rejected by the node with "Invalid post-quantum signature" because
sign_payment_tx was called WITHOUT chain_identity: it then signs with genesis_hash=b"" and
fork_id=0 while the node verifies against the real genesis and forkId, so the preimage
can never match. The credit was correctly recorded as failed rather than silently
retried, which is what made a clean retry possible after the fix.
"""

from __future__ import annotations

import os

import pytest

from animica.ena import ENA
from animica.ena.config import load_config
from animica.ena.errors import PoolError

TREASURY = "anim1zqpTREASURYFORTESTSONLY"


@pytest.fixture()
def funded_ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    monkeypatch.setenv("ENA_TREASURY_ADDRESS", TREASURY)
    return ENA(cfg=load_config())


@pytest.fixture(autouse=True)
def _settlement_env(monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_SETTLE", "1")
    monkeypatch.setenv("ANIMICA_ENA_SETTLE_FROM", "anim1zqpTESTPAYER")


def _credit(e, pid, addr, nano, rnd=1):
    """Write a credited payout row directly, as accrue() would."""
    e.store.record_payouts(pid, rnd, [{"role": "trainer", "address": addr,
                                       "nano": int(nano), "weight": 1.0}], 1_700_000_000)


# --- refusing to sign something the node cannot verify -----------------------

def test_refuses_to_sign_without_chain_identity(funded_ena, tmp_path, monkeypatch):
    """The live failure, pinned: no chain identity -> no transaction, and no claim of
    success. Signing anyway produced a signature the node rejected."""
    e = funded_ena
    p = e.pool.create("tiny", str(_ds(tmp_path)), name="s1", num_shards=1)
    pid = p["pool_id"]
    _credit(e, pid, "anim1zqpRECIPIENT", 1_000_000_000)

    # A node that answers chain.getChainIdentity with nothing usable.
    class _RPC:
        def __init__(self, *a, **k): pass
        def call(self, method, params):
            if method == "state.getNonce":
                return 0
            if method == "chain.getChainIdentity":
                return {}          # no genesisHash
            raise AssertionError(f"must not reach {method}")

    from animica.ena import pool as poolmod
    monkeypatch.setattr(poolmod.pay, "AnimicaRPC", _RPC)

    res = e.pool._broadcast_payment("anim1zqpTESTPAYER", "anim1zqpRECIPIENT",
                                    1_000_000_000, 100)
    assert "txid" not in res
    assert "chain identity unavailable" in res["error"], res


def test_broadcast_passes_chain_identity_to_the_signer(funded_ena, tmp_path, monkeypatch):
    """The identity must reach the signer — that is the whole fix."""
    e = funded_ena
    seen = {}

    class _RPC:
        def __init__(self, *a, **k): pass
        def call(self, method, params):
            if method == "state.getNonce":
                return 7
            if method == "chain.getChainIdentity":
                return {"chainId": 1, "genesisHash": "0x" + "ab" * 32,
                        "forkId": 3511060514}
            if method == "tx.sendRawTransaction":
                return "0x" + "cd" * 32
            raise AssertionError(method)

    def _fake_sign(**kw):
        seen.update(kw)
        return "deadbeef"

    from animica.ena import pool as poolmod
    monkeypatch.setattr(poolmod.pay, "AnimicaRPC", _RPC)
    import animica.wallet.payment as wp
    monkeypatch.setattr(wp, "sign_payment_tx", _fake_sign)

    res = e.pool._broadcast_payment("anim1zqpTESTPAYER", "anim1zqpRECIPIENT",
                                    2_500_000_000, 555)
    assert res.get("txid") == "0x" + "cd" * 32
    ident = seen.get("chain_identity")
    assert ident and ident.get("genesisHash") == "0x" + "ab" * 32, seen
    assert ident.get("forkId") == 3511060514
    assert seen.get("nonce") == 7
    assert seen.get("from_address") == "anim1zqpTESTPAYER"


# --- exactly once ------------------------------------------------------------

def test_failed_settlement_is_retryable_but_sent_is_not(funded_ena, tmp_path):
    """A failed broadcast never moved money, so retrying is safe. 'sent' and 'pending'
    must never be reclaimed — retrying either could pay twice."""
    e = funded_ena
    p = e.pool.create("tiny", str(_ds(tmp_path)), name="s2", num_shards=1)
    pid = p["pool_id"]
    _credit(e, pid, "anim1zqpA", 1_000_000_000)
    payout_id = e.store.list_payouts(pid)[0].get("payout_id") or 1
    rows = e.store.unsettled_payouts(pid)
    assert len(rows) == 1
    pid_row = int(rows[0]["payout_id"])

    assert e.store.claim_settlement(pid_row, pid, "anim1zqpA", 1_000_000_000,
                                    "anim1zqpTESTPAYER", 1_700_000_000) is True
    # While pending, it must NOT be reclaimable (in-flight, outcome unknown).
    assert e.store.claim_settlement(pid_row, pid, "anim1zqpA", 1_000_000_000,
                                    "anim1zqpTESTPAYER", 1_700_000_001) is False
    assert e.store.unsettled_payouts(pid) == []

    # Broadcast failed -> retryable.
    e.store.finish_settlement(pid_row, txid=None, status="failed",
                              reason="boom", updated_at=1_700_000_002)
    assert len(e.store.unsettled_payouts(pid)) == 1, "a failed credit must be retryable"
    assert e.store.claim_settlement(pid_row, pid, "anim1zqpA", 1_000_000_000,
                                    "anim1zqpTESTPAYER", 1_700_000_003) is True

    # Sent -> never again.
    e.store.finish_settlement(pid_row, txid="0xabc", status="sent",
                              reason=None, updated_at=1_700_000_004)
    assert e.store.unsettled_payouts(pid) == []
    assert e.store.claim_settlement(pid_row, pid, "anim1zqpA", 1_000_000_000,
                                    "anim1zqpTESTPAYER", 1_700_000_005) is False, (
        "a SENT settlement was reclaimed — that is a double-payment")


def test_settlement_is_off_without_explicit_opt_in(funded_ena, tmp_path, monkeypatch):
    e = funded_ena
    p = e.pool.create("tiny", str(_ds(tmp_path)), name="s3", num_shards=1)
    pid = p["pool_id"]
    monkeypatch.delenv("ANIMICA_ENA_SETTLE", raising=False)
    assert e.pool.settle(pid)["reason"] == "settlement_disabled"
    monkeypatch.setenv("ANIMICA_ENA_SETTLE", "1")
    monkeypatch.delenv("ANIMICA_ENA_SETTLE_FROM", raising=False)
    assert e.pool.settle(pid)["reason"] == "no_payer_configured"


def test_credit_larger_than_the_allowance_is_held_not_split(funded_ena, tmp_path,
                                                            monkeypatch):
    """Never pay a fraction and lose the rest, and never exceed 10 ANM/block."""
    e = funded_ena
    p = e.pool.create("tiny", str(_ds(tmp_path)), name="s4", num_shards=1)
    pid = p["pool_id"]
    _credit(e, pid, "anim1zqpBIG", 50_000_000_000)      # 50 ANM, > one block
    sent = []
    monkeypatch.setattr(type(e.pool), "_broadcast_payment",
                        lambda self, *a: (sent.append(a) or {"txid": "0xfeed"}))
    e.pool.settle(pid, height=500)                      # initialise watermark
    r = e.pool.settle(pid, height=501)                  # 10 ANM allowance
    assert r["paid_nano"] == 0, r
    assert sent == [], "a 50 ANM credit was paid against a 10 ANM allowance"
    assert r["skipped"] and r["skipped"][0]["reason"] == "over_block_allowance"


def _ds(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text("\n".join(
        '{"prompt": "q%d", "response": "a%d"}' % (i, i) for i in range(8)),
        encoding="utf-8")
    return p
