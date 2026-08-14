"""SDK integration tests: an in-process L2 node + the animica.l2_sdk client.

No network, no HTTP server — the SDK's ``in_process_transport`` invokes the
registered ``l2_*`` RPC handlers directly against the process-wide L2Node
singleton, so this exercises the exact code path a remote client hits minus
the socket.

    python -m pytest l2/tests/test_sdk.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from l2 import crypto  # noqa: E402
from l2.config import L2Config  # noqa: E402
from l2.constants import L2_CHAIN_ID_DEVNET, TxStatus, TxType  # noqa: E402
from l2.node import L2Node, reset_l2_node_for_tests, set_l2_node  # noqa: E402

from animica.l2_sdk import (  # noqa: E402
    AnimicaL2,
    L2RpcError,
    L2SdkError,
    L2Signer,
    in_process_transport,
)

CHAIN = L2_CHAIN_ID_DEVNET

BOB = bytes([0x55]) * 32
CAROL = bytes([0x66]) * 32
DAVE = bytes([0x77]) * 32


@pytest.fixture()
def node(tmp_path):
    crypto.reset_verifier_for_tests()
    reset_l2_node_for_tests()
    cfg = L2Config(
        enabled=True,
        mode="all",
        l2_chain_id=CHAIN,
        data_dir=str(tmp_path),
        exec_workers=2,
        sig_workers=1,
        batch_max_txs=1000,
        batch_max_ms=0,  # never age-close; tests seal via tick(force_close=True)
        batch_max_bytes=10**9,
    )
    n = L2Node(cfg)
    set_l2_node(n)
    n.start()
    yield n
    n.stop()
    reset_l2_node_for_tests()
    crypto.reset_verifier_for_tests()


@pytest.fixture()
def alice():
    return L2Signer.from_seed(bytes([0x01]) * 32)


@pytest.fixture()
def client(node, alice):
    c = AnimicaL2(signer=alice, transport=in_process_transport())
    node.sequencer.credit_genesis(alice.address, 1_000_000_000)  # 1 ANM
    return c


# ── identity + reads ─────────────────────────────────────────────────────────


def test_chain_id_is_fetched_from_node(client):
    assert client.chain_id == CHAIN


def test_balance_and_nonce_reads(client, alice):
    assert client.balance() == 1_000_000_000
    assert client.balance(alice.address) == 1_000_000_000
    assert client.balance("0x" + alice.address.hex()) == 1_000_000_000
    assert client.nonce() == 0
    assert client.pending_nonce() == 0
    root = client.state_root()
    assert root["stateRoot"].startswith("0x") and len(root["stateRoot"]) == 66


def test_account_proof_roundtrip(client, alice):
    proof = client.account_proof(alice.address)
    assert proof["address"] == "0x" + alice.address.hex()
    assert int(proof["account"]["balance"]) == 1_000_000_000
    assert isinstance(proof["siblings"], list)


# ── transfer lifecycle: balance moves and status reaches PROVEN ──────────────


def test_send_transfer_reaches_proven_and_moves_balance(node, client, alice):
    h = client.send(BOB, 25_000)

    # Admitted but not sealed: VALIDATED, and PROVEN must NOT be claimed yet.
    assert h.status()["status"] == TxStatus.VALIDATED.value
    assert node.sequencer.status_of(h.txid).status == TxStatus.VALIDATED
    with pytest.raises(TimeoutError):
        h.wait_proven(timeout=0.2, poll=0.02)

    # Seal the batch -> execute -> prove.
    batch = node.sequencer.tick(force_close=True)
    assert batch is not None

    rec = h.wait_proven(timeout=10)
    assert rec["status"] == TxStatus.PROVEN.value
    assert node.sequencer.status_of(h.txid).status == TxStatus.PROVEN

    # Soft confirmation is a weaker level, so it is also satisfied now…
    soft = h.wait_soft_confirmation(timeout=1)
    assert soft["status"] == TxStatus.PROVEN.value
    # …but L1 finality has NOT happened (no settlement in this test): the SDK
    # must not conflate proven with final.
    with pytest.raises(TimeoutError):
        h.wait_l1_finalized(timeout=0.3, poll=0.05)

    # Value conservation: bob credited, alice debited amount + fee.
    assert client.balance(BOB) == 25_000
    assert h.tx is not None and h.tx.fee > 0
    assert client.balance(alice.address) == 1_000_000_000 - 25_000 - h.tx.fee


def test_txid_matches_local_encoding(node, client):
    h = client.send(BOB, 1_000)
    assert h.txid == h.tx.txid()
    assert h.txid_hex == "0x" + h.tx.txid().hex()


# ── send_many: one signature authorizes the whole payout ─────────────────────


def test_send_many_single_signature_batch_payment(node, client, alice):
    h = client.send_many([(BOB, 1_000), (CAROL, 2_000), (DAVE, 3_000)])
    assert h.tx.tx_type == TxType.BATCH_PAYMENT
    assert len(h.tx.payload.payments) == 3
    # One tx, one nonce consumed — the high-throughput property.
    assert client.pending_nonce() == 1

    node.sequencer.tick(force_close=True)
    h.wait_proven(timeout=10)

    assert client.balance(BOB) == 1_000
    assert client.balance(CAROL) == 2_000
    assert client.balance(DAVE) == 3_000
    assert (
        client.balance(alice.address)
        == 1_000_000_000 - 6_000 - h.tx.fee
    )


def test_send_many_rejects_empty(client):
    with pytest.raises(L2SdkError):
        client.send_many([])


# ── sequential nonces without an intervening batch ───────────────────────────


def test_two_sends_before_sealing_use_pending_nonces(node, client):
    h1 = client.send(BOB, 100)
    h2 = client.send(BOB, 200)
    assert h1.tx.nonce == 0 and h2.tx.nonce == 1
    node.sequencer.tick(force_close=True)
    h1.wait_proven(timeout=10)
    h2.wait_proven(timeout=10)
    assert client.balance(BOB) == 300


# ── typed payments ───────────────────────────────────────────────────────────


def test_pay_carries_memo(node, client):
    h = client.pay(BOB, 5_000, memo="invoice:ap_12345")
    assert h.tx.tx_type == TxType.PAY
    assert h.tx.payload.memo == b"invoice:ap_12345"
    node.sequencer.tick(force_close=True)
    h.wait_proven(timeout=10)
    assert client.balance(BOB) == 5_000


def test_agent_and_inference_payments(node, client):
    h1 = client.agent_payment(BOB, 500, agent_id=b"\xaa" * 32, task_hash=b"\xbb" * 32)
    h2 = client.inference_payment(CAROL, 700, request_hash=b"\xcc" * 32, model_id="kimi-k3")
    assert h1.tx.tx_type == TxType.AGENT_PAYMENT
    assert h2.tx.tx_type == TxType.INFERENCE_PAYMENT
    assert h2.tx.payload.model_id == b"kimi-k3".ljust(32, b"\x00")
    node.sequencer.tick(force_close=True)
    h1.wait_proven(timeout=10)
    h2.wait_proven(timeout=10)
    assert client.balance(BOB) == 500
    assert client.balance(CAROL) == 700


def test_withdraw_reaches_proven(node, client):
    l1_recipient = bytes([0x99]) * 32
    h = client.withdraw(l1_recipient, 10_000)
    assert h.tx.tx_type == TxType.WITHDRAW
    node.sequencer.tick(force_close=True)
    rec = h.wait_proven(timeout=10)
    assert rec["status"] == TxStatus.PROVEN.value
    # An exit is only claimable after L1 finality — which has not happened.
    with pytest.raises(TimeoutError):
        h.wait_l1_finalized(timeout=0.3, poll=0.05)


# ── fees ─────────────────────────────────────────────────────────────────────


def test_estimate_fee_on_unsigned_draft_matches_admission(node, client, alice):
    from l2 import tx as l2tx
    from l2.constants import SigScheme

    draft = l2tx.L2Tx(
        version=1, l2_chain_id=CHAIN, tx_type=TxType.TRANSFER, sender=alice.address,
        nonce=0, fee=0, expiry=0,
        payload=l2tx.TransferPayload(BOB, 1_000, b""),
        sig_scheme=SigScheme.ML_DSA_65, pubkey=b"", signature=b"",
    )
    est = client.estimate_fee(draft)
    assert est["total"] == est["base"] + est["da"] + est["exec"]
    assert est["total"] > 0


def test_explicit_low_fee_is_rejected_by_admission(node, client):
    with pytest.raises(L2RpcError, match="fee below required"):
        client.send(BOB, 1_000, fee=1)


def test_float_amounts_are_rejected(client):
    with pytest.raises(L2SdkError):
        client.send(BOB, 0.5)  # type: ignore[arg-type]


# ── input validation ─────────────────────────────────────────────────────────


def test_bad_address_rejected(client):
    with pytest.raises(L2SdkError):
        client.send("not-an-address", 100)
    with pytest.raises(L2SdkError):
        client.send(b"\x01" * 31, 100)


def test_unknown_txid_status(client):
    assert client.status(b"\x00" * 32)["status"] == "UNKNOWN"
