from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional

from aicf.registry.provider import Capability
from aicf.errors import InsufficientStake, RegistryError  # InsufficientStake requires kw: required_nano, actual_nano

@dataclass(frozen=True)
class _UnstakeReq:
    amount: int
    release_height: int

class Staking:
    def __init__(self, *, min_stake_ai: int, min_stake_quantum: int, unlock_delay_blocks: int) -> None:
        self._min_ai = int(min_stake_ai)
        self._min_q = int(min_stake_quantum)
        self._delay = int(unlock_delay_blocks)
        self._total: Dict[str, int] = {}
        self._pending: Dict[str, List[_UnstakeReq]] = {}

    # --- basics ---
    def stake(self, provider_id: str, amount: int) -> int:
        self._total[provider_id] = self._total.get(provider_id, 0) + int(amount)
        return self._total[provider_id]

    def increase(self, provider_id: str, amount: int) -> int:
        return self.stake(provider_id, amount)

    def total_stake(self, provider_id: str) -> int:
        return self._total.get(provider_id, 0)

    def _pending_sum(self, provider_id: str, *, current_height: Optional[int]) -> int:
        reqs = self._pending.get(provider_id, [])
        if current_height is None:
            # treat all pending as reducing effective stake
            return sum(r.amount for r in reqs)
        return sum(r.amount for r in reqs if r.release_height > current_height)

    def effective_stake(self, provider_id: str, *, current_height: int | None = None) -> int:
        total = self.total_stake(provider_id)
        pend = self._pending_sum(provider_id, current_height=current_height)
        eff = max(0, total - pend)
        return eff

    # --- minimums ---
    def _min_for_cap(self, cap: Capability) -> int:
        if cap & Capability.QUANTUM:
            return self._min_q
        if cap & Capability.AI:
            return self._min_ai
        return 0

    def ensure_minimum(self, provider_id: str, capability: Capability, *, current_height: int | None = None) -> None:
        need = self._min_for_cap(capability)
        eff = self.effective_stake(provider_id, current_height=current_height)
        if eff < need:
            # project exception wants keyword-only fields
            raise InsufficientStake(required_nano=need, actual_nano=eff)

    # --- unstake lifecycle ---
    def request_unstake(self, provider_id: str, *, amount: int, current_height: int) -> dict:
        amount = int(amount)
        if amount <= 0 or amount > self.total_stake(provider_id):
            raise RegistryError("invalid unstake amount")
        rel = int(current_height) + self._delay
        self._pending.setdefault(provider_id, []).append(_UnstakeReq(amount=amount, release_height=rel))
        return {"release_height": rel}

    def process_unlocks(self, current_height: int) -> List[_UnstakeReq]:
        out: List[_UnstakeReq] = []
        for pid, items in list(self._pending.items()):
            keep: List[_UnstakeReq] = []
            for r in items:
                if r.release_height <= current_height:
                    # mature -> reduce total, emit
                    self._total[pid] = max(0, self._total.get(pid, 0) - r.amount)
                    out.append(r)
                else:
                    keep.append(r)
            if keep:
                self._pending[pid] = keep
            else:
                self._pending.pop(pid, None)
        return out
