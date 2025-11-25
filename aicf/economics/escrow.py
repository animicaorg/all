from __future__ import annotations
from aicf.queue.jobkind import JobKind
"""
Escrow: hold requester funds for off-chain jobs and unlock them deterministically.

This module offers a tiny, deterministic escrow ledger used by AICF to:
- place a hold when a job is accepted for processing,
- release funds to (provider, treasury, miner) upon successful proof,
- refund the requester on failure/expiration,
- optionally slash a portion to treasury on policy violations.

Design notes
------------
- Pure integer math; no floats, time, or randomness.
- Stateless w.r.t. chain I/O: this is *accounting only*. Actual transfers are
  performed by the execution/treasury subsystem elsewhere.
- Minimal in-memory store suitable for devnet/tests. A persistent backend can
  implement the same interface later.

Typical flow
------------
1) create_hold(task_id, requester, amount, created_height, ttl_blocks)
2) (later) either:
   - release(kind, policy)              -> payout to provider/treasury/miner
   - refund(reason)                     -> refund to requester
   - slash(percent_bps, reason)         -> treasury gets a cut, remainder refunded
3) expire_if_due(current_height)        -> auto-refund when past unlock height
"""


from dataclasses import dataclass, field
from typing import Dict,Final,Optional,Tuple

from ..errors import AICFError
from .split import (
    DEFAULT_SPLIT_POLICY,
    SplitPolicy,
    split_for_kind,
)

try:  # pragma: no cover - convenience import if present
    from ..types.payout import Payout  # type: ignore
except Exception:  # pragma: no cover
    Payout = None  # type: ignore


Amount = int
HoldStatus = Literal["HELD", "RELEASED", "REFUNDED", "SLASHED", "EXPIRED"]


class EscrowError(AICFError):
    """Raised for escrow-specific failures."""


@dataclass(frozen=True)
class EscrowHold:
    task_id: str
    requester: str  # address/identifier (opaque string here)
    amount: Amount
    created_height: int
    unlock_height: int
    status: HoldStatus = "HELD"
    # Settlement fields (populated when resolved)
    settled_at_height: Optional[int] = None
    # Resolution breakdowns (all optional; dependent on outcome)
    provider_amount: Optional[Amount] = None
    treasury_amount: Optional[Amount] = None
    miner_amount: Optional[Amount] = None
    requester_refund: Optional[Amount] = None
    reason: Optional[str] = None

    def can_unlock(self, current_height: int) -> bool:
        return current_height >= self.unlock_height and self.status == "HELD"


class EscrowStore:
    """
    Minimal in-memory store. Replaceable by a persistent backend with the same API.
    """

    def __init__(self) -> None:
        self._holds: Dict[str, EscrowHold] = {}

    def get(self, task_id: str) -> Optional[EscrowHold]:
        return self._holds.get(task_id)

    def put_new(self, hold: EscrowHold) -> None:
        if hold.task_id in self._holds:
            raise EscrowError(f"hold already exists for task_id={hold.task_id}")
        self._holds[hold.task_id] = hold

    def update(self, hold: EscrowHold) -> None:
        if hold.task_id not in self._holds:
            raise EscrowError(f"no such hold task_id={hold.task_id}")
        self._holds[hold.task_id] = hold


DEFAULT_TTL_BLOCKS: Final[int] = 3 * 60  # ~3 hours if 3-min blocks; devnet-friendly


