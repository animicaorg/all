"""Foundation tests: codec bijectivity, tx round-trip + signing, SMT root/proofs,
signature verifier native/pure equivalence. Run:

    python -m pytest l2/tests/test_foundation.py -q
"""

from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from l2 import codec, crypto, state, tx  # noqa: E402
from l2.constants import L2_CHAIN_ID_DEVNET, SigScheme, TxType  # noqa: E402


# ── codec ────────────────────────────────────────────────────────────────────


def test_uvarint_roundtrip_and_minimality():
    for v in [0, 1, 127, 128, 255, 256, 16383, 16384, 2**32, 2**64, 2**200]:
        out = bytearray()
        codec.write_uvarint(out, v)
        got, pos = codec.read_uvarint(memoryview(bytes(out)), 0)
        assert got == v and pos == len(out)


def test_uvarint_rejects_nonminimal():
    # 0x80 0x00 decodes to 0 but is non-minimal -> must be rejected.
    with pytest.raises(codec.CodecError):
        codec.read_uvarint(memoryview(b"\x80\x00"), 0)


def test_uvarint_rejects_truncated():
    with pytest.raises(codec.CodecError):
        codec.read_uvarint(memoryview(b"\x80"), 0)


def test_amount_bounds():
    out = bytearray()
    with pytest.raises(codec.CodecError):
        codec.write_amount(out, -1)


# ── tx round-trip ────────────────────────────────────────────────────────────


def _mk_transfer(sender: bytes) -> tx.L2Tx:
    return tx.L2Tx(
        version=1,
        l2_chain_id=L2_CHAIN_ID_DEVNET,
        tx_type=TxType.TRANSFER,
        sender=sender,
        nonce=3,
        fee=10,
        expiry=0,
        payload=tx.TransferPayload(recipient=b"\x22" * 32, amount=500, memo=b"hi"),
        sig_scheme=SigScheme.ML_DSA_65,
        pubkey=b"\x00" * 1952,
        signature=b"\x00" * 3309,
    )


def test_tx_encode_decode_roundtrip():
    t = _mk_transfer(b"\x11" * 32)
    enc = t.encode()
    dec = tx.decode(enc)
    assert dec.encode() == enc
    assert dec.tx_type == TxType.TRANSFER
    assert dec.payload.amount == 500
    assert dec.payload.memo == b"hi"


def test_tx_decode_rejects_trailing_bytes():
    t = _mk_transfer(b"\x11" * 32)
    with pytest.raises(codec.CodecError):
        tx.decode(t.encode() + b"\x00")


def test_all_tx_types_roundtrip():
    s = b"\x11" * 32
    cases = [
        (TxType.PAY, tx.TransferPayload(b"\x22" * 32, 1, b"pay:inv1")),
        (TxType.WITHDRAW, tx.WithdrawPayload(b"\x33" * 32, 9)),
        (TxType.DEPOSIT_CLAIM, tx.DepositClaimPayload(b"\x44" * 32, 7, b"\x55" * 32)),
        (TxType.ESCROW_OPEN, tx.EscrowOpenPayload(b"\x66" * 32, b"\x77" * 32, 4, 100)),
        (TxType.ESCROW_RELEASE, tx.EscrowRefPayload(b"\x66" * 32)),
        (TxType.ESCROW_REFUND, tx.EscrowRefPayload(b"\x66" * 32)),
        (TxType.AGENT_PAYMENT, tx.AgentPaymentPayload(b"\x22" * 32, 2, b"\x88" * 32, b"\x99" * 32)),
        (TxType.INFERENCE_PAYMENT, tx.InferencePaymentPayload(b"\x22" * 32, 1, b"\xaa" * 32, b"\xbb" * 32)),
        (TxType.BATCH_PAYMENT, tx.BatchPaymentPayload([(b"\xcc" * 32, 1), (b"\xdd" * 32, 2)])),
    ]
    for tx_type, payload in cases:
        t = tx.L2Tx(1, L2_CHAIN_ID_DEVNET, tx_type, s, 0, 1, 0, payload,
                    SigScheme.ML_DSA_65, b"\x00" * 1952, b"\x00" * 3309)
        assert tx.decode(t.encode()).encode() == t.encode(), tx_type


