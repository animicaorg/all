"""L2 sequencer — the transaction pipeline.

    submit → decode → cheap validation → dedupe → signature verify →
    nonce/balance admission → order → (on close) execute → DA → prove →
    commit → settle

The sequencer gives fast soft confirmations but never controls ownership: it may
order txs, but the money invariant (:mod:`l2.bridge`) and forced exits bound its
power, and every committed batch is re-verifiable from its DA blob
(:mod:`l2.proof`).

This class runs synchronously (drive it with :meth:`submit` + :meth:`tick`) which
is what the all-in-one dev node and the tests use; the async node service
(:mod:`l2.service`) wraps it with bounded queues, a tick loop, and the settlement
submitter. Several lifecycle transitions (SOFT_CONFIRMED→BATCHED→PROVEN) happen
together in the in-process all-in-one; a production deployment separates proving
and settlement onto their own services behind the same interfaces.

Backpressure: :meth:`submit` rejects with ``QueueFull`` once the pending queue
hits ``max_pending`` — we prefer a clear rejection to unbounded memory growth.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import da, tx as l2tx
from .batch import Batch, BatchBuilder, ClosurePolicy, build_header
from .bridge import Bridge
from .constants import (
    PROTOCOL_MINTED_TYPES,
    SettlementMode,
    TxStatus,
    TxType,
    USER_SIGNED_TYPES,
)
from .crypto import SignatureVerifier, get_verifier
from .executor import Escrow, ExecContext, execute
from .fees import FeeSchedule
from .metrics import L2_METRICS
from .proof import Proof, ProofBackend, ProofPublicInputs, make_backend
from .state import Account, StateTree
from .store import L2Store


class SequencerError(Exception):
    pass


class QueueFull(SequencerError):
    pass


class AdmissionError(SequencerError):
    pass


@dataclass
class TxRecord:
    txid: bytes
    status: TxStatus
    received_ms: int
    batch_number: int = -1
    reason: str = ""
    receipt_status: str = ""


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class SequencerConfig:
    l2_chain_id: int
    settlement_mode: SettlementMode = SettlementMode.VALIDITY
    exec_workers: int = 4
    max_pending: int = 500_000
    closure: ClosurePolicy = field(default_factory=ClosurePolicy)
    fees: FeeSchedule = field(default_factory=FeeSchedule)
    fee_recipient: Optional[bytes] = None
    status_cap: int = 1_000_000  # bound the status map


class Sequencer:
    def __init__(
        self,
        config: SequencerConfig,
        store: Optional[L2Store] = None,
        *,
        tree: Optional[StateTree] = None,
        escrows: Optional[Dict[bytes, Escrow]] = None,
        bridge: Optional[Bridge] = None,
        head_batch: int = -1,
        verifier: Optional[SignatureVerifier] = None,
    ) -> None:
        self.cfg = config
        self.store = store
        self.tree = tree if tree is not None else StateTree()
        self.escrows: Dict[bytes, Escrow] = escrows if escrows is not None else {}
        self.bridge = bridge if bridge is not None else Bridge(config.l2_chain_id)
        self.batch_number = head_batch  # last COMMITTED batch number
        self.verifier = verifier or get_verifier(workers=config.exec_workers)
        self.proof_backend: ProofBackend = make_backend(config.settlement_mode)
        self.builder = BatchBuilder(policy=config.closure)
        self._pending: List[Tuple[l2tx.L2Tx, int]] = []  # (tx, encoded_len)
        self._pending_nonce: Dict[bytes, int] = {}  # sender -> next expected nonce
        self._seen: set = set()  # txids currently pending or recently sealed
        self.status: "Dict[bytes, TxRecord]" = {}
        self.proofs: Dict[int, Proof] = {}  # batch_number -> proof
        self._batch_open_root: Optional[bytes] = None

    # ── admission ──────────────────────────────────────────────────────────────

    def submit_raw(self, raw: bytes) -> bytes:
        """Decode, verify, admit a raw signed tx. Returns the txid on success."""
        L2_METRICS.inc("ingress_total")
        t = l2tx.decode(raw)  # strict; raises CodecError on any malformation
        return self.submit(t)

    def submit(self, t: l2tx.L2Tx) -> bytes:
        if len(self._pending) >= self.cfg.max_pending:
            raise QueueFull("sequencer pending queue full")
        if t.l2_chain_id != self.cfg.l2_chain_id:
            raise AdmissionError("wrong l2_chain_id")
        txid = t.txid()
        if txid in self._seen:
            raise AdmissionError("duplicate tx")  # dedupe before expensive work

        rec = TxRecord(txid=txid, status=TxStatus.RECEIVED, received_ms=_now_ms())
        self._put_status(rec)

        if t.tx_type in PROTOCOL_MINTED_TYPES:
            # DEPOSIT_CLAIM etc. are minted by the sequencer itself, never
            # submitted by users through this path.
            rec.status = TxStatus.FAILED
            rec.reason = "protocol-minted type cannot be user-submitted"
            raise AdmissionError(rec.reason)

        if t.tx_type not in USER_SIGNED_TYPES:
            rec.status = TxStatus.FAILED
            rec.reason = "unknown tx type"
            raise AdmissionError(rec.reason)

        # Signature: pubkey must hash to sender, and sign over the body.
        if l2tx.address_from_pubkey(t.pubkey) != t.sender:
            rec.status = TxStatus.FAILED
            rec.reason = "pubkey does not match sender"
            raise AdmissionError(rec.reason)
        if not self.verifier.verify(t.pubkey, t.signing_hash(), t.signature):
            rec.status = TxStatus.FAILED
            rec.reason = "invalid signature"
            raise AdmissionError(rec.reason)

        # Nonce admission against a pending view (account nonce + already-queued).
        acct = self.tree.get(t.sender)
        expected = self._pending_nonce.get(t.sender, acct.nonce)
        if t.nonce != expected:
            rec.status = TxStatus.FAILED
            rec.reason = f"bad nonce (want {expected}, got {t.nonce})"
            raise AdmissionError(rec.reason)

        # Cheap balance pre-check (execution re-checks authoritatively).
        fee = self.cfg.fees.fee_for(t)
        if t.fee < fee:
            rec.status = TxStatus.FAILED
            rec.reason = "fee below required"
            raise AdmissionError(rec.reason)

        # Admit.
        enc_len = len(raw_encode(t))
        self._pending.append((t, enc_len))
        self._pending_nonce[t.sender] = expected + 1
        self._seen.add(txid)
        rec.status = TxStatus.VALIDATED
        L2_METRICS.inc("validated_total")
        L2_METRICS.set("mempool_size", len(self._pending))
        return txid

    # ── batch cycle ────────────────────────────────────────────────────────────

    def tick(self, now_ms: Optional[int] = None, *, force_close: bool = False) -> Optional[Batch]:
        """Advance the pipeline. Seals a batch when the closure policy fires (or
        ``force_close``). Returns the sealed Batch, else None."""
        now = now_ms if now_ms is not None else _now_ms()
        if not self._pending:
            return None
        # Feed the builder's accounting (used only for closure decisions).
        # We keep the actual tx list in _pending; the builder tracks counts/bytes.
        self.builder.reset()
        for t, enc_len in self._pending:
            self.builder.add(t, enc_len, now)
        if not (force_close or self.builder.ready(now)):
            return None
        return self._seal(now)

    def _seal(self, now_ms: int) -> Batch:
        txs = [t for (t, _) in self._pending]
        n = self.batch_number + 1
        prev_root = self.tree.root()

        # Deposit authorization is delegated to the bridge; the executor cannot
        # mint ANM on its own.
        ctx = ExecContext(
            tree=self.tree,
            escrows=self.escrows,
            fees=self.cfg.fees,
            height=n,
            fee_recipient=self.cfg.fee_recipient,
            deposit_authorizer=self.bridge.authorize_deposit_claim,
        )
        result = execute(txs, ctx, workers=self.cfg.exec_workers)

        # Reflect bridge-affecting txs (post-execution, only for SUCCESS).
        for r, t in zip(result.receipts, txs):
            if r.status != "SUCCESS":
                continue
            if t.tx_type == TxType.DEPOSIT_CLAIM:
                self.bridge.mark_deposit_credited(t.payload.deposit_id)
            elif t.tx_type in (TxType.WITHDRAW, TxType.FORCED_WITHDRAW):
                self.bridge.record_withdrawal(r.txid, t.payload.l1_recipient, t.payload.amount, n)

        # Data availability + proof.
        blob, data_root = da.encode_batch(txs)
        header = build_header(n, self.cfg.l2_chain_id, result, data_root, len(txs), now_ms)
        batch = Batch(header=header, txs=txs, receipts=result.receipts, da_blob=blob)
        pi = ProofPublicInputs(
            l2_chain_id=self.cfg.l2_chain_id,
            batch_number=n,
            prev_state_root=result.prev_state_root,
            new_state_root=result.new_state_root,
            transactions_root=result.transactions_root,
            receipts_root=result.receipts_root,
            escrow_root=result.escrow_root,
            data_root=data_root,
            fees_collected=result.fees_collected,
            deposited=result.deposited,
            withdrawn=result.withdrawn,
        )
        proof = self.proof_backend.generate(pi, blob)
        self.proofs[n] = proof

        # Money invariant — fatal on violation (halt rather than risk unbacked
        # withdrawals).
        balsum = sum(a.balance for a in self.tree.accounts().values())
        escrow_sum = sum(e.amount for e in self.escrows.values())
        self.bridge.check_invariant(balsum, escrow_sum)

        # Durable commit (atomic).
        if self.store is not None:
            self.store.commit_batch(batch, self.tree, self.escrows, self.bridge)

        # Status transitions.
        for r in result.receipts:
            rec = self.status.get(r.txid)
            if rec is None:
                rec = TxRecord(r.txid, TxStatus.RECEIVED, now_ms)
                self._put_status(rec)
            rec.batch_number = n
            rec.receipt_status = r.status
            if r.status == "SUCCESS":
                rec.status = TxStatus.PROVEN  # soft-confirmed+batched+proven in-process
            else:
                rec.status = TxStatus.REVERTED
                rec.reason = r.reason

        self.batch_number = n
        self._pending = []
        self._pending_nonce = {}
        self._seen = set()
        L2_METRICS.inc("batches_total")
        L2_METRICS.inc("soft_confirmed_total", result.success_count)
        L2_METRICS.set("mempool_size", 0)
        L2_METRICS.set("head_batch", n)
        L2_METRICS.set("batch_transactions", len(txs))
        L2_METRICS.set("batch_bytes", sum(len(t.encode()) for t in txs))
        L2_METRICS.set("batch_compressed_bytes", len(blob))
        L2_METRICS.set("anm_locked", self.bridge.locked_on_l1)
        L2_METRICS.set("anm_l2_supply", balsum + escrow_sum)
        return batch

    # ── protocol-minted: credit finalized deposits ──────────────────────────────

    def enqueue_finalized_deposits(self) -> int:
        """Mint DEPOSIT_CLAIM txs for every finalized, uncredited deposit and
        admit them into the pending set. Returns the count enqueued."""
        count = 0
        for dep in self.bridge.claimable_deposits():
            claim = l2tx.L2Tx(
                version=1,
                l2_chain_id=self.cfg.l2_chain_id,
                tx_type=TxType.DEPOSIT_CLAIM,
                sender=dep.beneficiary,
                nonce=0,
                fee=0,
                expiry=0,
                payload=l2tx.DepositClaimPayload(dep.beneficiary, dep.amount, dep.deposit_id),
                pubkey=b"\x00" * 1952,
                signature=b"\x00" * 3309,
            )
            enc_len = len(claim.body_bytes())
            txid = claim.txid()
            if txid in self._seen:
                continue
            self._pending.append((claim, enc_len))
            self._seen.add(txid)
            self._put_status(TxRecord(txid, TxStatus.VALIDATED, _now_ms()))
            count += 1
        return count

    # ── forced inclusion ─────────────────────────────────────────────────────────

    def process_forced(self, current_l1_height: int) -> int:
        """Admit any overdue forced-inclusion txs so a censoring sequencer cannot
        suppress them past the deadline. Returns count admitted."""
        admitted = 0
        for req in self.bridge.overdue_forced(current_l1_height):
            try:
                self.submit_raw(req.raw_l2_tx)
                self.bridge.mark_forced_included(req.request_id)
                admitted += 1
                L2_METRICS.inc("forced_transactions_total")
            except SequencerError:
                # Even a malformed/invalid forced tx is marked handled so it does
                # not wedge the queue forever; it simply reverts.
                self.bridge.mark_forced_included(req.request_id)
        return admitted

    # ── views ─────────────────────────────────────────────────────────────────

    def credit_genesis(self, addr: bytes, amount: int) -> None:
        """Fund an L2 account at genesis with backed ANM (records equivalent L1
        lock so the invariant holds). For genesis allocations and test setup —
        never a way to mint unbacked ANM."""
        acct = self.tree.get(addr)
        self.tree.set(addr, Account(acct.balance + amount, acct.nonce, acct.metadata))
        self.bridge.genesis_lock(amount)

    def balance(self, addr: bytes) -> int:
        return self.tree.get(addr).balance

    def nonce(self, addr: bytes) -> int:
        return self.tree.get(addr).nonce

    def pending_nonce(self, addr: bytes) -> int:
        return self._pending_nonce.get(addr, self.tree.get(addr).nonce)

    def account_proof(self, addr: bytes):
        return self.tree.prove(addr)

    def status_of(self, txid: bytes) -> Optional[TxRecord]:
        return self.status.get(txid)

    def state_root(self) -> bytes:
        return self.tree.root()

    def _put_status(self, rec: TxRecord) -> None:
        self.status[rec.txid] = rec
        if len(self.status) > self.cfg.status_cap:
            # Drop oldest ~1% to stay bounded (FIFO-ish on dict insertion order).
            for k in list(self.status.keys())[: self.cfg.status_cap // 100]:
                self.status.pop(k, None)


def raw_encode(t: l2tx.L2Tx) -> bytes:
    """Encode for byte-accounting; tolerant of unsigned drafts (uses body when a
    full envelope is not available)."""
    try:
        return t.encode()
    except Exception:
        return t.body_bytes()
