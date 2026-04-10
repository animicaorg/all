from __future__ import annotations

"""
Payout and RewardSplit types.

- RewardSplit expresses how a settlement total is split between
  provider / treasury / miner in basis points (bps, out of 10_000).
- Payout is a concrete settlement entry produced from one or more
  proof claims over a given accounting epoch or block height.

This module is intentionally small and pure (no DB/IO).
"""


from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from . import (BlockHeight, ProviderId, TaskId, Timestamp, TokenAmount,
               is_hex_id)

BPS_DENOM = 10_000  # basis points denominator (100.00%)


@dataclass
class RewardSplit:
    """
    Reward split in basis points (bps). All fields are integers in [0, 10_000]
    and must sum exactly to 10_000.
    """

    provider_bps: int
    treasury_bps: int
    miner_bps: int

    def validate(self) -> None:
        for name, val in (
            ("provider_bps", self.provider_bps),
            ("treasury_bps", self.treasury_bps),
            ("miner_bps", self.miner_bps),
        ):
            if not isinstance(val, int) or val < 0 or val > BPS_DENOM:
                raise ValueError(f"{name} must be an int in [0, {BPS_DENOM}]")
        total = self.provider_bps + self.treasury_bps + self.miner_bps
        if total != BPS_DENOM:
            raise ValueError(f"split bps must sum to {BPS_DENOM}, got {total}")

    def apply(
        self, amount_total: TokenAmount
    ) -> Tuple[TokenAmount, TokenAmount, TokenAmount]:
        """
        Deterministically split a total amount into (provider, treasury, miner).
        Remainders from integer division are assigned to miner to ensure:
            provider + treasury + miner == amount_total
        """
        self.validate()
        if amount_total < 0:
            raise ValueError("amount_total must be >= 0")

        prov = (amount_total * self.provider_bps) // BPS_DENOM
        tres = (amount_total * self.treasury_bps) // BPS_DENOM
        # assign remainder to miner to make sums exact
        mine = amount_total - prov - tres
        return TokenAmount(prov), TokenAmount(tres), TokenAmount(mine)

    def to_dict(self) -> Dict[str, int]:
        self.validate()
        return asdict(self)

    @staticmethod
    def from_dict(d: Mapping[str, int]) -> "RewardSplit":
        rs = RewardSplit(
            provider_bps=int(d.get("provider_bps", 0)),
            treasury_bps=int(d.get("treasury_bps", 0)),
            miner_bps=int(d.get("miner_bps", 0)),
        )
        rs.validate()
        return rs


@dataclass
class Payout:
    """
    A concrete payout entry for a provider, derived from one or more proof claims.

    Fields
    ------
    provider_id: Provider receiving the reward.
    amount_total: Total settlement amount before splitting.
    split: RewardSplit used for this payout.
    amount_provider / amount_treasury / amount_miner: Split amounts (sum equals amount_total).
    claims: TaskIds contributing to this payout (deterministic lowercase hex).
    epoch: Optional accounting epoch identifier (implementation-defined).
    height_settled: Block height at which this payout is recorded.
    settled_at: Optional UNIX seconds when settlement was finalized.
    """

    provider_id: ProviderId
    amount_total: TokenAmount
    split: RewardSplit
    amount_provider: TokenAmount
    amount_treasury: TokenAmount
    amount_miner: TokenAmount
    claims: List[TaskId]
    height_settled: BlockHeight
    epoch: Optional[int] = None
    settled_at: Optional[Timestamp] = None

    @staticmethod
    def from_amount(
        *,
        provider_id: ProviderId,
        amount_total: TokenAmount,
        split: RewardSplit,
        claims: Iterable[TaskId],
        height_settled: BlockHeight,
        epoch: Optional[int] = None,
        settled_at: Optional[Timestamp] = None,
    ) -> "Payout":
        """
        Build a Payout by applying the split to amount_total and validating inputs.
        """
        _require_hex_id(provider_id, "provider_id")
        if amount_total < 0:
            raise ValueError("amount_total must be >= 0")

        prov, tres, mine = split.apply(amount_total)

        claim_list = [TaskId(c) for c in claims]
        if not claim_list:
            raise ValueError("claims must contain at least one TaskId")
        for c in claim_list:
            _require_hex_id(c, "claims[]")

        if int(height_settled) < 0:
            raise ValueError("height_settled must be >= 0")
        if epoch is not None and epoch < 0:
            raise ValueError("epoch must be >= 0 when set")
        if settled_at is not None and int(settled_at) <= 0:
            raise ValueError("settled_at must be > 0 when set")

        return Payout(
            provider_id=ProviderId(provider_id),
            amount_total=TokenAmount(amount_total),
            split=split,
            amount_provider=TokenAmount(prov),
            amount_treasury=TokenAmount(tres),
            amount_miner=TokenAmount(mine),
            claims=claim_list,
            height_settled=BlockHeight(int(height_settled)),
            epoch=epoch,
            settled_at=settled_at,
        )

    def validate(self) -> None:
        _require_hex_id(self.provider_id, "provider_id")
        self.split.validate()
        if any(
            x < 0
            for x in (
                self.amount_total,
                self.amount_provider,
                self.amount_treasury,
                self.amount_miner,
            )
        ):
            raise ValueError("amounts must be >= 0")
        if (
            self.amount_provider + self.amount_treasury + self.amount_miner
            != self.amount_total
        ):
            raise ValueError("split amounts must sum to amount_total")
        if not self.claims:
            raise ValueError("claims must not be empty")
        for c in self.claims:
            _require_hex_id(c, "claims[]")
        if int(self.height_settled) < 0:
            raise ValueError("height_settled must be >= 0")
        if self.epoch is not None and self.epoch < 0:
            raise ValueError("epoch must be >= 0 when set")
        if self.settled_at is not None and int(self.settled_at) <= 0:
            raise ValueError("settled_at must be > 0 when set")

    def to_dict(self) -> Dict[str, object]:
        self.validate()
        d = asdict(self)
        d["split"] = self.split.to_dict()
        d["amount_total"] = int(self.amount_total)
        d["amount_provider"] = int(self.amount_provider)
        d["amount_treasury"] = int(self.amount_treasury)
        d["amount_miner"] = int(self.amount_miner)
        d["height_settled"] = int(self.height_settled)
        if self.settled_at is not None:
            d["settled_at"] = int(self.settled_at)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, object]) -> "Payout":
        split = RewardSplit.from_dict(
            d.get("split", {}) if isinstance(d.get("split"), Mapping) else {}
        )
        payout = Payout(
            provider_id=ProviderId(str(d.get("provider_id", ""))),
            amount_total=TokenAmount(int(d.get("amount_total", 0))),
            split=split,
            amount_provider=TokenAmount(int(d.get("amount_provider", 0))),
            amount_treasury=TokenAmount(int(d.get("amount_treasury", 0))),
            amount_miner=TokenAmount(int(d.get("amount_miner", 0))),
            claims=[TaskId(str(x)) for x in (d.get("claims") or [])],
            height_settled=BlockHeight(int(d.get("height_settled", 0))),
            epoch=(int(d["epoch"]) if d.get("epoch") is not None else None),
            settled_at=(
                Timestamp(int(d["settled_at"]))
                if d.get("settled_at") is not None
                else None
            ),
        )
        payout.validate()
        return payout


# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────


def _require_hex_id(v: str, label: str) -> None:
    if not is_hex_id(v):
        raise ValueError(f"{label} must be lowercase hex (no 0x)")
