"""Executor determinism + bridge money-invariant tests.

    python -m pytest l2/tests/test_executor_bridge.py -q
"""

from __future__ import annotations

import hashlib
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from l2 import executor, tx as l2tx  # noqa: E402
from l2.bridge import Bridge, WithdrawState  # noqa: E402
from l2.constants import L2_CHAIN_ID_DEVNET, SigScheme, TxType  # noqa: E402
from l2.fees import FeeSchedule  # noqa: E402
from l2.state import Account, StateTree  # noqa: E402

CHAIN = L2_CHAIN_ID_DEVNET
FEES = FeeSchedule()


def _addr(i: int) -> bytes:
    return hashlib.sha3_256(f"acct{i}".encode()).digest()


def _transfer(sender: bytes, recipient: bytes, amount: int, nonce: int, fee: int = 10_000) -> l2tx.L2Tx:
    return l2tx.L2Tx(
        version=1,
        l2_chain_id=CHAIN,
        tx_type=TxType.TRANSFER,
        sender=sender,
        nonce=nonce,
        fee=fee,
        expiry=0,
        payload=l2tx.TransferPayload(recipient, amount, b""),
        sig_scheme=SigScheme.ML_DSA_65,
        pubkey=b"\x00" * 1952,
        signature=b"\x00" * 3309,
    )


def _fresh_ctx(balances):
    tree = StateTree()
    for addr, bal in balances.items():
        tree.set(addr, Account(balance=bal, nonce=0))
    return executor.ExecContext(tree=tree, fees=FEES, height=1)


# ── determinism: parallel == sequential, any worker count ────────────────────


def test_parallel_matches_sequential_disjoint():
    # Unique sender -> unique receiver: maximal parallelism.
    n = 200
    balances = {_addr(i): 1_000_000 for i in range(n)}
    txs = [_transfer(_addr(i), _addr(1000 + i), 100, 0) for i in range(n)]

    ctx_seq = _fresh_ctx(balances)
    r_seq = executor.execute(txs, ctx_seq, workers=1)

    roots = set()
    for w in (2, 4, 8, 16):
        ctx = _fresh_ctx(balances)
        r = executor.execute(list(txs), ctx, workers=w)
        roots.add(r.new_state_root)
        assert r.receipts == r_seq.receipts, f"receipts differ at workers={w}"
    assert roots == {r_seq.new_state_root}, "parallel roots must equal sequential"


def test_parallel_matches_sequential_hot_account():
    # Many senders all pay ONE hot account + a hot sender fanning out: heavy
    # contention collapses toward one component but must still match sequential.
    hot = _addr(9999)
    balances = {hot: 10_000_000}
    txs = []
    for i in range(50):
        s = _addr(i)
        balances[s] = 1_000_000
        txs.append(_transfer(s, hot, 1000, 0))
    # hot then fans out with a proper nonce sequence
    for j in range(10):
        txs.append(_transfer(hot, _addr(2000 + j), 500, j))

    ctx_seq = _fresh_ctx(balances)
    r_seq = executor.execute(txs, ctx_seq, workers=1)
    for w in (2, 4, 8):
        ctx = _fresh_ctx(balances)
        r = executor.execute(list(txs), ctx, workers=w)
        assert r.new_state_root == r_seq.new_state_root, f"workers={w}"
        assert [x.status for x in r.receipts] == [x.status for x in r_seq.receipts]


def test_randomized_determinism_fuzz():
    rng = random.Random(1234)
    for trial in range(20):
        naccts = rng.randint(3, 30)
        balances = {_addr(i): rng.randint(0, 1_000_000) for i in range(naccts)}
        nonces = {a: 0 for a in balances}
        txs = []
        for _ in range(rng.randint(1, 60)):
            s = _addr(rng.randrange(naccts))
            r = _addr(rng.randrange(naccts))
            amt = rng.randint(0, 5000)
            txs.append(_transfer(s, r, amt, nonces[s]))
            nonces[s] += 1  # keep per-sender nonces monotone in submit order
        ctx_seq = _fresh_ctx(balances)
        r_seq = executor.execute(txs, ctx_seq, workers=1)
        for w in (2, 8):
            ctx = _fresh_ctx(balances)
            r = executor.execute(list(txs), ctx, workers=w)
            assert r.new_state_root == r_seq.new_state_root, f"trial {trial} w={w}"


