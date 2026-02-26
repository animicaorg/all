from __future__ import annotations

from dataclasses import dataclass

from .aicf_client import AicfClient


@dataclass
class EarningsSnapshot:
    address: str
    credits: int
    claimable: int


class EarningsService:
    def __init__(self, aicf_client: AicfClient) -> None:
        self.aicf = aicf_client

    def snapshot(self, address: str) -> EarningsSnapshot:
        credits = self.aicf.credits_by_address(address)
        claimable = self.aicf.get_claimable(address)
        return EarningsSnapshot(
            address=address,
            credits=int(credits.get("credits", 0)),
            claimable=int(claimable.get("claimable", 0)),
        )
