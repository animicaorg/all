"""
Two-sided, capacity-capped bandwidth accounting for the dVPN.

Both the client and the exit independently sample WireGuard's cumulative per-peer byte
counters and POST ML-DSA-65-signed usage reports. The marketplace reconciles them:
reward accrues on ``min(clientDelta, exitDelta)`` per interval, each delta capped by
measured link capacity × wall-clock, so neither side can inflate. Counters are monotonic
per session; a decrease means the peer/session was re-created — treat the new value as a
fresh baseline (delta 0 for that step).

This module only BUILDS and SIGNS reports and computes local deltas; the authoritative
reconciliation + IOU accrual lives in the marketplace (lib/vpn.ts). Nothing here credits
a spendable balance — rewards are deferred IOUs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .crypto import Wallet


@dataclass
class Sample:
    seq: int
    cum_rx: int
    cum_tx: int
    ts: int          # unix seconds (caller supplies; module never calls time itself)


@dataclass
class UsageMeter:
    """Tracks cumulative counters for one session and emits monotonic deltas."""
    session_id: str
    side: str                      # "client" | "exit"
    _seq: int = 0
    _last_rx: int = 0
    _last_tx: int = 0
    _cum_rx: int = 0               # session-cumulative (survives counter resets)
    _cum_tx: int = 0
    history: list[Sample] = field(default_factory=list)

    def observe(self, rx: int, tx: int, ts: int) -> Sample:
        # Monotonic per session; a decrease = interface/peer re-created -> rebase, no negative delta.
        d_rx = rx - self._last_rx
        d_tx = tx - self._last_tx
        if d_rx < 0 or d_tx < 0:
            d_rx = max(d_rx, 0)
            d_tx = max(d_tx, 0)
        self._cum_rx += d_rx
        self._cum_tx += d_tx
        self._last_rx, self._last_tx = rx, tx
        self._seq += 1
        s = Sample(seq=self._seq, cum_rx=self._cum_rx, cum_tx=self._cum_tx, ts=ts)
        self.history.append(s)
        return s

    def report_payload(self, sample: Sample) -> dict:
        return {
            "sessionId": self.session_id,
            "side": self.side,
            "seq": sample.seq,
            "cumRx": sample.cum_rx,
            "cumTx": sample.cum_tx,
            "ts": sample.ts,
        }

    def signed_report(self, wallet: Wallet, sample: Sample) -> dict:
        payload = self.report_payload(sample)
        # Sign over a canonical, order-stable string so the marketplace can re-derive it.
        msg = canonical_report_message(payload)
        sig = wallet.sign_login_bytes(msg)  # reuse the same domain-separated signer
        return {
            **payload,
            "address": wallet.address,
            "publicKey": wallet.public_key_hex,
            "sig": sig.hex(),
            "reportMsg": msg,
        }


def canonical_report_message(payload: dict) -> str:
    """Deterministic string the marketplace verifies the signature against."""
    return "usage:" + "|".join(str(payload[k]) for k in ("sessionId", "side", "seq", "cumRx", "cumTx", "ts"))


def reconcile(client_delta: int, exit_delta: int, capacity_bytes: int) -> tuple[int, bool]:
    """Return (reconciled_bytes, flagged). Mirrors lib/vpn.ts:
       reward = min(client, exit), each capped at capacity, flagged if they diverge >10%."""
    c = max(0, min(client_delta, capacity_bytes))
    e = max(0, min(exit_delta, capacity_bytes))
    reconciled = min(c, e)
    hi = max(c, e)
    flagged = hi > 0 and abs(c - e) / hi > 0.10
    return reconciled, flagged