# ── invariants ───────────────────────────────────────────────────────────────


def test_no_negative_balance_and_conservation():
    a, b = _addr(1), _addr(2)
    balances = {a: 5000}
    ctx = _fresh_ctx(balances)
    ctx.fee_recipient = _addr(0)  # treasury
    total_before = sum(x.balance for x in ctx.tree.accounts().values())
    # a tries to send more than it has -> revert, no change
    r = executor.execute([_transfer(a, b, 999999, 0)], ctx, workers=1)
    assert r.receipts[0].status == "REVERTED"
    assert ctx.tree.get(b).balance == 0
    assert ctx.tree.get(a).balance == 5000
    # a valid send conserves total (fees go to treasury, still inside L2)
    r2 = executor.execute([_transfer(a, b, 1000, 0)], ctx, workers=1)
    assert r2.receipts[0].status == "SUCCESS"
    total_after = sum(x.balance for x in ctx.tree.accounts().values())
    assert total_after == total_before, "fees must stay inside L2 (treasury)"


def test_nonce_replay_rejected():
    a, b = _addr(1), _addr(2)
    ctx = _fresh_ctx({a: 1_000_000})
    r = executor.execute([_transfer(a, b, 100, 0), _transfer(a, b, 100, 0)], ctx, workers=1)
    assert r.receipts[0].status == "SUCCESS"
    assert r.receipts[1].status == "REVERTED"  # nonce 0 already spent
    assert "bad_nonce" in r.receipts[1].reason


def test_batch_payment_one_sig_many_recipients():
    s = _addr(1)
    ctx = _fresh_ctx({s: 1_000_000})
    ctx.fee_recipient = _addr(0)
    pays = [(_addr(100 + i), 1000) for i in range(20)]
    t = l2tx.L2Tx(1, CHAIN, TxType.BATCH_PAYMENT, s, 0, 50_000, 0,
                  l2tx.BatchPaymentPayload(pays), SigScheme.ML_DSA_65, b"\x00" * 1952, b"\x00" * 3309)
    r = executor.execute([t], ctx, workers=1)
    assert r.receipts[0].status == "SUCCESS"
    for i in range(20):
        assert ctx.tree.get(_addr(100 + i)).balance == 1000


