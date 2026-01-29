from __future__ import annotations

"""
AICF Treasury — Provider Withdrawals
------------------------------------

Implements delayed provider withdrawals with an optional per-request cooldown.
Funds requested for withdrawal are *immediately locked* by debiting the provider's
internal balance at request time, then become *executable* after `delay_blocks`.
Execution simply marks the request as executed; higher layers (e.g. a bridge,
custodian, or operator) can read executed requests and perform the actual off-chain
transfer.

Design goals
  • Deterministic, integer-only accounting.
  • Storage-agnostic: state is held in a compact tracker with dump()/load().
  • Clear invariants: cannot over-withdraw; cooldown between requests; max pending.

Integration points
  • TreasuryState must support:
      - balance/available query (either `.available(pid)` or `.balance(pid)`)
      - `debit(pid, amount, height, reason)`
      - (optionally) `credit(pid, amount, height, reason)` for cancellations.
  • External settlement can poll `list_executable` / `finalize_due` and move funds.

"""

from dataclasses import asdict, dataclass
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

from aicf.aitypes.provider import ProviderId
from aicf.treasury.state import TreasuryState  # internal ledger


class WithdrawalError(Exception):
    """Base error for withdrawal operations."""


class CooldownNotElapsed(WithdrawalError):
    pass


class TooManyPending(WithdrawalError):
    pass


class InvalidRequest(WithdrawalError):
    pass


class InsufficientFunds(WithdrawalError):
    pass


class Status(Enum):
    PENDING = auto()
    EXECUTED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class WithdrawalRequest:
    id: int
    provider: ProviderId
    amount: int
    requested_height: int
    earliest_exec_height: int
    status: Status = Status.PENDING
    executed_height: Optional[int] = None
    cancelled_height: Optional[int] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "provider": str(self.provider),
            "amount": self.amount,
            "requested_height": self.requested_height,
            "earliest_exec_height": self.earliest_exec_height,
            "status": self.status.name,
            "executed_height": self.executed_height,
            "cancelled_height": self.cancelled_height,
        }

    @staticmethod
    def from_dict(d: Dict) -> "WithdrawalRequest":
        return WithdrawalRequest(
            id=int(d["id"]),
            provider=ProviderId(d["provider"]),
            amount=int(d["amount"]),
            requested_height=int(d["requested_height"]),
            earliest_exec_height=int(d["earliest_exec_height"]),
            status=Status[d["status"]],
            executed_height=d.get("executed_height"),
            cancelled_height=d.get("cancelled_height"),
        )


@dataclass
class WithdrawalConfig:
    """
    Config for withdrawals.

    - delay_blocks: minimum blocks to wait before a request may be executed
    - cooldown_blocks: minimum blocks between *requests* from the same provider
    - min_amount: optional floor for a single request
    - max_pending_per_provider: limit pending requests per provider
    - max_per_block_execute: optional cap on the *sum* executed in one block
    """

    delay_blocks: int = 7200  # ~1 day at 12s blocks
    cooldown_blocks: int = 0
    min_amount: int = 0
    max_pending_per_provider: int = 1
    max_per_block_execute: Optional[int] = None

    def validate(self) -> None:
        if self.delay_blocks < 0 or self.cooldown_blocks < 0:
            raise ValueError("delay_blocks and cooldown_blocks must be >= 0")
        if self.min_amount < 0:
            raise ValueError("min_amount must be >= 0")
        if self.max_pending_per_provider <= 0:
            raise ValueError("max_pending_per_provider must be > 0")


@dataclass
class WithdrawalTracker:
    """
    Persisted tracker state.

    - next_id: monotonic ID for new requests
    - pending: id -> serialized WithdrawalRequest
    - last_request_height: provider -> last request height (for cooldown)
    - last_height_processed: last height seen by finalize_due (optional)
    """

    next_id: int = 1
    pending: Dict[int, Dict] = None  # type: ignore
    last_request_height: Dict[str, int] = None  # type: ignore
    last_height_processed: Optional[int] = None

    def __post_init__(self) -> None:
        if self.pending is None:
            self.pending = {}
        if self.last_request_height is None:
            self.last_request_height = {}

    def to_dict(self) -> Dict:
        return {
            "next_id": self.next_id,
            "pending": {rid: req for rid, req in self.pending.items()},
            "last_request_height": dict(self.last_request_height),
            "last_height_processed": self.last_height_processed,
        }

    @staticmethod
    def from_dict(d: Dict) -> "WithdrawalTracker":
        wt = WithdrawalTracker(
            next_id=int(d.get("next_id", 1)),
            pending={int(k): v for k, v in (d.get("pending") or {}).items()},
            last_request_height={
                str(k): int(v) for k, v in (d.get("last_request_height") or {}).items()
            },
            last_height_processed=d.get("last_height_processed"),
        )
        return wt


