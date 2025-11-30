from __future__ import annotations

"""
AICF Treasury — Rewards Settlement & Audit
------------------------------------------

Credits provider balances from settlement payouts and keeps an append-only
audit trail for external reconciliation.

Goals
  • Idempotent application of settlement batches (safe to retry).
  • Minimal coupling: accepts any "payout-like" object with fields:
      - id / payout_id
      - provider
      - amount
      - epoch (optional)
  • Deterministic integer accounting; storage-agnostic audit snapshot.

Typical usage
  treasury = TreasuryState(...)
  audit = RewardsAudit()  # or load from persisted state
  rewards = RewardsManager(treasury, audit)

  records = rewards.credit_from_payouts(
      payouts, height=current_height, settlement_id="epoch-1024"
  )
  snapshot = rewards.dump()  # persist

Integration
  • TreasuryState must provide:
      - credit(provider: ProviderId, amount: int, height: int, reason: str) -> None
      - balance(provider: ProviderId) or available(provider: ProviderId) for reads (not required here)
"""

from dataclasses import dataclass
from hashlib import sha3_256
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aicf.aitypes.provider import ProviderId
from aicf.treasury.state import TreasuryState

# ---------- Errors ----------


class RewardsError(Exception):
    """Base class for rewards errors."""


class DuplicatePayout(RewardsError):
    """Raised when a payout with the same payout_id has already been applied and skip_duplicates=False."""


class InvalidPayout(RewardsError):
    """Raised when a payout record is missing required fields or contains invalid values."""


# ---------- Records & Audit ----------


@dataclass(frozen=True)
class CreditRecord:
    """
    Immutable record representing a single credited payout.

    Fields
      • credit_id: deterministic unique id for this credit (sha3_256(settlement_id|payout_id))
      • payout_id: external identifier for the payout (idempotency key)
      • settlement_id: caller-provided batch identifier (e.g., 'epoch-1234', tx hash)
      • provider: ProviderId that received the credit
      • amount: integer units credited
      • epoch: optional accounting epoch number
      • height: chain height when the credit was recorded
      • note: optional free-form text for external systems
    """

    credit_id: str
    payout_id: str
    settlement_id: str
    provider: ProviderId
    amount: int
    epoch: Optional[int]
    height: int
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "credit_id": self.credit_id,
            "payout_id": self.payout_id,
            "settlement_id": self.settlement_id,
            "provider": str(self.provider),
            "amount": self.amount,
            "epoch": self.epoch,
            "height": self.height,
            "note": self.note,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CreditRecord":
        return CreditRecord(
            credit_id=str(d["credit_id"]),
            payout_id=str(d["payout_id"]),
            settlement_id=str(d["settlement_id"]),
            provider=ProviderId(str(d["provider"])),
            amount=int(d["amount"]),
            epoch=None if d.get("epoch") is None else int(d["epoch"]),
            height=int(d["height"]),
            note=d.get("note"),
        )


@dataclass
class RewardsAudit:
    """
    Persistable audit state:
      • credits: credit_id -> serialized CreditRecord
      • payout_index: payout_id -> credit_id (for idempotency)
      • provider_totals: provider -> cumulative credited amount
      • last_height_processed: watermark for readers (optional)
    """

    credits: Dict[str, Dict[str, Any]]
    payout_index: Dict[str, str]
    provider_totals: Dict[str, int]
    last_height_processed: Optional[int] = None

    def __init__(self) -> None:
        self.credits = {}
        self.payout_index = {}
        self.provider_totals = {}
        self.last_height_processed = None

    def dump(self) -> Dict[str, Any]:
        return {
            "credits": {k: dict(v) for k, v in self.credits.items()},
            "payout_index": dict(self.payout_index),
            "provider_totals": dict(self.provider_totals),
            "last_height_processed": self.last_height_processed,
        }

    @staticmethod
    def load(data: Dict[str, Any]) -> "RewardsAudit":
        r = RewardsAudit()
        r.credits = {str(k): dict(v) for k, v in (data.get("credits") or {}).items()}
        r.payout_index = {
            str(k): str(v) for k, v in (data.get("payout_index") or {}).items()
        }
        r.provider_totals = {
            str(k): int(v) for k, v in (data.get("provider_totals") or {}).items()
        }
        r.last_height_processed = data.get("last_height_processed")
        return r


# ---------- Manager ----------