class EscrowLedger:
    """
    Deterministic escrow orchestrator operating over an EscrowStore.
    """

    def __init__(self, store: Optional[EscrowStore] = None) -> None:
        self.store = store or EscrowStore()

    # ---- Create / inspect -------------------------------------------------

    def create_hold(
        self,
        *,
        task_id: str,
        requester: str,
        amount: Amount,
        created_height: int,
        ttl_blocks: int = DEFAULT_TTL_BLOCKS,
    ) -> EscrowHold:
        if not isinstance(amount, int) or amount < 0:
            raise EscrowError(f"amount must be non-negative int, got {amount!r}")
        if ttl_blocks <= 0:
            raise EscrowError(f"ttl_blocks must be > 0, got {ttl_blocks}")
        unlock_height = created_height + ttl_blocks
        hold = EscrowHold(
            task_id=task_id,
            requester=requester,
            amount=amount,
            created_height=created_height,
            unlock_height=unlock_height,
            status="HELD",
        )
        self.store.put_new(hold)
        return hold

    def get(self, task_id: str) -> EscrowHold:
        hold = self.store.get(task_id)
        if not hold:
            raise EscrowError(f"no escrow hold for task_id={task_id}")
        return hold

    # ---- Resolution paths -------------------------------------------------

    def release(
        self,
        *,
        task_id: str,
        kind: JobKind,
        current_height: int,
        policy: SplitPolicy = DEFAULT_SPLIT_POLICY,
        reason: str = "proof_accepted",
    ):
        """
        Release a held amount to (provider, treasury, miner) by policy split.

        Returns a Payout (if available) or a dict with fields:
        {kind,total,provider,treasury,miner}. The EscrowHold is updated with
        status=RELEASED and settlement fields. No on-chain transfers are
        executed here.
        """
        hold = self.get(task_id)
        if hold.status != "HELD":
            raise EscrowError(f"hold not in HELD state (status={hold.status})")

        provider_amt, treasury_amt, miner_amt = split_for_kind(
            kind, total=hold.amount, policy=policy
        )

        # Update store with settlement
        settled = EscrowHold(
            **{
                **hold.__dict__,
                "status": "RELEASED",
                "settled_at_height": current_height,
                "provider_amount": provider_amt,
                "treasury_amount": treasury_amt,
                "miner_amount": miner_amt,
                "requester_refund": 0,
                "reason": reason,
            }
        )
        self.store.update(settled)

        # Build payout structure compatible with aicf.aitypes.payout if present
        if Payout is not None:
            try:
                return Payout(  # type: ignore[call-arg]
                    kind=kind,
                    total=hold.amount,
                    provider=provider_amt,
                    treasury=treasury_amt,
                    miner=miner_amt,
                )
            except TypeError as e:  # pragma: no cover
                # Fallback cleanly if Payout signature differs
                pass

        return {
            "kind": kind,
            "total": hold.amount,
            "provider": provider_amt,
            "treasury": treasury_amt,
            "miner": miner_amt,
        }

    def refund(
        self,
        *,
        task_id: str,
        current_height: int,
        reason: str = "failed_or_cancelled",
    ) -> EscrowHold:
        """
        Refund the requester the full held amount. Marks status=REFUNDED.
        """
        hold = self.get(task_id)
        if hold.status != "HELD":
            raise EscrowError(f"hold not in HELD state (status={hold.status})")

        settled = EscrowHold(
            **{
                **hold.__dict__,
                "status": "REFUNDED",
                "settled_at_height": current_height,
                "provider_amount": 0,
                "treasury_amount": 0,
                "miner_amount": 0,
                "requester_refund": hold.amount,
                "reason": reason,
            }
        )
        self.store.update(settled)
        return settled

    def slash(
        self,
        *,
        task_id: str,
        current_height: int,
        percent_bps_to_treasury: int,
        reason: str = "policy_violation",
    ) -> EscrowHold:
        """
        Slash a portion of the held funds to *treasury*, refund the remainder.

        Example: percent_bps_to_treasury=1000 (10%) -> 10% to treasury, 90% refund.
        """
        if percent_bps_to_treasury < 0 or percent_bps_to_treasury > 10_000:
            raise EscrowError("percent_bps_to_treasury must be in [0, 10_000]")

        hold = self.get(task_id)
        if hold.status != "HELD":
            raise EscrowError(f"hold not in HELD state (status={hold.status})")

        tres = (hold.amount * percent_bps_to_treasury) // 10_000
        refund = hold.amount - tres

        settled = EscrowHold(
            **{
                **hold.__dict__,
                "status": "SLASHED",
                "settled_at_height": current_height,
                "provider_amount": 0,
                "treasury_amount": tres,
                "miner_amount": 0,
                "requester_refund": refund,
                "reason": reason,
            }
        )
        self.store.update(settled)
        return settled

    def expire_if_due(self, *, task_id: str, current_height: int) -> Optional[EscrowHold]:
        """
        If a hold has reached its unlock height without being released, mark EXPIRED and refund.
        Returns the updated hold if an expiration occurred, else None.
        """
        hold = self.get(task_id)
        if hold.status != "HELD":
            return None
        if not hold.can_unlock(current_height):
            return None
        settled = EscrowHold(
            **{
                **hold.__dict__,
                "status": "EXPIRED",
                "settled_at_height": current_height,
                "provider_amount": 0,
                "treasury_amount": 0,
                "miner_amount": 0,
                "requester_refund": hold.amount,
                "reason": "expired_unlock",
            }
        )
        self.store.update(settled)
        return settled


__all__ = [
    "EscrowError",
    "EscrowHold",
    "EscrowStore",
    "EscrowLedger",
    "DEFAULT_TTL_BLOCKS",
    "JobKind",
    "HoldStatus",
]
