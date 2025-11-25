"""
mempool.policy
==============

Centralized admission & replacement policy plus a lightweight
sender-ban mechanism for abusive peers.

This module is *pure policy*: it does not mutate the mempool. It exposes
helpers that callers (e.g., mempool.pool.Pool or RPC ingress) can use
to decide whether to accept, replace, or temporarily ban a sender.

Key concepts
------------
- Admission rules: size limits, fee floors (optionally via FeeWatermark),
  basic allow/deny checks, and "local" bypass knobs.
- Replacement (RBF): same sender+nonce can replace only if the *effective*
  fee is ≥ (1 + bump_ratio) × old. Optionally defer to mempool.priority.
- Bans: per-sender temporary bans with reason codes and durations.
  Designed as a minimal DoS backstop; all times use a monotonic clock.

This file intentionally avoids importing heavy dependencies. All inputs
are passed in (e.g., FeeWatermark) or taken from Tx/Meta attributes.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Callable
import time

# Prefer the canonical error types if available
try:
    from .errors import (
        AdmissionError,
        ReplacementError,
        DoSError,
        FeeTooLow,
        Oversize,
    )
except Exception:  # pragma: no cover - fallback types for stand-alone use
    class AdmissionError(Exception): ...
    class ReplacementError(Exception): ...
    class DoSError(Exception): ...
    class FeeTooLow(AdmissionError): ...
    class Oversize(AdmissionError): ...

# Optional watermark integration
try:
    from .watermark import FeeWatermark
except Exception:  # pragma: no cover
    FeeWatermark = object  # type: ignore[assignment]

# Optional priority integration (effective_priority, rbf_min_bump)
try:
    from . import priority as _priority
except Exception:  # pragma: no cover
    _priority = None  # type: ignore[assignment]

Clock = Callable[[], float]


def _now_monotonic() -> float:
    return time.monotonic()


# -------------------------
# Ban policy
# -------------------------

@dataclass
class BanPolicy:
    """
    Parameters controlling temporary sender bans.

    A "ban" here is an admission short-circuit: while active, all new
    txs from the sender should be rejected with DoSError (or a derived
    type) at the *ingress* boundary, before any heavy checks.

    - low_fee_ban_s: applied when a sender repeatedly submits below-floor txs
    - spam_ban_s: applied for generic DoS-y behaviors (oversize, flood)
    - max_rejects_in_window: if a sender triggers this many rejects in `window_s`,
      they are banned for `spam_ban_s`.
    """
    low_fee_ban_s: int = 30
    spam_ban_s: int = 120
    window_s: int = 10
    max_rejects_in_window: int = 5


@dataclass
class BanState:
    """Internal accounting for bans and rolling reject counters."""
    until_s: float = 0.0
    # simple rolling window counter
    last_reset_s: float = 0.0
    rejects_in_window: int = 0


class BanList:
    """
    In-memory ban list keyed by sender address bytes (or any hashable id).
    """

    def __init__(self, policy: Optional[BanPolicy] = None, *, clock: Clock = _now_monotonic):
        self._policy = policy or BanPolicy()
        self._clock = clock
        self._state: Dict[bytes, BanState] = {}

    def is_banned(self, sender: bytes) -> bool:
        st = self._state.get(sender)
        if not st:
            return False
        return self._clock() < st.until_s

    def ban_for_low_fee(self, sender: bytes) -> None:
        st = self._state.setdefault(sender, BanState())
        st.until_s = max(st.until_s, self._clock() + self._policy.low_fee_ban_s)

    def ban_for_spam(self, sender: bytes) -> None:
        st = self._state.setdefault(sender, BanState())
        st.until_s = max(st.until_s, self._clock() + self._policy.spam_ban_s)

    def record_reject(self, sender: bytes) -> None:
        """Increment sender's reject counter and auto-ban if it exceeds the threshold."""
        now = self._clock()
        st = self._state.setdefault(sender, BanState())
        if (now - st.last_reset_s) > self._policy.window_s:
            st.last_reset_s = now
            st.rejects_in_window = 0
        st.rejects_in_window += 1
        if st.rejects_in_window >= self._policy.max_rejects_in_window:
            self.ban_for_spam(sender)

    def clear(self, sender: bytes) -> None:
        self._state.pop(sender, None)


# -------------------------
# Admission policy
# -------------------------

@dataclass
class AdmissionConfig:
    """
    Admission knobs for mempool ingress.

    - max_tx_size_bytes: hard cap on serialized size
    - accept_below_floor_for_local: bypass floors for trusted/local sources
    - min_effective_fee_override_wei: optional absolute floor; if set, used
      in addition to (not instead of) any FeeWatermark dynamic floor
    - allow_chain_id: optional int to reject mismatched chainIds early
      (stateless fast path; callers may also perform richer validation)
    """
    max_tx_size_bytes: int = 128 * 1024
    accept_below_floor_for_local: bool = True
    min_effective_fee_override_wei: Optional[int] = None
    allow_chain_id: Optional[int] = None


