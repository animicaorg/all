"""
mempool.sequence
================

Per-sender nonce queues with **gap handling** and **ready/held** sets.

Design
------
- We maintain a `SenderQueue` for each account (sender).
- Each queue tracks:
    * `next_nonce`: the **lowest** nonce not yet executed/consumed.
    * `txs`: mapping nonce -> PoolTx (covers both ready and held).
    * `ready_end`: smallest nonce **>= next_nonce** that is **missing** from `txs`.
      The *ready* window is the half-open range `[next_nonce, ready_end)`.
      Nonces `>= ready_end` are *held* (blocked by a gap).
- Upsert logic handles duplicates and RBF replacements (via `mempool.priority.should_replace`).

Operations
----------
- `NonceSequencer.admit(tx, base_fee_wei, rbf_policy)`:
    Insert or RBF-replace a transaction into the appropriate SenderQueue.
- `collect_ready(max_total=None)`:
    Round-robin traversal over senders to return ready transactions in nonce order.
- `consume(sender, nonce)`:
    Mark a nonce as executed/committed, advance `next_nonce` and promote held txs.
- `evict(sender, nonce)`:
    Remove a specific tx (e.g., TTL/size pressure); recompute `ready_end` if needed.
- `snapshot_*` helpers aid tests/metrics.

The module is dependency-light and will gracefully degrade if the optional imports
aren't available at import time (useful for isolated unit tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

# Optional types to avoid import cycles during isolated testing
try:
    from mempool.types import PoolTx, TxMeta  # type: ignore
except Exception:  # pragma: no cover

    @dataclass
    class TxMeta:  # type: ignore
        sender: str
        nonce: int
        gas_limit: int = 21_000
        size_bytes: int = 100
        first_seen: float = 0.0
        last_seen: float = 0.0
        priority_score: float = 0.0

    @dataclass
    class PoolTx:  # type: ignore
        tx_hash: str
        meta: TxMeta
        fee: object
        raw: bytes


# Optional RBF policy
try:
    from mempool.priority import RBFPolicy, should_replace  # type: ignore
except Exception:  # pragma: no cover

    @dataclass(frozen=True)
    class RBFPolicy:  # type: ignore
        rel_bump: float = 0.10
        abs_bump_wei: int = 2_000_000_000
        require_gas_limit_ge: bool = True
        copy_restrictions: bool = False

    def should_replace(  # type: ignore
        *,
        existing: PoolTx,
        candidate_fee: object,
        candidate_gas_limit: int,
        base_fee_wei: Optional[int],
        rbf: Optional[RBFPolicy] = None,
    ) -> Tuple[bool, str]:
        return (False, "rbf_disabled")


__all__ = [
    "SenderQueue",
    "NonceSequencer",
    "AdmitResult",
]


@dataclass(frozen=True)
class AdmitResult:
    """
    Outcome of an attempt to admit a transaction into a SenderQueue.
    """

    ok: bool
    action: str  # "added_ready" | "added_held" | "replaced_ready" | "replaced_held" | "rejected_*"
    reason: Optional[str] = None


@dataclass
class SenderQueue:
    """
    Per-sender nonce sequencing with gap handling.

    Invariants:
      - `next_nonce` is the smallest nonce not yet consumed.
      - `ready_end` is the smallest nonce >= next_nonce that is missing in `txs`.
      - For all n in [next_nonce, ready_end): n in txs.
    """

    sender: str
    next_nonce: int
    txs: Dict[int, PoolTx] = field(default_factory=dict)
    ready_end: int = field(init=False)

    def __post_init__(self) -> None:
        # Compute initial ready_end based on any prefilled txs (rare).
        object.__setattr__(self, "ready_end", self.next_nonce)
        self._advance_ready_end()

    # ----------------------------
    # Admission & replacement
    # ----------------------------

    def admit(
        self,
        tx: PoolTx,
        *,
        base_fee_wei: Optional[int] = None,
        rbf_policy: Optional[RBFPolicy] = None,
    ) -> AdmitResult:
        """
        Insert or RBF-replace a tx for this sender.

        Returns:
            AdmitResult(ok=True, action=...) on success, or ok=False with reason.
        """
        n = int(tx.meta.nonce)
        # Reject nonces lower than the base cursor: already executed/confirmed.
        if n < self.next_nonce:
            return AdmitResult(False, "rejected_nonce_too_low", "nonce_below_next")

        existing = self.txs.get(n)
        if existing is not None:
            # Possible RBF replacement
            allowed, reason = should_replace(
                existing=existing,
                candidate_fee=tx.fee,
                candidate_gas_limit=int(tx.meta.gas_limit),
                base_fee_wei=base_fee_wei,
                rbf=rbf_policy,
            )
            if not allowed:
                return AdmitResult(False, "rejected_rbf", reason)
            # Replace
            self.txs[n] = tx
            # If it was in the ready window, it stays ready
            if self.next_nonce <= n < self.ready_end:
                return AdmitResult(True, "replaced_ready", None)
            return AdmitResult(True, "replaced_held", None)

        # Insert new
        self.txs[n] = tx
        # If we filled the immediate gap at ready_end, advance contiguously.
        if n == self.ready_end:
            self._advance_ready_end()
            return AdmitResult(True, "added_ready", None)

        # If n < ready_end, it is necessarily in ready range only if contiguous,
        # but that cannot happen because ready_end marks the first missing nonce.
        # Therefore n < ready_end implies a logic error (shouldn't be missing).
        if n < self.ready_end:  # pragma: no cover - defensive
            self._recompute_ready_end()
            return AdmitResult(True, "added_ready_after_fixup", "recomputed_ready_end")

        # Otherwise, it's held behind a gap.
        return AdmitResult(True, "added_held", None)

    # ----------------------------
    # Consume / Evict
    # ----------------------------

    def consume(self, nonce: int) -> bool:
        """
        Mark a ready nonce as executed/committed and advance `next_nonce`.
        Returns True if removal happened, False if there was no tx at that nonce.
        """
        if nonce not in self.txs:
            return False
        # Remove the entry
        self.txs.pop(nonce, None)

        # If we consumed the head, advance next_nonce
        if nonce == self.next_nonce:
            object.__setattr__(self, "next_nonce", self.next_nonce + 1)
            # The ready window may extend if held txs fill the next gaps.
            if self.ready_end <= self.next_nonce:
                object.__setattr__(self, "ready_end", self.next_nonce)
            self._advance_ready_end()
        else:
            # We consumed something beyond head (e.g., eviction/rollback); recompute boundaries.
            self._recompute_ready_end()

        return True

    def evict(self, nonce: int) -> bool:
        """
        Remove a tx (e.g., TTL or pressure). Returns True if found & removed.
        Recomputes `ready_end` if we created a gap inside the ready window.
        """
        if nonce not in self.txs:
            return False
        self.txs.pop(nonce, None)
        if self.next_nonce <= nonce < self.ready_end:
            # We just created the first gap at or before `nonce`.
            object.__setattr__(self, "ready_end", nonce)
        # Ensure consistency (cheap linear walk across contiguous prefix)
        self._advance_ready_end()
        return True

    # ----------------------------
    # Queries
    # ----------------------------

    def ready_nonces(self) -> List[int]:
        """List of contiguous ready nonces in order."""
        return list(range(self.next_nonce, self.ready_end))

    def ready(self) -> List[PoolTx]:
        """List of contiguous ready transactions in nonce order."""
        return [self.txs[n] for n in self.ready_nonces()]

    def held_nonces(self) -> List[int]:
        """List of nonces that are present but blocked behind a gap."""
        r = []
        for n in sorted(self.txs.keys()):
            if n >= self.ready_end:
                r.append(n)
        return r

    def has_ready(self) -> bool:
        return self.ready_end > self.next_nonce

    def peek_next(self) -> Optional[PoolTx]:
        """Return the next-ready tx (at `next_nonce`) if present."""
        if self.has_ready():
            return self.txs.get(self.next_nonce)
        return None

    def __len__(self) -> int:
        return len(self.txs)

    # ----------------------------
    # Internal maintenance
    # ----------------------------

    def _advance_ready_end(self) -> None:
        """
        Extend `ready_end` rightwards while the next nonce exists.
        """
        re = self.ready_end
        while self.txs.get(re) is not None:
            re += 1
        if re != self.ready_end:
            object.__setattr__(self, "ready_end", re)

    def _recompute_ready_end(self) -> None:
        """
        Recompute `ready_end` from `next_nonce` (O(#ready)).
        """
        re = self.next_nonce
        while self.txs.get(re) is not None:
            re += 1
        object.__setattr__(self, "ready_end", re)

    # ----------------------------
    # Snapshots (for tests/metrics)
    # ----------------------------

    def snapshot(self) -> Dict[str, object]:
        return {
            "sender": self.sender,
            "next_nonce": self.next_nonce,
            "ready_end": self.ready_end,
            "ready_nonces": self.ready_nonces(),
            "held_nonces": self.held_nonces(),
            "size": len(self.txs),
        }


class NonceSequencer:
    """
    Orchestrates `SenderQueue`s for all senders and exposes:
      - admission with RBF handling
      - round-robin collection of ready transactions
      - targeted consume/evict operations
    """

    def __init__(self) -> None:
        self._queues: Dict[str, SenderQueue] = {}
        # Round-robin state
        self._rr_senders: List[str] = []
        self._rr_index: int = 0

    # ----------------------------
    # Admission
    # ----------------------------

    def admit(
        self,
        tx: PoolTx,
        *,
        base_fee_wei: Optional[int] = None,
        rbf_policy: Optional[RBFPolicy] = None,
        sender_next_nonce_hint: Optional[int] = None,
    ) -> AdmitResult:
        """
        Insert/RBF a tx into the per-sender queue. Creates the queue if needed.

        Args:
            sender_next_nonce_hint: if provided and queue does not exist,
                initialize `next_nonce` to this hint (from state DB).
        """
        s = tx.meta.sender
        q = self._queues.get(s)
        if q is None:
            base_nonce = int(
                sender_next_nonce_hint
                if sender_next_nonce_hint is not None
                else tx.meta.nonce
            )
            q = SenderQueue(sender=s, next_nonce=base_nonce)
            self._queues[s] = q
            self._maybe_add_rr_sender(s)

        res = q.admit(tx, base_fee_wei=base_fee_wei, rbf_policy=rbf_policy)
        return res

    # ----------------------------
    # Ready collection
    # ----------------------------

    def collect_ready(self, max_total: Optional[int] = None) -> List[PoolTx]:
        """
        Round-robin over senders and gather ready transactions in contiguous
        nonce order per sender. This prevents one hot sender from starving others.

        If `max_total` is provided, stop after collecting that many txs.
        """
        if not self._rr_senders:
            return []

        out: List[PoolTx] = []
        remaining = max_total if max_total is not None else float("inf")

        start_idx = self._rr_index % len(self._rr_senders)
        i = start_idx
        visited = 0

        while remaining > 0 and visited < len(self._rr_senders):
            sender = self._rr_senders[i]
            q = self._queues.get(sender)
            if q is not None and q.has_ready():
                # Pull exactly one from this sender to be fair, advance cursor later
                nxt = q.peek_next()
                if nxt is not None:
                    out.append(nxt)
                    remaining = remaining - 1
            # advance round-robin pointer
            i = (i + 1) % len(self._rr_senders)
            visited += 1

        # Update rr index for next call
        self._rr_index = i
        return out

    # ----------------------------
    # Consume & Evict
    # ----------------------------

    def consume(self, sender: str, nonce: int) -> bool:
        """
        Mark (sender, nonce) as executed/committed and update queue state.
        Returns True if a tx was removed from the queue.
        """
        q = self._queues.get(sender)
        if q is None:
            return False
        removed = q.consume(nonce)
        if removed and len(q) == 0:
            self._remove_rr_sender(sender)
        return removed

    def evict(self, sender: str, nonce: int) -> bool:
        """
        Remove a tx (TTL or pressure). Returns True if found & removed.
        """
        q = self._queues.get(sender)
        if q is None:
            return False
        removed = q.evict(nonce)
        if removed and len(q) == 0:
            self._remove_rr_sender(sender)
        return removed

    def drop_sender(self, sender: str) -> None:
        """Remove the entire sender queue (e.g., on account deletion)."""
        if sender in self._queues:
            del self._queues[sender]
            self._remove_rr_sender(sender)

    # ----------------------------
    # Introspection
    # ----------------------------

    def sender_queue(self, sender: str) -> Optional[SenderQueue]:
        return self._queues.get(sender)

    def senders(self) -> Iterable[str]:
        return self._queues.keys()

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        return {s: q.snapshot() for s, q in self._queues.items()}

    # ----------------------------
    # Round-robin helpers
    # ----------------------------

    def _maybe_add_rr_sender(self, sender: str) -> None:
        if sender not in self._rr_senders:
            self._rr_senders.append(sender)

    def _remove_rr_sender(self, sender: str) -> None:
        try:
            idx = self._rr_senders.index(sender)
        except ValueError:
            return
        del self._rr_senders[idx]
        # Adjust rr_index if needed
        if self._rr_senders:
            self._rr_index = self._rr_index % len(self._rr_senders)
        else:
            self._rr_index = 0


# Convenience: tiny self-test (manual) when run as a script
if __name__ == "__main__":  # pragma: no cover
    from pprint import pprint

    def mk(sender: str, nonce: int) -> PoolTx:
        return PoolTx(
            tx_hash=f"{sender}:{nonce}",
            meta=TxMeta(sender=sender, nonce=nonce),
            fee=object(),
            raw=b"x",
        )

    seq = NonceSequencer()
    # Assume account A needs next_nonce=5 (hinted from state)
    for n in [6, 8, 5, 7]:
        ar = seq.admit(mk("A", n), sender_next_nonce_hint=5)
        print("admit", n, ar)
    pprint(seq.snapshot())
    ready = seq.collect_ready()
    print("ready batch:", [t.tx_hash for t in ready])
    # Consume 5 and 6, then check promotion of 7
    seq.consume("A", 5)
    seq.consume("A", 6)
    pprint(seq.snapshot())
    ready = seq.collect_ready()
    print("ready batch:", [t.tx_hash for t in ready])