class RewardsManager:
    """
    Applies settlements to the treasury and records an audit trail.
    """

    __slots__ = ("treasury", "audit")

    def __init__(
        self, treasury: TreasuryState, audit: Optional[RewardsAudit] = None
    ) -> None:
        self.treasury = treasury
        self.audit = audit or RewardsAudit()

    # ---- persistence ----

    def dump(self) -> Dict[str, Any]:
        return self.audit.dump()

    def load(self, data: Dict[str, Any]) -> None:
        self.audit = RewardsAudit.load(data)

    # ---- queries ----

    def list_credits(
        self,
        *,
        provider: Optional[ProviderId] = None,
        since_height: Optional[int] = None,
    ) -> List[CreditRecord]:
        out: List[CreditRecord] = []
        for rec in self.audit.credits.values():
            cr = CreditRecord.from_dict(rec)
            if provider is not None and cr.provider != provider:
                continue
            if since_height is not None and cr.height < since_height:
                continue
            out.append(cr)
        out.sort(key=lambda r: (r.height, r.credit_id))
        return out

    def totals_for_provider(self, provider: ProviderId) -> int:
        return int(self.audit.provider_totals.get(str(provider), 0))

    def has_payout(self, payout_id: str) -> bool:
        return payout_id in self.audit.payout_index

    # ---- application ----

    def credit_from_payouts(
        self,
        payouts: Iterable[Any],
        *,
        height: int,
        settlement_id: str,
        note: Optional[str] = None,
        skip_duplicates: bool = True,
    ) -> List[CreditRecord]:
        """
        Apply a batch of payouts at `height`, crediting the treasury and recording audit entries.

        Idempotency:
          - Each payout must have a unique payout_id (string/number).
          - We compute credit_id = sha3_256( settlement_id || "|" || payout_id ).
          - If a payout_id is already present:
              * if skip_duplicates=True (default), it is ignored.
              * else, DuplicatePayout is raised.

        Returns the list of newly created CreditRecord entries (duplicates are not returned).
        """
        if not settlement_id:
            raise InvalidPayout("settlement_id must be a non-empty string")
        if height < 0:
            raise InvalidPayout("height must be >= 0")

        created: List[CreditRecord] = []

        for p in payouts:
            payout_id, provider, amount, epoch = _coerce_payout(p)

            if amount <= 0:
                raise InvalidPayout(f"payout amount must be > 0 (got {amount})")

            if self.has_payout(payout_id):
                if skip_duplicates:
                    continue
                raise DuplicatePayout(f"payout {payout_id} already applied")

            credit_id = _make_credit_id(settlement_id, payout_id)

            # Credit treasury
            self.treasury.credit(
                provider,
                amount,
                height=height,
                reason=f"settlement:{settlement_id} payout:{payout_id}",
            )

            # Update audit indexes
            record = CreditRecord(
                credit_id=credit_id,
                payout_id=payout_id,
                settlement_id=settlement_id,
                provider=provider,
                amount=amount,
                epoch=epoch,
                height=height,
                note=note,
            )
            self.audit.credits[credit_id] = record.to_dict()
            self.audit.payout_index[payout_id] = credit_id
            key = str(provider)
            self.audit.provider_totals[key] = (
                self.audit.provider_totals.get(key, 0) + amount
            )

            created.append(record)

        self.audit.last_height_processed = height
        return created


# ---------- helpers ----------


def _coerce_payout(p: Any) -> Tuple[str, ProviderId, int, Optional[int]]:
    """
    Extract (payout_id, provider, amount, epoch?) from either a dataclass-like object or dict.

    Recognized field names:
      id or payout_id, provider, amount, epoch
    """
    # Dict-like
    if isinstance(p, dict):
        payout_id = str(p.get("id", p.get("payout_id")))
        provider = ProviderId(str(p["provider"]))
        amount = int(p["amount"])
        epoch = p.get("epoch")
        epoch = None if epoch is None else int(epoch)
    else:
        # Object-like
        payout_id = str(getattr(p, "id", getattr(p, "payout_id", None)))
        provider = ProviderId(str(getattr(p, "provider")))
        amount = int(getattr(p, "amount"))
        epoch_val = getattr(p, "epoch", None)
        epoch = None if epoch_val is None else int(epoch_val)

    if not payout_id:
        raise InvalidPayout("payout missing id/payout_id")
    if amount < 0:
        raise InvalidPayout("amount must be >= 0")
    return payout_id, provider, amount, epoch


def _make_credit_id(settlement_id: str, payout_id: str) -> str:
    h = sha3_256()
    h.update(b"aicf:rewards:v1|")
    h.update(settlement_id.encode("utf-8"))
    h.update(b"|")
    h.update(payout_id.encode("utf-8"))
    return "0x" + h.hexdigest()


__all__ = [
    "RewardsError",
    "DuplicatePayout",
    "InvalidPayout",
    "CreditRecord",
    "RewardsAudit",
    "RewardsManager",
]