class AdmissionPolicy:
    """
    Stateless admission checker. It may consult a FeeWatermark for
    dynamic fee floors but performs no I/O and holds no mempool refs.

    Expected tx/meta shape (duck-typed):
      tx.sender: bytes
      tx.nonce: int
      tx.chain_id: int (optional but recommended)
      meta.size_bytes: int
      meta.effective_fee_wei: int
    """

    def __init__(
        self,
        cfg: Optional[AdmissionConfig] = None,
        *,
        watermark: Optional[FeeWatermark] = None,
        clock: Clock = _now_monotonic,
    ):
        self.cfg = cfg or AdmissionConfig()
        self._wm = watermark
        self._clock = clock

    def _floor_from_watermark(self, pool_size: int, capacity: int) -> int:
        if self._wm is None:
            return 0
        th = self._wm.thresholds(pool_size=pool_size, capacity=capacity)
        return int(getattr(th, "admit_floor_wei", 0))

    def _effective_fee(self, meta, tx) -> int:
        eff = getattr(meta, "effective_fee_wei", None)
        if eff is None:
            eff = getattr(tx, "effective_fee_wei", None)
        return int(eff or 0)

    def check_admit(
        self,
        *,
        tx,
        meta,
        pool_size: int,
        capacity: int,
        is_local: bool = False,
    ) -> None:
        """
        Raise an AdmissionError subclass if admission should be denied.

        This function is intentionally minimal; heavier checks (signature,
        intrinsic gas, state access) should live in stateless validators.
        """
        # Size
        size = int(getattr(meta, "size_bytes", 0))
        if size <= 0 or size > self.cfg.max_tx_size_bytes:
                raise Oversize(size_bytes=size, max_bytes=self.cfg.max_tx_size_bytes)

        # Chain id (if configured)
        if self.cfg.allow_chain_id is not None:
            tx_chain = getattr(tx, "chain_id", None)
            if tx_chain is not None and int(tx_chain) != int(self.cfg.allow_chain_id):
                raise AdmissionError(f"wrong chainId {tx_chain}, expected {self.cfg.allow_chain_id}")

        # Fee floors
        if not (is_local and self.cfg.accept_below_floor_for_local):
            eff = self._effective_fee(meta, tx)
            dyn_floor = self._floor_from_watermark(pool_size, capacity)
            min_floor = max(int(self.cfg.min_effective_fee_override_wei or 0), dyn_floor)
            if eff < min_floor:
                raise FeeTooLow(offered_gas_price_wei=eff, min_required_wei=min_floor)

        # Passed all admission checks

    # -------------------------
    # Replacement (RBF) policy
    # -------------------------

    @staticmethod
    def _rbf_ratio_from_priority(old_meta, new_meta) -> Optional[float]:
        """
        Ask mempool.priority (if present) for a context-aware bump ratio.
        """
        if _priority is None:
            return None
        try:
            return float(_priority.rbf_min_bump(old_meta, new_meta))  # type: ignore[attr-defined]
        except Exception:
            return None

    def check_replacement(
        self,
        *,
        old_meta,
        new_meta,
        min_bump_ratio: float = 1.10,
    ) -> None:
        """
        Ensure the new tx's *effective fee* is sufficiently higher than the old.

        Args:
            old_meta: metadata of the currently pooled tx
            new_meta: metadata of the candidate replacement
            min_bump_ratio: default ratio (e.g. 1.10 = +10%) used if no
                            custom priority module supplies a value.

        Raises:
            ReplacementError if the bump is insufficient.
        """
        # Allow a custom policy to override the default
        ratio = self._rbf_ratio_from_priority(old_meta, new_meta) or float(min_bump_ratio)
        old_fee = int(getattr(old_meta, "effective_fee_wei", 0))
        new_fee = int(getattr(new_meta, "effective_fee_wei", 0))

        # Require strict > (not >=) to avoid equality churn
        required = int((old_fee * ratio) + 0.9999)
        if new_fee < required:
            raise ReplacementError(
                required_bump=ratio,
                current_effective_gas_price_wei=old_fee,
                offered_effective_gas_price_wei=new_fee,
            )


# -------------------------
# Top-level helpers
# -------------------------

@dataclass
class PolicySuite:
    """
    Convenience bundle used by RPC ingress:
      - admission: AdmissionPolicy
      - bans: BanList
    """
    admission: AdmissionPolicy
    bans: BanList

    @classmethod
    def default(cls, watermark: Optional[FeeWatermark] = None) -> "PolicySuite":
        adm = AdmissionPolicy(watermark=watermark)
        bl = BanList()
        return cls(admission=adm, bans=bl)

    def should_admit(self, tx, meta, *, pool_size: int, capacity: int, is_local: bool = False) -> None:
        """
        Combined check: ban gate + admission. Raises if not allowed.
        """
        sender = getattr(tx, "sender", None)
        if sender is not None and self.bans.is_banned(sender):
            raise DoSError("sender temporarily banned due to prior abusive behavior")
        try:
            self.admission.check_admit(
                tx=tx, meta=meta, pool_size=pool_size, capacity=capacity, is_local=is_local
            )
        except FeeTooLow:
            # Record reject for potential low-fee auto-ban
            if sender:
                self.bans.record_reject(sender)
                self.bans.ban_for_low_fee(sender)
            raise
        except (Oversize, AdmissionError):
            if sender:
                self.bans.record_reject(sender)
            raise

