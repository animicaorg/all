from __future__ import annotations
"""
Flow-control primitives for Animica P2P.

Design
------
We use a receiver-advertised *credit window* per topic, tracked in both
directions:

• ReceiverWindow (consumer side): admits incoming messages iff there are
  available credits (bytes/messages). When the remaining credits cross a
  low-watermark, it emits a *CreditUpdate* to top the window back up.

• SenderWindow (producer side): estimates the peer's available credits.
  It blocks/sheds load when the estimate is low and exposes a
  BackpressureSignal (OK/SOFT/HARD) to the caller. It updates its estimate
  upon receiving a *CreditUpdate* from the peer.

Backpressure is advisory: callers can use the signal to delay or shed work.

Wire
----
This module provides a compact msgpack payload for CREDIT_UPDATE messages.
The outer message-id/envelope is handled by p2p.wire.*; here we only encode
the payload. Integrators typically map this payload under a message id such
as MessageId.CREDIT_UPDATE.

Safety/DoS
----------
• Window sizes are bounded from p2p.constants (with sane fallbacks).
• Per-message and per-batch guards prevent unbounded growth.
• Byte-based accounting dominates; msg-count is a secondary check.

"""

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Dict, Tuple, Optional, Iterable, Callable

import math
import time

import msgspec

# -----------------------------------------------------------------------------
# Constants (prefer p2p.constants, but provide fallbacks)
# -----------------------------------------------------------------------------
try:
    from p2p.constants import (
        DEFAULT_FC_WINDOW_BYTES,
        DEFAULT_FC_WINDOW_MSGS,
        FC_LOW_WATERMARK_FRAC,
        FC_GRANT_CHUNK_FRAC,
        FC_HARD_FRACTION,
        FC_SOFT_FRACTION,
        MAX_CREDIT_GRANT_BYTES,
        MAX_CREDIT_GRANT_MSGS,
        MIN_CREDIT_GRANT_BYTES,
        MIN_CREDIT_GRANT_MSGS,
    )
except Exception:  # pragma: no cover
    DEFAULT_FC_WINDOW_BYTES = 512 * 1024        # 512 KiB
    DEFAULT_FC_WINDOW_MSGS  = 512
    FC_LOW_WATERMARK_FRAC   = 0.25              # when remaining < 25%, send top-up
    FC_GRANT_CHUNK_FRAC     = 0.50              # grant back up to 50% of capacity
    FC_HARD_FRACTION        = 0.05              # remote-available / capacity <= 5% => HARD
    FC_SOFT_FRACTION        = 0.15              # remote-available / capacity <= 15% => SOFT
    MAX_CREDIT_GRANT_BYTES  = 2 * 1024 * 1024   # 2 MiB per update
    MAX_CREDIT_GRANT_MSGS   = 2048
    MIN_CREDIT_GRANT_BYTES  = 8 * 1024          # 8 KiB
    MIN_CREDIT_GRANT_MSGS   = 8

# -----------------------------------------------------------------------------
# Topic identifier (string or small int); we normalize to str on-wire
# -----------------------------------------------------------------------------
Topic = str

# -----------------------------------------------------------------------------
# Backpressure signal
# -----------------------------------------------------------------------------
class BackpressureSignal(IntEnum):
    OK   = 0
    SOFT = 1
    HARD = 2

# Simple helper translating signal to a suggested delay (seconds)
def suggested_delay(signal: BackpressureSignal, rtt_seconds: float) -> float:
    rtt = max(0.0, rtt_seconds)
    if signal == BackpressureSignal.OK:
        return 0.0
    if signal == BackpressureSignal.SOFT:
        return 0.50 * rtt
    return 1.50 * rtt  # HARD

