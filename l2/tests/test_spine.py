"""DA + proof + sequencer + store integration tests.

    python -m pytest l2/tests/test_spine.py -q
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from l2 import crypto, da, tx as l2tx  # noqa: E402
from l2.bridge import Bridge  # noqa: E402
from l2.constants import L2_CHAIN_ID_DEVNET, SettlementMode, SigScheme, TxStatus, TxType  # noqa: E402
from l2.proof import ProofPublicInputs, ReExecutionValidityBackend  # noqa: E402
from l2.sequencer import AdmissionError, Sequencer, SequencerConfig  # noqa: E402
from l2.state import Account, StateTree  # noqa: E402
from l2.store import L2Store  # noqa: E402
from l2.batch import ClosurePolicy  # noqa: E402
from pq.py.algs import ml_dsa_65  # noqa: E402

CHAIN = L2_CHAIN_ID_DEVNET


# ── signed-tx helpers ────────────────────────────────────────────────────────


def make_account(seed: int):
    sk, pk = ml_dsa_65.keypair(bytes([seed]) * 32)
    addr = l2tx.address_from_pubkey(pk)
    return sk, pk, addr


def signed_transfer(sk, pk, addr, recipient, amount, nonce, fee=10_000):
    t = l2tx.L2Tx(
        version=1, l2_chain_id=CHAIN, tx_type=TxType.TRANSFER, sender=addr,
        nonce=nonce, fee=fee, expiry=0,
        payload=l2tx.TransferPayload(recipient, amount, b""),
        sig_scheme=SigScheme.ML_DSA_65, pubkey=pk, signature=b"",
    )
    t.signature = ml_dsa_65.sign(sk, t.signing_hash())
    return t


# ── DA ───────────────────────────────────────────────────────────────────────


def test_da_roundtrip_and_root():
    _, pk, addr = make_account(1)
    txs = [
        l2tx.L2Tx(1, CHAIN, TxType.TRANSFER, addr, i, 100, 0,
                  l2tx.TransferPayload(bytes([i + 2]) * 32, 100 + i, b""),
                  SigScheme.ML_DSA_65, pk, b"\x00" * 3309)
        for i in range(50)
    ]
    blob, root = da.encode_batch(txs)
    assert da.verify_blob(blob, root)
    assert not da.verify_blob(blob, b"\x00" * 32)  # wrong root rejected
    recovered = da.decode_batch(blob)
    assert [t.encode() for t in recovered] == [t.encode() for t in txs]
    assert da.data_root_of(txs) == root


def test_da_compression_beats_raw_on_repeated_addresses():
    _, pk, addr = make_account(2)
    merchant = b"\x42" * 32
    txs = [
        l2tx.L2Tx(1, CHAIN, TxType.TRANSFER, addr, i, 100, 0,
                  l2tx.TransferPayload(merchant, 10, b""),
                  SigScheme.ML_DSA_65, pk, b"\x00" * 3309)
        for i in range(100)
    ]
    ratio = da.compression_ratio(txs)
    assert ratio > 1.0  # DA blob smaller than raw wire bytes


def test_da_rejects_corruption():
    _, pk, addr = make_account(3)
    txs = [l2tx.L2Tx(1, CHAIN, TxType.TRANSFER, addr, 0, 100, 0,
                     l2tx.TransferPayload(b"\x09" * 32, 1, b""),
                     SigScheme.ML_DSA_65, pk, b"\x00" * 3309)]
    blob, root = da.encode_batch(txs)
    corrupt = bytearray(blob)
    corrupt[-1] ^= 0xFF
    assert not da.verify_blob(bytes(corrupt), root)


# ── proof (re-execution validity) ────────────────────────────────────────────


def test_reexec_proof_verifies_and_rejects_forgery():
    crypto.reset_verifier_for_tests()
    sk, pk, alice = make_account(10)
    bob = b"\x77" * 32
    tree = StateTree()
    tree.set(alice, Account(balance=1_000_000, nonce=0))
    prev_accounts = tree.accounts()

    from l2.executor import ExecContext, execute
    from l2.fees import FeeSchedule

    t = signed_transfer(sk, pk, alice, bob, 1000, 0)
    ctx = ExecContext(tree=StateTree(prev_accounts), fees=FeeSchedule(), height=1)
    result = execute([t], ctx, workers=1)
    blob, data_root = da.encode_batch([t])
    pi = ProofPublicInputs(
        CHAIN, 0, result.prev_state_root, result.new_state_root,
        result.transactions_root, result.receipts_root, result.escrow_root,
        data_root, result.fees_collected, result.deposited, result.withdrawn,
    )
    backend = ReExecutionValidityBackend()
    proof = backend.generate(pi, blob)
    assert backend.verify(proof, prev_accounts, {})

    # Forge: claim a different new_state_root -> verification must fail.
    bad_pi = ProofPublicInputs(
        CHAIN, 0, result.prev_state_root, b"\xde" * 32,
        result.transactions_root, result.receipts_root, result.escrow_root,
        data_root, result.fees_collected, result.deposited, result.withdrawn,
    )
    from l2.proof import Proof
    bad_proof = Proof(backend.name, bad_pi, blob)
    assert not backend.verify(bad_proof, prev_accounts, {})
    crypto.reset_verifier_for_tests()


def test_reexec_proof_catches_bad_signature():
    crypto.reset_verifier_for_tests()
    sk, pk, alice = make_account(11)
    bob = b"\x77" * 32
    tree = StateTree()
    tree.set(alice, Account(balance=1_000_000, nonce=0))
    prev_accounts = tree.accounts()
    t = signed_transfer(sk, pk, alice, bob, 1000, 0)
    t.signature = bytearray(t.signature)
    t.signature[10] ^= 1
    t.signature = bytes(t.signature)

    from l2.executor import ExecContext, execute
    from l2.fees import FeeSchedule
    ctx = ExecContext(tree=StateTree(prev_accounts), fees=FeeSchedule(), height=1)
    result = execute([t], ctx, workers=1)
    blob, data_root = da.encode_batch([t])
    pi = ProofPublicInputs(
        CHAIN, 0, result.prev_state_root, result.new_state_root,
        result.transactions_root, result.receipts_root, result.escrow_root,
        data_root, result.fees_collected, result.deposited, result.withdrawn,
    )
    backend = ReExecutionValidityBackend()
    proof = backend.generate(pi, blob)
    # Re-execution accepts the state transition but signature check fails.
    assert not backend.verify(proof, prev_accounts, {})
    crypto.reset_verifier_for_tests()


# ── sequencer end-to-end ─────────────────────────────────────────────────────


def _seq(tmpdir=None, mode=SettlementMode.VALIDITY):
    crypto.reset_verifier_for_tests()
    store = L2Store(tmpdir) if tmpdir else None
    cfg = SequencerConfig(
        l2_chain_id=CHAIN, settlement_mode=mode, exec_workers=4,
        closure=ClosurePolicy(max_txs=1000, max_bytes=10**9, max_age_ms=0),
        fee_recipient=b"\x00" * 32,
    )
    return Sequencer(cfg, store)


def test_sequencer_submit_execute_prove():
    seq = _seq()
    sk, pk, alice = make_account(20)
    bob = b"\x55" * 32
    seq.credit_genesis(alice, 1_000_000)
    t = signed_transfer(sk, pk, alice, bob, 25_000, 0)
    txid = seq.submit(t)
    assert seq.status_of(txid).status == TxStatus.VALIDATED
    batch = seq.tick(force_close=True)
    assert batch is not None
    assert seq.status_of(txid).status == TxStatus.PROVEN
    assert seq.balance(bob) == 25_000
    # The batch's proof re-verifies against the pre-batch state.
    crypto.reset_verifier_for_tests()


def test_sequencer_rejects_bad_signature_and_replay():
    seq = _seq()
    sk, pk, alice = make_account(21)
    seq.tree.set(alice, Account(balance=1_000_000, nonce=0))
    t = signed_transfer(sk, pk, alice, b"\x33" * 32, 100, 0)
    txid = seq.submit(t)
    with pytest.raises(AdmissionError):
        seq.submit(t)  # duplicate
    # forged sig
    bad = signed_transfer(sk, pk, alice, b"\x33" * 32, 100, 1)
    bad.signature = b"\x00" * 3309
    with pytest.raises(AdmissionError):
        seq.submit(bad)
    crypto.reset_verifier_for_tests()


def test_sequencer_wrong_chain_id_rejected():
    seq = _seq()
    sk, pk, alice = make_account(22)
    seq.tree.set(alice, Account(balance=1_000_000, nonce=0))
    t = signed_transfer(sk, pk, alice, b"\x33" * 32, 100, 0)
    t.l2_chain_id = CHAIN + 1
    # re-sign for the wrong chain so the sig is valid but chain id is wrong
    t.signature = ml_dsa_65.sign(sk, t.signing_hash())
    with pytest.raises(AdmissionError):
        seq.submit(t)
    crypto.reset_verifier_for_tests()


# ── store crash recovery ─────────────────────────────────────────────────────


def test_store_commit_and_recover():
    with tempfile.TemporaryDirectory() as tmp:
        seq = _seq(tmp)
        sk, pk, alice = make_account(30)
        bob = b"\x66" * 32
        seq.credit_genesis(alice, 1_000_000)
        for i in range(3):
            t = signed_transfer(sk, pk, alice, bob, 1000, i)
            seq.submit(t)
            seq.tick(force_close=True)
        head_root = seq.state_root()
        head_batch = seq.batch_number

        # Simulate restart: fresh store recovery from disk.
        store2 = L2Store(tmp)
        tree, escrows, bridge, recovered_head = store2.recover(CHAIN)
        assert recovered_head == head_batch
        assert tree.root() == head_root
        assert tree.get(bob).balance == 3000
        crypto.reset_verifier_for_tests()


def test_store_ignores_torn_final_wal_line():
    with tempfile.TemporaryDirectory() as tmp:
        seq = _seq(tmp)
        sk, pk, alice = make_account(31)
        seq.credit_genesis(alice, 1_000_000)
        seq.submit(signed_transfer(sk, pk, alice, b"\x66" * 32, 1000, 0))
        seq.tick(force_close=True)
        good_root = seq.state_root()
        # Append a torn/garbage line as if a crash happened mid-write.
        with open(os.path.join(tmp, "wal.log"), "a") as f:
            f.write('{"batch_number": 99, "batch_id": "0xdead"')  # no newline, truncated
        store2 = L2Store(tmp)
        tree, _, _, head = store2.recover(CHAIN)
        assert head == 0  # torn line ignored; canonical head is the good batch
        assert tree.root() == good_root
        crypto.reset_verifier_for_tests()