class WithdrawalManager:
    """
    High-level withdrawal manager.

    Usage:
      cfg = WithdrawalConfig(...)
      mgr = WithdrawalManager(cfg, treasury, tracker=None)
      req = mgr.request(provider, amount, height)
      execd = mgr.finalize_due(height)  # execute matured requests (returns list)
    """

    __slots__ = ("cfg", "treasury", "_t")

    def __init__(
        self,
        cfg: WithdrawalConfig,
        treasury: TreasuryState,
        tracker: Optional[WithdrawalTracker] = None,
    ) -> None:
        cfg.validate()
        self.cfg = cfg
        self.treasury = treasury
        self._t = tracker or WithdrawalTracker()

    # --- persistence ---

    def dump(self) -> Dict:
        return self._t.to_dict()

    def load(self, data: Dict) -> None:
        self._t = WithdrawalTracker.from_dict(data)

    # --- queries ---

    def list_pending(
        self, provider: Optional[ProviderId] = None
    ) -> List[WithdrawalRequest]:
        out: List[WithdrawalRequest] = []
        for d in self._t.pending.values():
            req = WithdrawalRequest.from_dict(d)
            if req.status == Status.PENDING and (
                provider is None or req.provider == provider
            ):
                out.append(req)
        out.sort(key=lambda r: (r.earliest_exec_height, r.id))
        return out

    def list_executable(
        self, height: int, *, provider: Optional[ProviderId] = None
    ) -> List[WithdrawalRequest]:
        return [
            r for r in self.list_pending(provider) if r.earliest_exec_height <= height
        ]

    def next_allowed_request_height(self, provider: ProviderId) -> int:
        last = self._t.last_request_height.get(str(provider))
        if last is None:
            return 0
        return last + self.cfg.cooldown_blocks

    # --- core ops ---

    def request(
        self, provider: ProviderId, amount: int, height: int
    ) -> WithdrawalRequest:
        """
        Create a withdrawal request:
          • Enforces min_amount
          • Enforces cooldown between requests per provider
          • Enforces max pending per provider
          • Debits provider balance immediately to lock funds
        Returns the created request.
        """
        if amount <= 0:
            raise ValueError("amount must be > 0")
        if amount < self.cfg.min_amount:
            raise ValueError(f"amount below minimum ({self.cfg.min_amount})")
        if height < 0:
            raise ValueError("height must be >= 0")

        # cooldown
        next_allowed = self.next_allowed_request_height(provider)
        if height < next_allowed:
            raise CooldownNotElapsed(
                f"cooldown not elapsed: next at height >= {next_allowed}"
            )

        # pending count
        pending_for_provider = sum(
            1
            for r in self._t.pending.values()
            if r["status"] == Status.PENDING.name and r["provider"] == str(provider)
        )
        if pending_for_provider >= self.cfg.max_pending_per_provider:
            raise TooManyPending(
                f"max pending reached ({self.cfg.max_pending_per_provider})"
            )

        # sufficient funds
        available = self._available(provider)
        if amount > available:
            raise InsufficientFunds(
                f"insufficient funds: want {amount}, have {available}"
            )

        # lock funds
        self.treasury.debit(provider, amount, height=height, reason="withdraw-request")

        rid = self._t.next_id
        self._t.next_id += 1

        req = WithdrawalRequest(
            id=rid,
            provider=provider,
            amount=amount,
            requested_height=height,
            earliest_exec_height=height + self.cfg.delay_blocks,
            status=Status.PENDING,
        )
        self._t.pending[rid] = req.to_dict()
        self._t.last_request_height[str(provider)] = height
        return req

    def cancel(
        self, request_id: int, provider: ProviderId, height: int
    ) -> WithdrawalRequest:
        """
        Cancel a *pending* request; credits funds back to provider.
        """
        rec = self._t.pending.get(request_id)
        if not rec:
            raise InvalidRequest("unknown request id")
        req = WithdrawalRequest.from_dict(rec)
        if req.provider != provider:
            raise InvalidRequest("request does not belong to provider")
        if req.status != Status.PENDING:
            raise InvalidRequest(f"cannot cancel request in status {req.status.name}")
        # credit back
        self.treasury.credit(
            provider, req.amount, height=height, reason="withdraw-cancel"
        )
        # update record
        req = WithdrawalRequest(
            id=req.id,
            provider=req.provider,
            amount=req.amount,
            requested_height=req.requested_height,
            earliest_exec_height=req.earliest_exec_height,
            status=Status.CANCELLED,
            executed_height=None,
            cancelled_height=height,
        )
        self._t.pending[request_id] = req.to_dict()
        return req

    def execute(self, request_id: int, height: int) -> WithdrawalRequest:
        """
        Execute a single matured request (no external side-effects beyond marking executed).
        Respects cfg.max_per_block_execute if multiple calls are aggregated in finalize_due.
        """
        rec = self._t.pending.get(request_id)
        if not rec:
            raise InvalidRequest("unknown request id")
        req = WithdrawalRequest.from_dict(rec)
        if req.status != Status.PENDING:
            raise InvalidRequest(f"cannot execute request in status {req.status.name}")
        if height < req.earliest_exec_height:
            raise InvalidRequest(
                f"not yet executable (earliest {req.earliest_exec_height})"
            )

        executed = WithdrawalRequest(
            id=req.id,
            provider=req.provider,
            amount=req.amount,
            requested_height=req.requested_height,
            earliest_exec_height=req.earliest_exec_height,
            status=Status.EXECUTED,
            executed_height=height,
        )
        self._t.pending[request_id] = executed.to_dict()
        return executed

    def finalize_due(self, height: int) -> List[WithdrawalRequest]:
        """
        Execute all requests matured by `height`, optionally capped by max_per_block_execute.
        Returns the list of executed requests (may be empty).
        """
        matured = self.list_executable(height)
        executed: List[WithdrawalRequest] = []
        budget: Optional[int] = self.cfg.max_per_block_execute
        for req in matured:
            if budget is not None and budget <= 0:
                break
            if budget is not None and req.amount > budget:
                # skip larger-than-budget requests; continue to check smaller ones
                continue
            done = self.execute(req.id, height)
            executed.append(done)
            if budget is not None:
                budget -= done.amount

        self._t.last_height_processed = height
        return executed

    # --- helpers ---

    def _available(self, provider: ProviderId) -> int:
        # Prefer an explicit 'available' method if TreasuryState provides it, else fallback to balance.
        if hasattr(self.treasury, "available"):
            return int(self.treasury.available(provider))  # type: ignore[attr-defined]
        return int(self.treasury.balance(provider))  # type: ignore[attr-defined]
