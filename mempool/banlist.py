"""
mempool.banlist
----------------

Lightweight, in-memory ban list used by the mempool ingress path.

A "ban" here is an admission short-circuit: while active, all new txs from the
sender should be rejected before doing any heavy work. Higher-level code is
responsible for actually turning these into DoSError / RPC errors.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional


Clock = Callable[[], float]


def _now_monotonic() -> float:
    """Default clock for BanList: monotonic seconds."""
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
    - window_s: length of the rolling window for reject counting
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

    This is intentionally small and stateless-with-respect-to-chain. It is
    designed to live alongside `AdmissionPolicy` in the RPC ingress layer.
    """

    def __init__(
        self,
        policy: Optional[BanPolicy] = None,
        *,
        clock: Clock = _now_monotonic,
    ) -> None:
        self._policy = policy or BanPolicy()
        self._clock = clock
        self._state: Dict[bytes, BanState] = {}

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def is_banned(self, sender: bytes) -> bool:
        """
        Return True if `sender` is currently banned.

        The ban is considered active while `clock() < until_s`.
        """
        st = self._state.get(sender)
        if not st:
            return False
        return self._clock() < st.until_s

    # ------------------------------------------------------------------
    # Ban operations
    # ------------------------------------------------------------------

    def ban_for_low_fee(self, sender: bytes) -> None:
        """
        Apply a low-fee ban to `sender`.

        This is typically called when the admission layer detects repeated
        below-floor submissions from a sender. Each invocation extends the
        ban horizon to at least `now + low_fee_ban_s`.
        """
        st = self._state.setdefault(sender, BanState())
        st.until_s = max(st.until_s, self._clock() + self._policy.low_fee_ban_s)

    def ban_for_spam(self, sender: bytes) -> None:
        """
        Apply a generic "spam" or DoS ban to `sender`.

        Used for oversize bursts, malformed floods, or too many rejects in a
        short window. Each invocation extends the ban horizon to at least
        `now + spam_ban_s`.
        """
        st = self._state.setdefault(sender, BanState())
        st.until_s = max(st.until_s, self._clock() + self._policy.spam_ban_s)

    # ------------------------------------------------------------------
    # Rolling reject accounting
    # ------------------------------------------------------------------

    def record_reject(self, sender: bytes) -> None:
        """
        Record a rejected transaction for `sender` and auto-ban if it crosses
        the configured threshold within the rolling window.

        Semantics (matching the Animica spec):

          - We maintain a simple counter per sender with a "window start"
            timestamp.
          - If `now - last_reset_s > window_s`, we reset the window and
            counter.
          - After incrementing, if `rejects_in_window >= max_rejects_in_window`,
            we escalate to `ban_for_spam(sender)`.
        """
        now = self._clock()
        st = self._state.setdefault(sender, BanState())

        # reset rolling window if we've moved past it
        if (now - st.last_reset_s) > self._policy.window_s:
            st.last_reset_s = now
            st.rejects_in_window = 0

        st.rejects_in_window += 1

        if st.rejects_in_window >= self._policy.max_rejects_in_window:
            self.ban_for_spam(sender)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear(self, sender: bytes) -> None:
        """Remove any ban / state associated with `sender` (if present)."""
        self._state.pop(sender, None)