def test_signing_hash_binds_chain_and_domain():
    t = _mk_transfer(b"\x11" * 32)
    h1 = tx.signing_hash_for(t.body_bytes(), L2_CHAIN_ID_DEVNET)
    h2 = tx.signing_hash_for(t.body_bytes(), L2_CHAIN_ID_DEVNET + 1)
    assert h1 != h2  # different L2 chain id -> different signing hash (no replay)
    assert len(h1) == 64  # sha3-512


def test_address_from_pubkey_matches_l1_scheme():
    pk = b"\x11" * 1952
    addr = tx.address_from_pubkey(pk)
    assert len(addr) == 32
    # deterministic
    assert addr == tx.address_from_pubkey(pk)


# ── SMT ──────────────────────────────────────────────────────────────────────


def test_empty_root_stable():
    t = state.StateTree()
    assert t.root() == state.EMPTY_ROOT


def test_smt_root_order_independent():
    a1, a2, a3 = b"\x01" * 32, b"\x02" * 32, b"\xff" * 32
    t1 = state.StateTree()
    t1.set(a1, state.Account(balance=100, nonce=1))
    t1.set(a2, state.Account(balance=200, nonce=2))
    t1.set(a3, state.Account(balance=300, nonce=3))
    t2 = state.StateTree()
    t2.set(a3, state.Account(balance=300, nonce=3))
    t2.set(a1, state.Account(balance=100, nonce=1))
    t2.set(a2, state.Account(balance=200, nonce=2))
    assert t1.root() == t2.root()  # deterministic regardless of insertion order


def test_smt_membership_proof_verifies():
    t = state.StateTree()
    accts = {bytes([i]) * 32: state.Account(balance=i * 10, nonce=i) for i in range(1, 8)}
    for a, acct in accts.items():
        t.set(a, acct)
    root = t.root()
    for a, acct in accts.items():
        proof = t.prove(a)
        assert proof.account == acct
        assert proof.verify(root), a.hex()


def test_smt_nonmembership_proof():
    t = state.StateTree()
    t.set(b"\x01" * 32, state.Account(balance=5))
    root = t.root()
    absent = b"\x02" * 32
    proof = t.prove(absent)
    assert proof.account == state.EMPTY_ACCOUNT
    assert proof.verify(root)  # proves the account is empty/absent


def test_smt_forged_proof_rejected():
    t = state.StateTree()
    t.set(b"\x01" * 32, state.Account(balance=5))
    root = t.root()
    proof = t.prove(b"\x01" * 32)
    # Claiming a higher balance under the same siblings must fail.
    assert not state.verify_proof(root, b"\x01" * 32, state.Account(balance=999), proof.siblings)


def test_account_encode_decode():
    a = state.Account(balance=2**80, nonce=42, metadata=b"\xab" * 32)
    assert state.Account.decode(a.encode()) == a


# ── signature verifier ───────────────────────────────────────────────────────


def test_verifier_backend_and_equivalence():
    crypto.reset_verifier_for_tests()
    v = crypto.get_verifier(workers=2)
    from pq.py.algs import ml_dsa_65

    sk, pk = ml_dsa_65.keypair(b"\x07" * 32)
    msg = hashlib.sha3_512(b"l2-sig-test").digest()
    sig = ml_dsa_65.sign(sk, msg)
    assert v.verify(pk, msg, sig) is True
    # tamper
    bad = bytearray(sig)
    bad[100] ^= 1
    assert v.verify(pk, msg, bytes(bad)) is False
    # batch, order-preserving
    items = [(pk, msg, sig), (pk, msg, bytes(bad)), (pk, msg, sig)]
    assert v.verify_batch(items) == [True, False, True]
    crypto.reset_verifier_for_tests()