def test_full_bridge_lifecycle_invariant_holds():
    """Deposit -> credit -> transfers -> withdraw -> L1-finalize -> claim, with
    the money invariant asserted at every step."""
    bridge = Bridge(CHAIN)
    alice, bob = _addr(1), _addr(2)
    treasury = _addr(0)
    tree = StateTree()

    def balsum():
        return sum(x.balance for x in tree.accounts().values())

    # 1. Alice deposits 10_000 ANM (in nanos) on L1.
    amount = 10_000 * 10**9
    l1_txid = hashlib.sha3_256(b"deposit-tx").digest()
    dep = bridge.observe_deposit(l1_txid, alice, amount, seen_height=100)
    # Not finalized yet -> cannot claim.
    assert not bridge.authorize_deposit_claim(dep.deposit_id, alice, amount)
    bridge.update_l1_head(100 + 10)  # CONFIRMED but not FINALIZED
    assert not bridge.authorize_deposit_claim(dep.deposit_id, alice, amount)
    bridge.update_l1_head(100 + 64)  # FINALIZED
    assert bridge.authorize_deposit_claim(dep.deposit_id, alice, amount)

    # 2. DEPOSIT_CLAIM credits Alice on L2.
    ctx = executor.ExecContext(tree=tree, fees=FEES, height=1, fee_recipient=treasury,
                               deposit_authorizer=bridge.authorize_deposit_claim)
    claim = l2tx.L2Tx(1, CHAIN, TxType.DEPOSIT_CLAIM, alice, 0, 0, 0,
                      l2tx.DepositClaimPayload(alice, amount, dep.deposit_id),
                      SigScheme.ML_DSA_65, b"\x00" * 1952, b"\x00" * 3309)
    r = executor.execute([claim], ctx, workers=1)
    assert r.receipts[0].status == "SUCCESS"
    bridge.mark_deposit_credited(dep.deposit_id)
    assert tree.get(alice).balance == amount
    bridge.check_invariant(balsum(), 0)  # locked == credited == balances

    # 3. Alice sends 25 ANM to Bob.
    ctx2 = executor.ExecContext(tree=tree, fees=FEES, height=2, fee_recipient=treasury)
    send = _transfer(alice, bob, 25 * 10**9, nonce=0)
    r2 = executor.execute([send], ctx2, workers=1)
    assert r2.receipts[0].status == "SUCCESS"
    bridge.check_invariant(balsum(), 0)  # fees to treasury keep it exact

    # 4. Bob withdraws 20 ANM to L1.
    ctx3 = executor.ExecContext(tree=tree, fees=FEES, height=3, fee_recipient=treasury)
    wd_amount = 20 * 10**9
    wtx = l2tx.L2Tx(1, CHAIN, TxType.WITHDRAW, bob, 0, 10_000, 0,
                    l2tx.WithdrawPayload(bob, wd_amount), SigScheme.ML_DSA_65,
                    b"\x00" * 1952, b"\x00" * 3309)
    r3 = executor.execute([wtx], ctx3, workers=1)
    assert r3.receipts[0].status == "SUCCESS"
    wd = bridge.record_withdrawal(r3.receipts[0].txid, bob, wd_amount, batch_number=3)
    bridge.check_invariant(balsum(), 0)  # burned ANM still counted as in-flight

    # 5. Batch containing the withdrawal is L1-finalized -> claimable.
    bridge.mark_batch_finalized_on_l1(3)
    assert bridge.withdrawals[wd.nullifier].state == WithdrawState.CLAIMABLE

    # 6. Bob claims on L1 -> nullifier spent, locked ANM released.
    bridge.claim_withdrawal_on_l1(wd.nullifier)
    with pytest.raises(Exception):
        bridge.claim_withdrawal_on_l1(wd.nullifier)  # double-claim blocked
    bridge.check_invariant(balsum(), 0)
    assert bridge.locked_on_l1 == amount - wd_amount


def test_deposit_double_claim_blocked():
    bridge = Bridge(CHAIN)
    a = _addr(1)
    amt = 1000 * 10**9
    dep = bridge.observe_deposit(hashlib.sha3_256(b"d").digest(), a, amt, 10)
    bridge.update_l1_head(10 + 64)
    assert bridge.authorize_deposit_claim(dep.deposit_id, a, amt)
    bridge.mark_deposit_credited(dep.deposit_id)
    # Now no longer authorized (credited).
    assert not bridge.authorize_deposit_claim(dep.deposit_id, a, amt)
    with pytest.raises(Exception):
        bridge.mark_deposit_credited(dep.deposit_id)


def test_l1_reorg_drops_unfinalized_deposit():
    bridge = Bridge(CHAIN)
    a = _addr(1)
    dep = bridge.observe_deposit(hashlib.sha3_256(b"d2").digest(), a, 500, seen_height=200)
    bridge.update_l1_head(205)  # observed only
    dropped = bridge.rollback_l1_to(199)  # reorg below where it was seen
    assert dep.deposit_id in dropped
    assert dep.deposit_id not in bridge.deposits


def test_withdrawal_replay_rejected():
    bridge = Bridge(CHAIN)
    txid = hashlib.sha3_256(b"w").digest()
    bridge.record_withdrawal(txid, _addr(1), 100, 1)
    with pytest.raises(Exception):
        bridge.record_withdrawal(txid, _addr(1), 100, 1)


def test_expiry_enforced():
    a, b = _addr(1), _addr(2)
    ctx = _fresh_ctx({a: 1_000_000})
    ctx.height = 100
    t = _transfer(a, b, 100, 0)
    t.expiry = 50  # already past
    r = executor.execute([t], ctx, workers=1)
    assert r.receipts[0].status == "REVERTED" and r.receipts[0].reason == "expired"
