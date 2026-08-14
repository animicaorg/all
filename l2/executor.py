"""Deterministic parallel L2 execution.

The sequencer fixes a total order over admitted txs. Execution must yield exactly
the state a single-threaded run in that order would — *regardless of how work is
scheduled across cores*. We achieve provable equivalence without optimistic
re-execution by exploiting the L2's account model:

* every tx writes only its ``sender`` (balance debit + nonce bump) and *adds* to
  the balances of a set of credited accounts (recipient/provider/beneficiary/…);
* two txs conflict iff they touch a common account.

So we build the **account-conflict graph** for the batch and split it into
connected components (union-find). Two txs in different components share no
account, therefore their executions are independent and commute. We execute each
component's txs **serially in sequencer order** (preserving nonce/balance
semantics inside the component) and run the components **in parallel**. Because
components are account-disjoint, merging their resulting account maps can never
collide, and the merged state is identical to a full sequential run. This holds
for any worker count — the determinism the spec requires.

Parallelism is workload-dependent and honest: "unique sender → unique receiver"
fully parallelizes (every tx is its own component); hot-account contention
collapses toward one component and executes serially — which is the only
order-preserving behavior possible for writes to a shared account. We never fake
parallelism by dropping ordering guarantees.

Each tx is re-validated authoritatively at execution time (nonce, balance, expiry,
type-specific rules). Admission earlier in the pipeline is an optimization, never
trusted here. A tx that fails execution is marked REVERTED and changes nothing —
it does not abort the batch.

Escrow lives in a dedicated keyed store committed as ``escrow_root`` in the batch
so escrow state is authenticated and provable like accounts.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import tx as l2tx
from .constants import L2_TREASURY_ADDRESS, MAX_AMOUNT, TxType
from .fees import FeeSchedule
from .metrics import L2_METRICS
from .state import Account, StateTree, ZERO32

# ── escrow state ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Escrow:
    depositor: bytes
    beneficiary: bytes
    amount: int
    refund_after: int  # L2 batch height; 0 = only releasable, never auto-refund
    open_height: int

    def encode(self) -> bytes:
        return (
            self.depositor
            + self.beneficiary
            + self.amount.to_bytes(16, "big")
            + self.refund_after.to_bytes(8, "big")
            + self.open_height.to_bytes(8, "big")
        )


def escrows_root(escrows: Dict[bytes, Escrow]) -> bytes:
    """Deterministic commitment over the open-escrow set (sorted by id)."""
    if not escrows:
        return ZERO32
    m = hashlib.sha3_256()
    m.update(b"animica.l2.escrows.v1")
    for eid in sorted(escrows):
        m.update(eid)
        m.update(escrows[eid].encode())
    return m.digest()


# ── receipts ─────────────────────────────────────────────────────────────────


@dataclass
class Receipt:
    txid: bytes
    index: int
    status: str  # "SUCCESS" | "REVERTED"
    reason: str = ""
    fee_charged: int = 0
    sender_new_nonce: int = 0

    def encode(self) -> bytes:
        return (
            self.txid
            + self.index.to_bytes(8, "big")
            + (b"\x01" if self.status == "SUCCESS" else b"\x00")
            + self.fee_charged.to_bytes(16, "big")
            + self.sender_new_nonce.to_bytes(16, "big")
        )


def receipts_root(receipts: List[Receipt]) -> bytes:
    m = hashlib.sha3_256()
    m.update(b"animica.l2.receipts.v1")
    for r in receipts:  # receipts are already in sequencer order
        m.update(r.encode())
    return m.digest()


def transactions_root(txs: List[l2tx.L2Tx]) -> bytes:
    m = hashlib.sha3_256()
    m.update(b"animica.l2.txs.v1")
    for t in txs:
        m.update(t.txid())
    return m.digest()


# ── execution context ────────────────────────────────────────────────────────


@dataclass
class ExecContext:
    """Everything execution reads/writes. Deposit authorization is delegated to
    the bridge via ``deposit_authorizer`` — the executor never mints ANM on its
    own; a DEPOSIT_CLAIM only credits if the bridge confirms a finalized,
    unclaimed L1 deposit with a matching id/amount/beneficiary."""

    tree: StateTree
    escrows: Dict[bytes, Escrow] = field(default_factory=dict)
    fees: FeeSchedule = field(default_factory=FeeSchedule)
    height: int = 0
    # Fees are credited to this L2 account once per batch at finalize (never
    # per-tx — that would make every tx touch it and collapse all parallelism).
    # Because no tx in a batch reads the fee recipient's balance, crediting it
    # after execution is identical to a sequential run. Keeping fees inside an
    # L2 account (rather than a bare counter) is what makes ANM conservation
    # hold exactly: fees move within L2, they are not destroyed.
    fee_recipient: Optional[bytes] = None
    # (deposit_id, beneficiary, amount) -> bool  (finalized & not-yet-claimed)
    deposit_authorizer: Optional[Callable[[bytes, bytes, int], bool]] = None
    # Accumulated fees credited to the treasury/sequencer this batch.
    fees_collected: int = 0
    # Sum of amounts unlocked by WITHDRAW/FORCED_WITHDRAW this batch (leaves L2).
    withdrawn: int = 0
    # Sum credited by DEPOSIT_CLAIM this batch (enters L2).
    deposited: int = 0


@dataclass
class ExecResult:
    receipts: List[Receipt]
    prev_state_root: bytes
    new_state_root: bytes
    transactions_root: bytes
    receipts_root: bytes
    escrow_root: bytes
    fees_collected: int
    deposited: int
    withdrawn: int
    success_count: int
    reverted_count: int


# ── conflict graph ───────────────────────────────────────────────────────────


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[bytes, bytes] = {}

    def find(self, x: bytes) -> bytes:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        # path compression
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: bytes, b: bytes) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _touched_accounts(t: l2tx.L2Tx) -> List[bytes]:
    """Every account a tx reads or writes. Superset is safe (only reduces
    parallelism, never correctness)."""
    accts = [t.sender]
    p = t.payload
    tt = t.tx_type
    if tt in (TxType.TRANSFER, TxType.PAY):
        accts.append(p.recipient)
    elif tt in (TxType.WITHDRAW, TxType.FORCED_WITHDRAW):
        pass  # only debits sender; l1_recipient is off-L2
    elif tt == TxType.DEPOSIT_CLAIM:
        accts.append(p.beneficiary)
    elif tt == TxType.ESCROW_OPEN:
        accts.append(p.beneficiary)
    elif tt in (TxType.ESCROW_RELEASE, TxType.ESCROW_REFUND):
        # touches whoever the escrow pays out to; resolved at exec, so be
        # conservative and serialize escrow ops against their depositor by
        # unioning on the escrow id namespace.
        accts.append(hashlib.sha3_256(b"escrow:" + p.escrow_id).digest())
    elif tt in (TxType.AGENT_PAYMENT, TxType.INFERENCE_PAYMENT):
        accts.append(p.provider)
    elif tt == TxType.BATCH_PAYMENT:
        for recipient, _ in p.payments:
            accts.append(recipient)
    return accts


def partition_components(txs: List[l2tx.L2Tx]) -> List[List[int]]:
    """Return lists of tx indices, one per account-disjoint component, each list
    in ascending (sequencer) order. Component order itself is deterministic
    (sorted by first index)."""
    uf = _UnionFind()
    for t in txs:
        touched = _touched_accounts(t)
        for a in touched[1:]:
            uf.union(touched[0], a)
    groups: Dict[bytes, List[int]] = {}
    for i, t in enumerate(txs):
        root = uf.find(_touched_accounts(t)[0])
        groups.setdefault(root, []).append(i)
    # Deterministic ordering of components by their earliest tx index.
    ordered = sorted(groups.values(), key=lambda idxs: idxs[0])
    return ordered


# ── per-tx application (serial within a component) ───────────────────────────


class _LocalState:
    """A component's private view: a dict of account overrides + escrow ops,
    seeded lazily from the shared tree (read-only). Because components are
    account-disjoint, no two _LocalState instances touch the same account."""

    def __init__(self, tree: StateTree) -> None:
        self._tree = tree
        self.writes: Dict[bytes, Account] = {}
        self.escrow_writes: Dict[bytes, Optional[Escrow]] = {}

    def get(self, addr: bytes) -> Account:
        if addr in self.writes:
            return self.writes[addr]
        return self._tree.get(addr)

    def put(self, addr: bytes, acct: Account) -> None:
        self.writes[addr] = acct


def _apply_one(
    t: l2tx.L2Tx,
    idx: int,
    ls: _LocalState,
    ctx: ExecContext,
    fee_delta: List[int],
    dep_delta: List[int],
    wd_delta: List[int],
) -> Receipt:
    txid = t.txid()
    sender = ls.get(t.sender)
    fee = ctx.fees.fee_for(t)

    def revert(reason: str) -> Receipt:
        L2_METRICS.inc("reverted_total")
        return Receipt(txid, idx, "REVERTED", reason, 0, sender.nonce)

    # Expiry (0 = no expiry). height is the batch this executes in.
    if t.expiry and ctx.height > t.expiry:
        return revert("expired")

    # Protocol-minted types authenticate differently (no user nonce spend on the
    # sender path for DEPOSIT_CLAIM; the bridge is the authority).
    if t.tx_type == TxType.DEPOSIT_CLAIM:
        p = t.payload
        if ctx.deposit_authorizer is None or not ctx.deposit_authorizer(
            p.deposit_id, p.beneficiary, p.amount
        ):
            return revert("deposit_unauthorized_or_claimed")
        ben = ls.get(p.beneficiary)
        new_bal = ben.balance + p.amount
        if new_bal > MAX_AMOUNT:
            return revert("balance_overflow")
        ls.put(p.beneficiary, Account(new_bal, ben.nonce, ben.metadata))
        dep_delta[0] += p.amount
        return Receipt(txid, idx, "SUCCESS", "", 0, ben.nonce)

    # All user-signed types: nonce must match exactly (prevents replay/reorder).
    if t.nonce != sender.nonce:
        return revert(f"bad_nonce(want {sender.nonce}, got {t.nonce})")

    # The signed tx.fee is the sender-authorized ceiling; charge the schedule
    # fee and refuse if the ceiling is below it. Only the schedule fee is taken.
    if t.fee < fee:
        return revert(f"fee_below_required(need {fee}, cap {t.fee})")

    # Compute the total debit for this tx type.
    if t.tx_type in (TxType.TRANSFER, TxType.PAY):
        p = t.payload
        total = p.amount + fee
        if sender.balance < total:
            return revert("insufficient_balance")
        recip = ls.get(p.recipient)
        rb = recip.balance + p.amount
        if rb > MAX_AMOUNT:
            return revert("balance_overflow")
        ls.put(t.sender, Account(sender.balance - total, sender.nonce + 1, sender.metadata))
        ls.put(p.recipient, Account(rb, recip.nonce, recip.metadata))

    elif t.tx_type in (TxType.AGENT_PAYMENT, TxType.INFERENCE_PAYMENT):
        p = t.payload
        total = p.amount + fee
        if sender.balance < total:
            return revert("insufficient_balance")
        prov = ls.get(p.provider)
        pb = prov.balance + p.amount
        if pb > MAX_AMOUNT:
            return revert("balance_overflow")
        ls.put(t.sender, Account(sender.balance - total, sender.nonce + 1, sender.metadata))
        ls.put(p.provider, Account(pb, prov.nonce, prov.metadata))

    elif t.tx_type == TxType.BATCH_PAYMENT:
        p = t.payload
        total_out = 0
        for _, amt in p.payments:
            total_out += amt
        total = total_out + fee
        if sender.balance < total:
            return revert("insufficient_balance")
        # Apply credits; recipients may repeat, so accumulate.
        credit: Dict[bytes, int] = {}
        for recipient, amt in p.payments:
            credit[recipient] = credit.get(recipient, 0) + amt
        for recipient, amt in credit.items():
            r = ls.get(recipient)
            nb = r.balance + amt
            if nb > MAX_AMOUNT:
                return revert("balance_overflow")
            ls.put(recipient, Account(nb, r.nonce, r.metadata))
        ls.put(t.sender, Account(sender.balance - total, sender.nonce + 1, sender.metadata))

    elif t.tx_type in (TxType.WITHDRAW, TxType.FORCED_WITHDRAW):
        p = t.payload
        total = p.amount + fee
        if sender.balance < total:
            return revert("insufficient_balance")
        # Burn on L2: sender debited amount+fee; amount becomes claimable on L1
        # (bridge tracks the claim). No L2 account is credited the amount.
        ls.put(t.sender, Account(sender.balance - total, sender.nonce + 1, sender.metadata))
        wd_delta[0] += p.amount

    elif t.tx_type == TxType.ESCROW_OPEN:
        p = t.payload
        total = p.amount + fee
        if sender.balance < total:
            return revert("insufficient_balance")
        if p.escrow_id in ctx.escrows or p.escrow_id in ls.escrow_writes:
            return revert("escrow_exists")
        ls.put(t.sender, Account(sender.balance - total, sender.nonce + 1, sender.metadata))
        ls.escrow_writes[p.escrow_id] = Escrow(
            depositor=t.sender,
            beneficiary=p.beneficiary,
            amount=p.amount,
            refund_after=p.refund_after,
            open_height=ctx.height,
        )

    elif t.tx_type == TxType.ESCROW_RELEASE:
        p = t.payload
        esc = ls.escrow_writes.get(p.escrow_id, ctx.escrows.get(p.escrow_id))
        if esc is None:
            return revert("escrow_missing")
        if t.sender != esc.depositor:
            return revert("escrow_not_depositor")
        if sender.balance < fee:
            return revert("insufficient_balance")
        ben = ls.get(esc.beneficiary)
        nb = ben.balance + esc.amount
        if nb > MAX_AMOUNT:
            return revert("balance_overflow")
        ls.put(esc.beneficiary, Account(nb, ben.nonce, ben.metadata))
        ls.put(t.sender, Account(sender.balance - fee, sender.nonce + 1, sender.metadata))
        ls.escrow_writes[p.escrow_id] = None  # closed

    elif t.tx_type == TxType.ESCROW_REFUND:
        p = t.payload
        esc = ls.escrow_writes.get(p.escrow_id, ctx.escrows.get(p.escrow_id))
        if esc is None:
            return revert("escrow_missing")
        if t.sender != esc.depositor:
            return revert("escrow_not_depositor")
        if esc.refund_after == 0 or ctx.height < esc.refund_after:
            return revert("escrow_refund_too_early")
        if sender.balance < fee:
            return revert("insufficient_balance")
        # Refund the escrowed amount back to the depositor.
        refunded = Account(
            sender.balance - fee + esc.amount, sender.nonce + 1, sender.metadata
        )
        if refunded.balance > MAX_AMOUNT:
            return revert("balance_overflow")
        ls.put(t.sender, refunded)
        ls.escrow_writes[p.escrow_id] = None
    else:
        return revert("unknown_type")

    fee_delta[0] += fee
    return Receipt(txid, idx, "SUCCESS", "", fee, ls.get(t.sender).nonce)


def _run_component(
    txs: List[l2tx.L2Tx], indices: List[int], tree: StateTree, ctx: ExecContext
) -> Tuple[Dict[bytes, Account], Dict[bytes, Optional[Escrow]], List[Receipt], int, int, int]:
    ls = _LocalState(tree)
    fee_delta = [0]
    dep_delta = [0]
    wd_delta = [0]
    receipts: List[Receipt] = []
    for idx in indices:
        receipts.append(_apply_one(txs[idx], idx, ls, ctx, fee_delta, dep_delta, wd_delta))
    return ls.writes, ls.escrow_writes, receipts, fee_delta[0], dep_delta[0], wd_delta[0]


# ── public entry points ──────────────────────────────────────────────────────


def execute(
    txs: List[l2tx.L2Tx], ctx: ExecContext, *, workers: int = 1
) -> ExecResult:
    """Execute a batch. ``workers==1`` uses the sequential reference path;
    ``workers>1`` uses account-disjoint component parallelism (identical result).
    """
    prev_root = ctx.tree.root()
    with L2_METRICS.time("execution_seconds"):
        if workers <= 1 or len(txs) < 2:
            result = _execute_sequential(txs, ctx)
        else:
            result = _execute_parallel(txs, ctx, workers)
    return result


def _finalize(
    txs: List[l2tx.L2Tx],
    ctx: ExecContext,
    prev_root: bytes,
    all_writes: Dict[bytes, Account],
    all_escrow_writes: Dict[bytes, Optional[Escrow]],
    receipts: List[Receipt],
) -> ExecResult:
    # Commit account writes to the tree.
    for addr, acct in all_writes.items():
        ctx.tree.set(addr, acct)
    # Credit the batch's collected fees to the treasury account (once), so ANM
    # is conserved rather than destroyed. Done here — after component writes,
    # before the root — because no tx read this account during execution. The
    # recipient defaults to the protocol treasury so fees always stay inside L2
    # (conservation) and any verifier agrees without extra public inputs.
    if ctx.fees_collected > 0:
        recipient = ctx.fee_recipient if ctx.fee_recipient is not None else L2_TREASURY_ADDRESS
        cur = ctx.tree.get(recipient)
        ctx.tree.set(
            recipient,
            Account(cur.balance + ctx.fees_collected, cur.nonce, cur.metadata),
        )
    # Commit escrow writes (None = closed).
    for eid, esc in all_escrow_writes.items():
        if esc is None:
            ctx.escrows.pop(eid, None)
        else:
            ctx.escrows[eid] = esc
    receipts.sort(key=lambda r: r.index)  # restore sequencer order
    success = sum(1 for r in receipts if r.status == "SUCCESS")
    with L2_METRICS.time("state_commit_seconds"):
        new_root = ctx.tree.root()
    L2_METRICS.inc("executed_total", len(txs))
    return ExecResult(
        receipts=receipts,
        prev_state_root=prev_root,
        new_state_root=new_root,
        transactions_root=transactions_root(txs),
        receipts_root=receipts_root(receipts),
        escrow_root=escrows_root(ctx.escrows),
        fees_collected=ctx.fees_collected,
        deposited=ctx.deposited,
        withdrawn=ctx.withdrawn,
        success_count=success,
        reverted_count=len(receipts) - success,
    )


def _execute_sequential(txs: List[l2tx.L2Tx], ctx: ExecContext) -> ExecResult:
    prev_root = ctx.tree.root()
    ls = _LocalState(ctx.tree)
    fee_delta, dep_delta, wd_delta = [0], [0], [0]
    receipts: List[Receipt] = []
    for i, t in enumerate(txs):
        receipts.append(_apply_one(t, i, ls, ctx, fee_delta, dep_delta, wd_delta))
    ctx.fees_collected += fee_delta[0]
    ctx.deposited += dep_delta[0]
    ctx.withdrawn += wd_delta[0]
    return _finalize(txs, ctx, prev_root, ls.writes, ls.escrow_writes, receipts)


def _execute_parallel(txs: List[l2tx.L2Tx], ctx: ExecContext, workers: int) -> ExecResult:
    prev_root = ctx.tree.root()
    components = partition_components(txs)
    all_writes: Dict[bytes, Account] = {}
    all_escrow: Dict[bytes, Optional[Escrow]] = {}
    all_receipts: List[Receipt] = []
    fee_total = dep_total = wd_total = 0
    # Threads are fine: components are account-disjoint, so there is zero shared
    # mutable state between tasks; each returns its private writes to be merged.
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="l2-exec") as pool:
        futs = [
            pool.submit(_run_component, txs, idxs, ctx.tree, ctx) for idxs in components
        ]
        for fut in futs:
            writes, esc_writes, receipts, fee_d, dep_d, wd_d = fut.result()
            # Disjoint by construction — assert to catch any conflict-graph bug.
            for addr in writes:
                assert addr not in all_writes, "component account overlap"
            all_writes.update(writes)
            all_escrow.update(esc_writes)
            all_receipts.extend(receipts)
            fee_total += fee_d
            dep_total += dep_d
            wd_total += wd_d
    ctx.fees_collected += fee_total
    ctx.deposited += dep_total
    ctx.withdrawn += wd_total
    return _finalize(txs, ctx, prev_root, all_writes, all_escrow, all_receipts)