# -----------------------------------------------------------------------------
# Receiver window (authoritative credits we grant to the sender)
# -----------------------------------------------------------------------------
@dataclass
class ReceiverWindow:
    capacity_bytes: int = DEFAULT_FC_WINDOW_BYTES
    capacity_msgs: int  = DEFAULT_FC_WINDOW_MSGS
    low_watermark_frac: float = FC_LOW_WATERMARK_FRAC
    grant_chunk_frac: float   = FC_GRANT_CHUNK_FRAC

    # current remaining credits
    avail_bytes: int = 0
    avail_msgs: int = 0

    def __post_init__(self) -> None:
        self.capacity_bytes = int(max(0, self.capacity_bytes))
        self.capacity_msgs  = int(max(0, self.capacity_msgs))
        self.avail_bytes    = self.capacity_bytes
        self.avail_msgs     = self.capacity_msgs

    def admit(self, msg_bytes: int, msgs: int = 1) -> bool:
        """Check and reserve credits for an incoming message."""
        if msg_bytes < 0 or msgs <= 0:
            return False
        if msg_bytes > self.avail_bytes or msgs > self.avail_msgs:
            return False
        self.avail_bytes -= msg_bytes
        self.avail_msgs  -= msgs
        return True

    def _target_topup(self) -> Tuple[int, int]:
        """How many credits should we grant to top up the window."""
        tgt_b = int(self.capacity_bytes * self.grant_chunk_frac)
        tgt_m = int(self.capacity_msgs  * self.grant_chunk_frac)
        # clamp to min/max grant envelopes
        b = max(MIN_CREDIT_GRANT_BYTES, min(tgt_b, MAX_CREDIT_GRANT_BYTES))
        m = max(MIN_CREDIT_GRANT_MSGS,  min(tgt_m, MAX_CREDIT_GRANT_MSGS))
        return b, m

    def maybe_grant(self) -> Tuple[int, int]:
        """
        If below low-watermark, replenish credits and return (grant_bytes, grant_msgs).
        Otherwise return (0, 0).
        """
        lwm_b = int(self.capacity_bytes * self.low_watermark_frac)
        lwm_m = int(self.capacity_msgs  * self.low_watermark_frac)
        if self.avail_bytes > lwm_b and self.avail_msgs > lwm_m:
            return (0, 0)
        g_b, g_m = self._target_topup()
        # replenish
        self.avail_bytes = min(self.capacity_bytes, self.avail_bytes + g_b)
        self.avail_msgs  = min(self.capacity_msgs,  self.avail_msgs  + g_m)
        return g_b, g_m

    def force_grant_all(self) -> Tuple[int, int]:
        """Aggressively top-up to full capacity (e.g., on new peer)."""
        g_b = max(0, self.capacity_bytes - self.avail_bytes)
        g_m = max(0, self.capacity_msgs  - self.avail_msgs)
        if g_b == 0 and g_m == 0:
            return (0, 0)
        # clamp to single-update maxima
        g_b = min(g_b, MAX_CREDIT_GRANT_BYTES)
        g_m = min(g_m, MAX_CREDIT_GRANT_MSGS)
        self.avail_bytes += g_b
        self.avail_msgs  += g_m
        return g_b, g_m

# -----------------------------------------------------------------------------
# Sender window (our estimate of the remote peer's remaining credits)
# -----------------------------------------------------------------------------
@dataclass
class SenderWindow:
    capacity_bytes: int = DEFAULT_FC_WINDOW_BYTES
    capacity_msgs: int  = DEFAULT_FC_WINDOW_MSGS
    hard_frac: float    = FC_HARD_FRACTION
    soft_frac: float    = FC_SOFT_FRACTION

    est_avail_bytes: int = 0
    est_avail_msgs: int  = 0

    def __post_init__(self) -> None:
        self.capacity_bytes = int(max(0, self.capacity_bytes))
        self.capacity_msgs  = int(max(0, self.capacity_msgs))
        # pessimistic start; caller should call on_credit_update() after HELLO
        self.est_avail_bytes = 0
        self.est_avail_msgs  = 0

    def can_send(self, msg_bytes: int, msgs: int = 1) -> bool:
        return (msg_bytes <= self.est_avail_bytes) and (msgs <= self.est_avail_msgs)

    def note_send(self, msg_bytes: int, msgs: int = 1) -> BackpressureSignal:
        """Reserve credits and compute a backpressure signal for *next* sends."""
        if msg_bytes < 0 or msgs <= 0:
            return BackpressureSignal.HARD
        if not self.can_send(msg_bytes, msgs):
            # pessimistically clamp to zero
            self.est_avail_bytes = max(0, self.est_avail_bytes - msg_bytes)
            self.est_avail_msgs  = max(0, self.est_avail_msgs  - msgs)
            return BackpressureSignal.HARD
        self.est_avail_bytes -= msg_bytes
        self.est_avail_msgs  -= msgs
        return self._signal()

    def on_credit_update(self, grant_bytes: int, grant_msgs: int) -> None:
        self.est_avail_bytes = min(self.capacity_bytes, self.est_avail_bytes + max(0, grant_bytes))
        self.est_avail_msgs  = min(self.capacity_msgs,  self.est_avail_msgs  + max(0, grant_msgs))

    def _signal(self) -> BackpressureSignal:
        # compare remaining to capacity
        frac_b = 1.0 if self.capacity_bytes == 0 else (self.est_avail_bytes / max(1, self.capacity_bytes))
        frac_m = 1.0 if self.capacity_msgs  == 0 else (self.est_avail_msgs  / max(1, self.capacity_msgs))
        frac = min(frac_b, frac_m)
        if frac <= self.hard_frac:
            return BackpressureSignal.HARD
        if frac <= self.soft_frac:
            return BackpressureSignal.SOFT
        return BackpressureSignal.OK

