"""L2 (Animica 10.0.0) JSON-RPC methods.

Registered like every other method module via the ``@method`` decorator; add this
module to ``rpc/methods/__init__.py::_iter_builtin_modules`` (done as part of the
10.0.0 change). All state comes from the process-wide :class:`l2.node.L2Node`
singleton, constructed lazily on first use so importing this module never forces
an L2 node to exist on an L1-only deployment.

Addresses are accepted as ``0x``-hex 32-byte account keys or bech32m ``anim1…``
(decoded via the L1 address helper when available). Amounts are decimal strings
of integer nanos to avoid float precision loss over JSON.
"""

from __future__ import annotations

import binascii
from typing import Any, List, Optional

from rpc.errors import InvalidParams
from rpc.methods import method

from l2 import da, tx as l2tx
from l2.config import L2Config
from l2.constants import TxStatus
from l2.metrics import L2_METRICS
from l2.node import get_l2_node


# ── helpers ──────────────────────────────────────────────────────────────────


def _node():
    try:
        return get_l2_node()
    except Exception as e:  # pragma: no cover
        raise InvalidParams(f"L2 node unavailable: {e}")


def _addr(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        b = bytes(value)
        if len(b) != 32:
            raise InvalidParams("address must be 32 bytes")
        return b
    if not isinstance(value, str):
        raise InvalidParams("address must be a string")
    s = value.strip()
    if s.startswith("0x") or s.startswith("0X"):
        try:
            b = binascii.unhexlify(s[2:])
        except binascii.Error:
            raise InvalidParams("bad hex address")
        if len(b) != 32:
            raise InvalidParams("address must be 32 bytes")
        return b
    if s.startswith("anim1"):
        # Reuse the L1 bech32m decoder; the 32-byte digest is the L2 key.
        try:
            from pq.py.address import decode_address  # type: ignore

            _hrp, _alg, digest = decode_address(s)
            return bytes(digest)[:32]
        except Exception:
            raise InvalidParams("could not decode anim1 address")
    raise InvalidParams("unrecognized address format")


def _hexb(value: Any) -> bytes:
    if not isinstance(value, str):
        raise InvalidParams("expected 0x-hex string")
    s = value[2:] if value.startswith(("0x", "0X")) else value
    try:
        return binascii.unhexlify(s)
    except binascii.Error:
        raise InvalidParams("bad hex")


# ── read methods ─────────────────────────────────────────────────────────────


@method("l2_chainId", desc="L2 chain id")
def l2_chain_id() -> int:
    return _node().config.l2_chain_id


@method("l2_status", desc="L2 node/sequencer status summary")
def l2_status() -> dict:
    return _node().status()


@method("l2_getBalance", desc="L2 (instant) ANM balance for an address, in nanos")
def l2_get_balance(address: Any = None, **_: Any) -> dict:
    if address is None:
        raise InvalidParams("address required")
    a = _addr(address)
    seq = _node().sequencer
    return {
        "address": "0x" + a.hex(),
        "balance": str(seq.balance(a)),
        "nonce": seq.nonce(a),
        "pendingNonce": seq.pending_nonce(a),
        "unit": "nanos",
    }


@method("l2_getNonce", desc="Next L2 nonce (pending-aware) for an address")
def l2_get_nonce(address: Any = None, **_: Any) -> dict:
    a = _addr(address)
    seq = _node().sequencer
    return {"nonce": seq.nonce(a), "pendingNonce": seq.pending_nonce(a)}


@method("l2_getTransaction", desc="Lifecycle status of an L2 tx by id")
def l2_get_transaction(txid: Any = None, **_: Any) -> Optional[dict]:
    h = _hexb(txid)
    rec = _node().sequencer.status_of(h)
    if rec is None:
        return {"txid": "0x" + h.hex(), "status": "UNKNOWN"}
    return {
        "txid": "0x" + rec.txid.hex(),
        "status": rec.status.value,
        "batch": rec.batch_number,
        "receipt": rec.receipt_status,
        "reason": rec.reason,
        "receivedMs": rec.received_ms,
    }


@method("l2_getReceipt", desc="Execution receipt for an L2 tx by id", aliases=("l2_getTransactionReceipt",))
def l2_get_receipt(txid: Any = None, **_: Any) -> Optional[dict]:
    h = _hexb(txid)
    rec = _node().sequencer.status_of(h)
    if rec is None or rec.batch_number < 0:
        return None
    return {
        "txid": "0x" + rec.txid.hex(),
        "batch": rec.batch_number,
        "status": rec.receipt_status or ("SUCCESS" if rec.status == TxStatus.PROVEN else ""),
        "lifecycle": rec.status.value,
    }


@method("l2_sendRawTransaction", desc="Submit one signed L2 tx (0x-hex). Returns txid.")
def l2_send_raw(raw: Any = None, **_: Any) -> str:
    data = _hexb(raw)
    try:
        txid = _node().sequencer.submit_raw(data)
    except Exception as e:
        raise InvalidParams(f"admission failed: {e}")
    return "0x" + txid.hex()


@method(
    "l2_sendRawTransactions",
    desc="Submit many signed L2 txs in one call (high-throughput batch ingress).",
)
def l2_send_raw_batch(txs: Any = None, **_: Any) -> dict:
    if not isinstance(txs, list):
        raise InvalidParams("txs must be a list of 0x-hex strings")
    seq = _node().sequencer
    accepted: List[str] = []
    rejected: List[dict] = []
    for i, raw in enumerate(txs):
        try:
            txid = seq.submit_raw(_hexb(raw))
            accepted.append("0x" + txid.hex())
        except Exception as e:
            rejected.append({"index": i, "error": str(e)})
    return {"accepted": accepted, "rejected": rejected, "count": len(accepted)}


@method("l2_estimateFee", desc="Estimate the fee (nanos) for a signed-or-draft L2 tx")
def l2_estimate_fee(raw: Any = None, **_: Any) -> dict:
    t = l2tx.decode(_hexb(raw))
    return _node().sequencer.cfg.fees.estimate(t)


@method("l2_getStateRoot", desc="Current L2 state root")
def l2_get_state_root() -> dict:
    seq = _node().sequencer
    return {"batch": seq.batch_number, "stateRoot": "0x" + seq.state_root().hex()}


@method("l2_getBatch", desc="Batch header by number")
def l2_get_batch(number: Any = None, **_: Any) -> Optional[dict]:
    if number is None:
        raise InvalidParams("batch number required")
    return _node().store.load_header(int(number))


@method("l2_getBatchData", desc="DA blob (0x-hex) for a batch — reconstructs the batch")
def l2_get_batch_data(number: Any = None, **_: Any) -> Optional[dict]:
    blob = _node().store.load_blob(int(number))
    if blob is None:
        return None
    return {"number": int(number), "blob": "0x" + blob.hex(), "bytes": len(blob)}


@method("l2_getAccountProof", desc="SMT membership/non-membership proof for an address")
def l2_get_account_proof(address: Any = None, **_: Any) -> dict:
    a = _addr(address)
    seq = _node().sequencer
    proof = seq.account_proof(a)
    return {
        "address": "0x" + a.hex(),
        "stateRoot": "0x" + seq.state_root().hex(),
        "account": {
            "balance": str(proof.account.balance),
            "nonce": proof.account.nonce,
            "metadata": "0x" + proof.account.metadata.hex(),
        },
        "siblings": ["0x" + s.hex() for s in proof.siblings],
    }


@method("l2_getWithdrawalProof", desc="Proof data a user needs to claim a withdrawal on L1")
def l2_get_withdrawal_proof(nullifier: Any = None, **_: Any) -> Optional[dict]:
    nf = _hexb(nullifier)
    wd = _node().sequencer.bridge.withdrawals.get(nf)
    if wd is None:
        return None
    return {
        "nullifier": "0x" + wd.nullifier.hex(),
        "l2Txid": "0x" + wd.l2_txid.hex(),
        "l1Recipient": "0x" + wd.l1_recipient.hex(),
        "amount": str(wd.amount),
        "batch": wd.batch_number,
        "state": wd.state.value,
    }


@method("l2_getDeposit", desc="Deposit record by id")
def l2_get_deposit(deposit_id: Any = None, **_: Any) -> Optional[dict]:
    did = _hexb(deposit_id)
    dep = _node().sequencer.bridge.deposits.get(did)
    if dep is None:
        return None
    return {
        "depositId": "0x" + dep.deposit_id.hex(),
        "beneficiary": "0x" + dep.beneficiary.hex(),
        "amount": str(dep.amount),
        "confirmation": dep.confirmation.value,
        "credited": dep.credited,
    }


@method("l2_getSyncStatus", desc="Sync/head status")
def l2_get_sync_status() -> dict:
    seq = _node().sequencer
    return {"headBatch": seq.batch_number, "stateRoot": "0x" + seq.state_root().hex()}


@method("l2_getSequencerStatus", desc="Sequencer queue + backend status")
def l2_get_sequencer_status() -> dict:
    seq = _node().sequencer
    return {
        "pending": len(seq._pending),  # noqa: SLF001
        "headBatch": seq.batch_number,
        "sigBackend": seq.verifier.backend_name,
        "sigWorkers": seq.verifier.workers,
        "settlementMode": seq.cfg.settlement_mode.value,
    }


@method("l2_getProofStatus", desc="Proof presence for a batch")
def l2_get_proof_status(number: Any = None, **_: Any) -> dict:
    seq = _node().sequencer
    n = int(number) if number is not None else seq.batch_number
    proof = seq.proofs.get(n)
    return {
        "batch": n,
        "hasProof": proof is not None,
        "backend": proof.backend if proof else None,
        "publicInputsDigest": (
            "0x" + proof.public_inputs.digest().hex() if proof else None
        ),
    }


@method("l2_getTPS", desc="Throughput snapshot (ingress/executed/soft/settled)")
def l2_get_tps() -> dict:
    m = L2_METRICS
    return {
        "ingressTotal": m.counter_value("ingress_total"),
        "executedTotal": m.counter_value("executed_total"),
        "softConfirmedTotal": m.counter_value("soft_confirmed_total"),
        "settledTotal": m.counter_value("settled_total"),
        "batchesTotal": m.counter_value("batches_total"),
        "sigVerificationsTotal": m.counter_value("sig_verifications_total"),
        "ingressTps": m.gauge_value("ingress_tps"),
        "executedTps": m.gauge_value("executed_tps"),
        "softConfirmedTps": m.gauge_value("soft_confirmed_tps"),
        "settledTps": m.gauge_value("settled_tps"),
    }


@method("l2_getMetrics", desc="Full L2 metrics snapshot")
def l2_get_metrics() -> dict:
    return L2_METRICS.snapshot()


@method("l2_verifyBatch", desc="Re-verify a committed batch's proof from its DA blob (trustless check)")
def l2_verify_batch(number: Any = None, **_: Any) -> dict:
    seq = _node().sequencer
    n = int(number)
    proof = seq.proofs.get(n)
    header = seq.store.load_header(n) if seq.store else None
    blob = seq.store.load_blob(n) if seq.store else None
    if proof is None or blob is None or header is None:
        return {"batch": n, "verified": False, "reason": "batch/proof/DA not available"}
    ok = da.verify_blob(blob, proof.public_inputs.data_root)
    return {"batch": n, "daAvailable": ok, "backend": proof.backend}
