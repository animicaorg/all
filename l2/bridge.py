"""L1 bridge, deposits, withdrawals, forced operations, and the money invariant.

ANM is never created on L2. The bridge is the accounting authority that ties L2
credits/burns to L1 lock/unlock events, and it continuously enforces:

    total_withdrawable_L2_ANM  <=  ANM_locked_for_L2_on_L1

In 10.0.0 the anchoring is *additive* to L1: deposits are ordinary L1 transfers
to the canonical bridge account carrying an ``ANML2D1`` magic memo (the L1
executor treats the memo as opaque — verified against the codebase — so no L1
consensus change is needed to observe them). State-root / DA commitments ride the
same way (``ANML2C1``). When 10.0.0 anchoring becomes *consensus-interpreted*
(fork ``FORK_L2_ANCHOR`` at height 80,000) full nodes validate these; until then
they are indexed off-consensus. The forced-exit escape hatch (below) is what
bounds sequencer power — we never call the sequencer-operated component trustless.

Reorg safety: an L1 deposit is credited to L2 only once it is FINALIZED
(``L1_FINALITY_DEPTH`` blocks deep). An OBSERVED/CONFIRMED deposit can still
vanish in a reorg and is never credited, so a reorg can never mint unbacked L2
ANM. Withdrawals carry a unique nullifier; the L1 claim spends it exactly once.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from .constants import L1_CONFIRM_DEPTH, L1_FINALITY_DEPTH, L1Confirmation


class WithdrawState(str, Enum):
    BURNED = "BURNED"  # burned on L2, batch not yet L1-finalized
    CLAIMABLE = "CLAIMABLE"  # batch L1-finalized; user may claim on L1
    CLAIMED = "CLAIMED"  # nullifier spent on L1; ANM released


@dataclass
class Deposit:
    deposit_id: bytes  # unique: sha3_256(l1_txid || beneficiary || amount)
    l1_txid: bytes
    beneficiary: bytes  # 32-byte L2 account key
    amount: int
    seen_height: int  # L1 height where observed
    confirmation: L1Confirmation = L1Confirmation.OBSERVED
    credited: bool = False  # a DEPOSIT_CLAIM has credited it on L2


@dataclass
class Withdrawal:
    nullifier: bytes  # unique: sha3_256(l2_txid)
    l2_txid: bytes
    l1_recipient: bytes
    amount: int
    batch_number: int
    state: WithdrawState = WithdrawState.BURNED


@dataclass
class ForcedRequest:
    """A user-submitted L2 tx enqueued via L1 for censorship resistance. The
    sequencer must include it before ``deadline_height`` (an L2 batch height) or
    the escape path lets it be force-processed."""

    request_id: bytes
    raw_l2_tx: bytes
    submit_l1_height: int
    deadline_height: int
    included: bool = False


class BridgeError(Exception):
    pass


class Bridge:
    """In-memory bridge state. Persistence is delegated to :mod:`l2.store`
    (the sequencer/full-node snapshots this alongside the state tree). All
    quantities are integer nanos."""

    def __init__(self, l2_chain_id: int) -> None:
        self.l2_chain_id = l2_chain_id
        self.deposits: Dict[bytes, Deposit] = {}
        self.withdrawals: Dict[bytes, Withdrawal] = {}
        self.forced: Dict[bytes, ForcedRequest] = {}
        # Running totals used by the invariant.
        self.locked_on_l1: int = 0  # ANM held by the L1 bridge account for L2
        self.credited_total: int = 0  # sum of DEPOSIT_CLAIM credits on L2
        self.burned_total: int = 0  # sum of WITHDRAW burns on L2
        self.claimed_on_l1_total: int = 0  # sum released on L1 (nullifier spent)

    # ── genesis / backed funding ───────────────────────────────────────────────

    def genesis_lock(self, amount: int) -> None:
        """Record ``amount`` nanos as locked on L1 and credited to L2 in one
        step. Used for a genesis allocation (or a test fixture) that funds L2
        accounts directly: the funds MUST be backed by equivalent locked L1 ANM,
        so this keeps ``credited_total`` and ``locked_on_l1`` in lockstep and the
        conservation invariant intact. Never call this to create unbacked ANM."""
        if amount < 0:
            raise BridgeError("genesis lock must be non-negative")
        self.locked_on_l1 += amount
        self.credited_total += amount

    # ── deposits ─────────────────────────────────────────────────────────────

    @staticmethod
    def make_deposit_id(l1_txid: bytes, beneficiary: bytes, amount: int) -> bytes:
        m = hashlib.sha3_256()
        m.update(b"animica.l2.deposit.v1")
        m.update(l1_txid)
        m.update(beneficiary)
        m.update(amount.to_bytes(16, "big"))
        return m.digest()

    def observe_deposit(
        self, l1_txid: bytes, beneficiary: bytes, amount: int, seen_height: int
    ) -> Deposit:
        """Record an L1 deposit transfer to the bridge address. Idempotent on
        deposit_id — a re-observed deposit (e.g. after restart) is not doubled."""
        if amount <= 0:
            raise BridgeError("deposit amount must be positive")
        did = self.make_deposit_id(l1_txid, beneficiary, amount)
        existing = self.deposits.get(did)
        if existing is not None:
            return existing
        dep = Deposit(did, l1_txid, beneficiary, amount, seen_height)
        self.deposits[did] = dep
        return dep

    def update_l1_head(self, l1_head_height: int) -> List[Deposit]:
        """Advance confirmation tiers as L1 grows. Returns deposits that JUST
        became FINALIZED (and thus newly claimable on L2)."""
        newly_final: List[Deposit] = []
        for dep in self.deposits.values():
            depth = l1_head_height - dep.seen_height
            prev = dep.confirmation
            if depth >= L1_FINALITY_DEPTH:
                dep.confirmation = L1Confirmation.FINALIZED
            elif depth >= L1_CONFIRM_DEPTH:
                dep.confirmation = L1Confirmation.CONFIRMED
            else:
                dep.confirmation = L1Confirmation.OBSERVED
            if dep.confirmation == L1Confirmation.FINALIZED and prev != L1Confirmation.FINALIZED:
                # Lock the ANM the moment it is safe — this is the "L1 lock".
                self.locked_on_l1 += dep.amount
                newly_final.append(dep)
        return newly_final

    def rollback_l1_to(self, safe_height: int) -> List[bytes]:
        """Handle an L1 reorg: drop OBSERVED/CONFIRMED deposits seen above the
        new safe height. FINALIZED deposits are past finality and are never
        rolled back. Returns dropped deposit ids."""
        dropped: List[bytes] = []
        for did, dep in list(self.deposits.items()):
            if (
                dep.confirmation != L1Confirmation.FINALIZED
                and dep.seen_height > safe_height
            ):
                del self.deposits[did]
                dropped.append(did)
        return dropped

    def authorize_deposit_claim(
        self, deposit_id: bytes, beneficiary: bytes, amount: int
    ) -> bool:
        """Callback used by the executor: may this DEPOSIT_CLAIM credit L2? Only
        if a FINALIZED, not-yet-credited deposit matches exactly."""
        dep = self.deposits.get(deposit_id)
        if dep is None:
            return False
        return (
            dep.confirmation == L1Confirmation.FINALIZED
            and not dep.credited
            and dep.beneficiary == beneficiary
            and dep.amount == amount
        )

    def mark_deposit_credited(self, deposit_id: bytes) -> None:
        """Called after a DEPOSIT_CLAIM successfully executes on L2."""
        dep = self.deposits[deposit_id]
        if dep.credited:
            raise BridgeError("deposit already credited")
        dep.credited = True
        self.credited_total += dep.amount

    def claimable_deposits(self) -> List[Deposit]:
        return [
            d
            for d in self.deposits.values()
            if d.confirmation == L1Confirmation.FINALIZED and not d.credited
        ]

    # ── withdrawals ──────────────────────────────────────────────────────────

    @staticmethod
    def make_nullifier(l2_txid: bytes) -> bytes:
        m = hashlib.sha3_256()
        m.update(b"animica.l2.withdraw.nullifier.v1")
        m.update(l2_txid)
        return m.digest()

    def record_withdrawal(
        self, l2_txid: bytes, l1_recipient: bytes, amount: int, batch_number: int
    ) -> Withdrawal:
        """Called when a WITHDRAW/FORCED_WITHDRAW burns ANM on L2."""
        nf = self.make_nullifier(l2_txid)
        if nf in self.withdrawals:
            raise BridgeError("withdrawal nullifier already exists (replay)")
        wd = Withdrawal(nf, l2_txid, l1_recipient, amount, batch_number)
        self.withdrawals[nf] = wd
        self.burned_total += amount
        return wd

    def mark_batch_finalized_on_l1(self, batch_number: int) -> List[Withdrawal]:
        """When the batch containing a withdrawal is L1-finalized, its burns
        become CLAIMABLE on L1. Returns the withdrawals that became claimable."""
        out: List[Withdrawal] = []
        for wd in self.withdrawals.values():
            if wd.batch_number <= batch_number and wd.state == WithdrawState.BURNED:
                wd.state = WithdrawState.CLAIMABLE
                out.append(wd)
        return out

    def claim_withdrawal_on_l1(self, nullifier: bytes) -> Withdrawal:
        """User claims on L1. Spends the nullifier exactly once and releases the
        locked ANM. Double-claim is rejected."""
        wd = self.withdrawals.get(nullifier)
        if wd is None:
            raise BridgeError("unknown withdrawal nullifier")
        if wd.state == WithdrawState.CLAIMED:
            raise BridgeError("withdrawal already claimed (double-spend blocked)")
        if wd.state != WithdrawState.CLAIMABLE:
            raise BridgeError("withdrawal not yet claimable (batch not L1-finalized)")
        if self.locked_on_l1 < wd.amount:
            # Must never happen if the invariant holds; refuse rather than
            # release unbacked ANM.
            raise BridgeError("INVARIANT VIOLATION: insufficient locked ANM")
        wd.state = WithdrawState.CLAIMED
        self.locked_on_l1 -= wd.amount
        self.claimed_on_l1_total += wd.amount
        return wd

    # ── forced inclusion / exit ────────────────────────────────────────────────

    def enqueue_forced(
        self, raw_l2_tx: bytes, submit_l1_height: int, deadline_blocks: int
    ) -> ForcedRequest:
        rid = hashlib.sha3_256(b"animica.l2.forced.v1" + raw_l2_tx).digest()
        if rid in self.forced:
            return self.forced[rid]
        req = ForcedRequest(
            request_id=rid,
            raw_l2_tx=raw_l2_tx,
            submit_l1_height=submit_l1_height,
            deadline_height=submit_l1_height + deadline_blocks,
        )
        self.forced[rid] = req
        return req

    def pending_forced(self, l2_height: int) -> List[ForcedRequest]:
        return [r for r in self.forced.values() if not r.included]

    def overdue_forced(self, current_l1_height: int) -> List[ForcedRequest]:
        """Forced requests the sequencer failed to include by their deadline —
        the escape path processes these to guarantee censorship resistance."""
        return [
            r
            for r in self.forced.values()
            if not r.included and current_l1_height >= r.deadline_height
        ]

    def mark_forced_included(self, request_id: bytes) -> None:
        if request_id in self.forced:
            self.forced[request_id].included = True

    # ── the invariant ─────────────────────────────────────────────────────────

    def l1_locked(self) -> int:
        return self.locked_on_l1

    def l2_total_value(self, account_balance_sum: int, open_escrow_sum: int) -> int:
        """Total ANM that L2 could ever pay out: live balances + open escrows +
        burns that left L2 accounts but are not yet claimed on L1."""
        unclaimed_burns = sum(
            wd.amount
            for wd in self.withdrawals.values()
            if wd.state != WithdrawState.CLAIMED
        )
        return account_balance_sum + open_escrow_sum + unclaimed_burns

    def check_invariant(self, account_balance_sum: int, open_escrow_sum: int) -> None:
        """Assert conservation. Raises BridgeError on violation — callers treat
        this as fatal and halt settlement rather than risk unbacked withdrawals.

        Conservation: everything currently inside L2 (balances + escrows) plus
        everything mid-exit (burned, unclaimed) must equal what has been locked
        on L1 and not yet released. Equivalently:

            balances + escrows + unclaimed_burns  ==  credited_total - claimed_on_l1

        and the withdrawable side is always <= locked_on_l1 by construction.
        """
        l2_value = self.l2_total_value(account_balance_sum, open_escrow_sum)
        expected = self.credited_total - self.claimed_on_l1_total
        if l2_value != expected:
            raise BridgeError(
                f"INVARIANT VIOLATION: L2 value {l2_value} != credited-claimed "
                f"{expected} (credited={self.credited_total}, "
                f"claimed_l1={self.claimed_on_l1_total})"
            )
        # The withdrawable amount must never exceed what is locked on L1.
        withdrawable = account_balance_sum + open_escrow_sum + sum(
            wd.amount
            for wd in self.withdrawals.values()
            if wd.state == WithdrawState.CLAIMABLE
        )
        if withdrawable > self.locked_on_l1 + self._burned_not_yet_locked_release():
            # locked_on_l1 already excludes finalized+claimed; claimable burns are
            # backed by locked funds not yet released. Guard against over-release.
            raise BridgeError(
                f"INVARIANT VIOLATION: withdrawable {withdrawable} > locked "
                f"{self.locked_on_l1}"
            )

    def _burned_not_yet_locked_release(self) -> int:
        # Claimable+burned withdrawals are backed by still-locked ANM; they are
        # released (locked_on_l1 decremented) only at claim time. This helper
        # keeps check_invariant's inequality exact.
        return 0

    def summary(self) -> dict:
        return {
            "lockedOnL1": self.locked_on_l1,
            "creditedTotal": self.credited_total,
            "burnedTotal": self.burned_total,
            "claimedOnL1Total": self.claimed_on_l1_total,
            "deposits": len(self.deposits),
            "claimableDeposits": len(self.claimable_deposits()),
            "withdrawals": len(self.withdrawals),
            "forcedPending": sum(1 for r in self.forced.values() if not r.included),
        }