# -----------------------------------------------------------------------------
# FlowController: per-peer collection of windows keyed by topic
# -----------------------------------------------------------------------------
class FlowController:
    """
    Per-peer flow controller maintaining receiver/sender windows per topic.

    Typical usage (receiver side):
        fc = FlowController(rtt_provider=my_rtt_fn)
        ok = fc.admit("blocks", msg_bytes)
        if not ok: drop/NAK
        grant = fc.maybe_grant("blocks")
        if grant: send CreditUpdate(topic, *grant)

    Typical usage (sender side):
        sig = fc.note_send("blocks", msg_bytes)
        if sig != BackpressureSignal.OK:
            sleep(suggested_delay(sig, fc.get_rtt()))

        # when we get a CreditUpdate from peer:
        fc.on_credit_update("blocks", grant_bytes, grant_msgs)
    """
    def __init__(
        self,
        *,
        default_capacity_bytes: int = DEFAULT_FC_WINDOW_BYTES,
        default_capacity_msgs: int  = DEFAULT_FC_WINDOW_MSGS,
        rtt_provider: Optional[Callable[[], float]] = None,
    ) -> None:
        self._rx: Dict[Topic, ReceiverWindow] = {}
        self._tx: Dict[Topic, SenderWindow]   = {}
        self._default_cap_b = int(default_capacity_bytes)
        self._default_cap_m = int(default_capacity_msgs)
        self._rtt_provider = rtt_provider or (lambda: 0.200)  # 200ms default

    # --- helpers
    def get_rtt(self) -> float:
        try:
            r = float(self._rtt_provider())
        except Exception:
            r = 0.200
        return max(0.0, r)

    def _rxw(self, topic: Topic) -> ReceiverWindow:
        if topic not in self._rx:
            self._rx[topic] = ReceiverWindow(
                capacity_bytes=self._default_cap_b,
                capacity_msgs=self._default_cap_m,
            )
        return self._rx[topic]

    def _txw(self, topic: Topic) -> SenderWindow:
        if topic not in self._tx:
            self._tx[topic] = SenderWindow(
                capacity_bytes=self._default_cap_b,
                capacity_msgs=self._default_cap_m,
            )
        return self._tx[topic]

    # --- receiver side API
    def admit(self, topic: Topic, msg_bytes: int, msgs: int = 1) -> bool:
        return self._rxw(topic).admit(msg_bytes, msgs)

    def maybe_grant(self, topic: Topic) -> Tuple[int, int]:
        return self._rxw(topic).maybe_grant()

    def force_grant_all(self, topic: Topic) -> Tuple[int, int]:
        return self._rxw(topic).force_grant_all()

    # --- sender side API
    def note_send(self, topic: Topic, msg_bytes: int, msgs: int = 1) -> BackpressureSignal:
        return self._txw(topic).note_send(msg_bytes, msgs)

    def on_credit_update(self, topic: Topic, grant_bytes: int, grant_msgs: int) -> None:
        self._txw(topic).on_credit_update(grant_bytes, grant_msgs)

    def backpressure_delay(self, topic: Topic) -> float:
        sig = self._txw(topic)._signal()
        return suggested_delay(sig, self.get_rtt())

# -----------------------------------------------------------------------------
# Wire payload (msgpack) for CREDIT_UPDATE
# -----------------------------------------------------------------------------
# Local tag for this payload; outer frame id is assigned by p2p.wire/messages.
TAG_CREDIT_UPDATE = 55

class _CreditUpdateS(msgspec.Struct, omit_defaults=True):
    t: int
    topic: str
    grant_bytes: int
    grant_msgs: int

ENC = msgspec.msgpack.Encoder()
DEC = msgspec.msgpack.Decoder(type=_CreditUpdateS)

def build_credit_update(topic: Topic, grant_bytes: int, grant_msgs: int) -> bytes:
    """
    Encode a CREDIT_UPDATE payload (to be placed into a wire frame).
    """
    gb = int(max(0, min(grant_bytes,  MAX_CREDIT_GRANT_BYTES)))
    gm = int(max(0, min(grant_msgs,   MAX_CREDIT_GRANT_MSGS)))
    return ENC.encode(_CreditUpdateS(t=TAG_CREDIT_UPDATE, topic=str(topic), grant_bytes=gb, grant_msgs=gm))

def parse_credit_update(data: bytes) -> Tuple[Topic, int, int]:
    """
    Parse a CREDIT_UPDATE payload and return (topic, grant_bytes, grant_msgs).
    """
    m = DEC.decode(data)
    if m.t != TAG_CREDIT_UPDATE:
        raise ValueError("CREDIT_UPDATE tag mismatch")
    gb = int(max(0, min(m.grant_bytes, MAX_CREDIT_GRANT_BYTES)))
    gm = int(max(0, min(m.grant_msgs,  MAX_CREDIT_GRANT_MSGS)))
    return m.topic, gb, gm

# -----------------------------------------------------------------------------
# Convenience: receiver path helper to generate update if needed
# -----------------------------------------------------------------------------
def rx_maybe_credit_update(fc: FlowController, topic: Topic) -> Optional[bytes]:
    """
    If the receiver window for `topic` crossed the low watermark,
    return an encoded CREDIT_UPDATE payload; else None.
    """
    gb, gm = fc.maybe_grant(topic)
    if gb or gm:
        return build_credit_update(topic, gb, gm)
    return None

# -----------------------------------------------------------------------------
# Public exports
# -----------------------------------------------------------------------------
__all__ = [
    "BackpressureSignal", "suggested_delay",
    "ReceiverWindow", "SenderWindow", "FlowController",
    "build_credit_update", "parse_credit_update", "rx_maybe_credit_update",
    "TAG_CREDIT_UPDATE",
]
