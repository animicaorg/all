from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional, Tuple

# --- Wire IDs (best effort) ---------------------------------------------------------------------

try:
    from p2p.wire.message_ids import MSG_PING, MSG_PONG  # type: ignore
except Exception:  # pragma: no cover
    MSG_PING = 0x03
    MSG_PONG = 0x04

try:
    from p2p.wire.encoding import decode, encode  # type: ignore
except Exception:  # pragma: no cover
    encode = decode = None  # type: ignore

# --- Helpers ------------------------------------------------------------------------------------


def _mono_ms() -> float:
    return time.monotonic() * 1000.0


@dataclass
class PingStats:
    # TCP-style smoothed RTT estimator (RFC 6298)
    srtt_ms: Optional[float] = None
    rttvar_ms: Optional[float] = None
    last_rtt_ms: Optional[float] = None

    # Windowed stats
    window: Deque[float] = field(default_factory=lambda: deque(maxlen=64))
    min_ms: Optional[float] = None
    max_ms: Optional[float] = None

    # Loss & meta
    sent: int = 0
    ok: int = 0
    lost: int = 0
    last_ok_at: Optional[float] = None  # monotonic seconds

    def observe(self, rtt_ms: float) -> None:
        self.last_rtt_ms = rtt_ms
        self.ok += 1
        self.sent += 1
        self.last_ok_at = time.monotonic()
        self.window.append(rtt_ms)
        self.min_ms = rtt_ms if self.min_ms is None else min(self.min_ms, rtt_ms)
        self.max_ms = rtt_ms if self.max_ms is None else max(self.max_ms, rtt_ms)

        # RFC 6298 update:
        if self.srtt_ms is None:
            # First sample initializes both SRTT and RTTVAR
            self.srtt_ms = rtt_ms
            self.rttvar_ms = rtt_ms / 2.0
        else:
            # alpha=1/8, beta=1/4
            err = abs(self.srtt_ms - rtt_ms)
            self.rttvar_ms = (1 - 0.25) * self.rttvar_ms + 0.25 * err  # type: ignore[operator]
            self.srtt_ms = (1 - 0.125) * self.srtt_ms + 0.125 * rtt_ms  # type: ignore[operator]

    def mark_loss(self) -> None:
        self.lost += 1
        self.sent += 1

    @property
    def loss_rate(self) -> float:
        if self.sent <= 0:
            return 0.0
        return self.lost / float(self.sent)

    @property
    def rto_ms(self) -> Optional[float]:
        """Recommended Retransmission Timeout ~= SRTT + 4*RTTVAR"""
        if self.srtt_ms is None or self.rttvar_ms is None:
            return None
        return self.srtt_ms + 4.0 * self.rttvar_ms

    def snapshot(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        if self.last_rtt_ms is not None:
            out["last_ms"] = self.last_rtt_ms
        if self.srtt_ms is not None:
            out["srtt_ms"] = self.srtt_ms
        if self.rttvar_ms is not None:
            out["rttvar_ms"] = self.rttvar_ms
        if self.min_ms is not None:
            out["min_ms"] = self.min_ms
        if self.max_ms is not None:
            out["max_ms"] = self.max_ms
        out["loss_rate"] = self.loss_rate
        out["sent"] = float(self.sent)
        out["ok"] = float(self.ok)
        out["lost"] = float(self.lost)
        return out


class PingError(Exception):
    pass


class PingService:
    """
    Transport-agnostic ping/pong with moving RTT estimator.

    It supports (in order of preference):
      1) conn.ping() → returns RTT ms (or dict with rtt_ms)
      2) conn.request("PING", payload) → expects {"ok":true,"t":..} or {"rtt_ms":..}
      3) Wire frames: MSG_PING / MSG_PONG via conn.send_frame/recv_frame with p2p.wire.encoding
      4) Line JSON: write {"op":"PING","seq":N,"t_ms":...}\\n → read {"op":"PONG","seq":N}

    The class can be used either one-shot via `ping_once()` or as a background task via start()/stop().
    """

    def __init__(
        self,
        conn: Any,
        *,
        interval: float = 20.0,
        timeout: float = 5.0,
        jitter: float = 0.2,
    ) -> None:
        self._conn = conn
        self._interval = float(interval)
        self._timeout = float(timeout)
        self._jitter = float(jitter)
        self._seq = random.randrange(0, 2**31)
        self._stats = PingStats()
        self._task: Optional[asyncio.Task] = None
        self._closed = False

    # --- public API ------------------------------------------------------------------------------

    @property
    def stats(self) -> PingStats:
        return self._stats

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._closed = False
        self._task = asyncio.create_task(self._loop(), name="p2p_ping_loop")

    async def stop(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

    async def ping_once(self) -> float:
        """Send one ping and return RTT in milliseconds."""
        rtt = await asyncio.wait_for(self._send_and_wait(), timeout=self._timeout)
        self._stats.observe(rtt)
        return rtt

    # --- internals -------------------------------------------------------------------------------

    async def _loop(self) -> None:
        # Jittered interval
        try:
            while not self._closed:
                try:
                    await self.ping_once()
                except asyncio.TimeoutError:
                    self._stats.mark_loss()
                except Exception:
                    self._stats.mark_loss()
                # sleep with +/- jitter
                base = self._interval
                delta = base * self._jitter
                wait_s = max(1e-3, random.uniform(base - delta, base + delta))
                await asyncio.sleep(wait_s)
        except asyncio.CancelledError:
            pass

    async def _send_and_wait(self) -> float:
        # 1) conn.ping()
        if hasattr(self._conn, "ping") and callable(getattr(self._conn, "ping")):
            t0 = _mono_ms()
            resp = await self._conn.ping()
            t1 = _mono_ms()
            # honor explicit rtt if provided
            if isinstance(resp, dict) and "rtt_ms" in resp:
                return float(resp["rtt_ms"])
            return t1 - t0

        # 2) conn.request("PING", ...)
        if hasattr(self._conn, "request") and callable(getattr(self._conn, "request")):
            seq = self._next_seq()
            payload = {"seq": seq, "t_ms": _mono_ms()}
            t0 = _mono_ms()
            try:
                resp = await self._conn.request("PING", payload, timeout=self._timeout)
            except TypeError:
                # some request() may not accept timeout kw
                resp = await self._conn.request("PING", payload)
            t1 = _mono_ms()
            if isinstance(resp, dict):
                if "rtt_ms" in resp:
                    return float(resp["rtt_ms"])
            return t1 - t0

        # 3) Wire frames with encode/decode and send_frame/recv_frame
        if (
            encode
            and hasattr(self._conn, "send_frame")
            and hasattr(self._conn, "recv_frame")
        ):
            seq = self._next_seq()
            body = encode({"seq": seq, "t_ms": _mono_ms()})  # type: ignore
            await self._conn.send_frame(MSG_PING, body)
            t0 = _mono_ms()
            # We will wait until we see PONG for our seq, skipping unrelated frames.
            end = time.monotonic() + self._timeout
            while time.monotonic() < end:
                remaining = max(0.0, end - time.monotonic())
                msg_id, payload = await asyncio.wait_for(
                    self._conn.recv_frame(), timeout=remaining
                )
                if msg_id != MSG_PONG:
                    # Not ours — return it back if the conn has a router/putback hook.
                    putback = getattr(self._conn, "putback_frame", None)
                    if callable(putback):
                        try:
                            putback(msg_id, payload)
                        except Exception:
                            pass
                    continue
                try:
                    obj = decode(payload)  # type: ignore
                    if isinstance(obj, dict) and obj.get("seq") == seq:
                        return _mono_ms() - t0
                except Exception:
                    # Malformed pong; ignore and continue until timeout
                    pass
            raise asyncio.TimeoutError("pong not received in time")

        # 4) JSON line fallback (StreamReader/Writer-like conn)
        if hasattr(self._conn, "write") and hasattr(self._conn, "readuntil"):
            seq = self._next_seq()
            t0 = _mono_ms()
            line = ('{"op":"PING","seq":%d,"t_ms":%.3f}\n' % (seq, t0)).encode("utf-8")
            self._conn.write(line)  # type: ignore[attr-defined]
            await self._conn.drain()  # type: ignore[attr-defined]
            end = time.monotonic() + self._timeout
            while time.monotonic() < end:
                remaining = max(0.001, end - time.monotonic())
                raw = await asyncio.wait_for(self._conn.readuntil(b"\n"), timeout=remaining)  # type: ignore[attr-defined]
                try:
                    # very small hand-rolled parser to avoid importing json in hot path
                    s = raw.decode("utf-8", "strict")
                    if '"op":"PONG"' not in s:
                        continue
                    # check seq equality
                    if f'"seq":{seq}' in s:
                        return _mono_ms() - t0
                except Exception:
                    continue
            raise asyncio.TimeoutError("pong not received in time (line mode)")

        # No supported mode
        raise PingError("connection does not support ping")

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0x7FFFFFFF
        return self._seq


# --- Server-side helpers ------------------------------------------------------------------------


async def handle_ping_request(req: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a PONG response for a parsed request dict. Safe to use in routers.
    """
    seq = req.get("seq")
    try:
        seq = int(seq)
    except Exception:
        seq = None
    # echo the sequence; let the transport measure RTT if needed
    return {"ok": True, "seq": seq, "t_ms": _mono_ms()}


async def respond_to_frames(conn: Any, msg_id: int, payload: bytes) -> bool:
    """
    Optional helper for routers using the canonical wire path:
      If msg_id == MSG_PING, reply with MSG_PONG (same 'seq') and return True.
      Otherwise return False and do nothing.

    This keeps ping handling O(1) and minimizes coupling with the main router.
    """
    if msg_id != MSG_PING or not decode:
        return False
    try:
        req = decode(payload)  # type: ignore
        if not isinstance(req, dict):
            return False
        seq = req.get("seq")
        pong = encode({"seq": seq, "t_ms": _mono_ms()})  # type: ignore
        await conn.send_frame(MSG_PONG, pong)
        return True
    except Exception:
        return False
