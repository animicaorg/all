"""
randomness.beacon_api.driver
============================

Produces a continuous, hash-chained stream of verifiable beacon rounds.

Each round:
  entropy   = best available source (pseudo-quantum, or real attested QRNG)
  aggregate = aggregate_entropy([(entropy, attested, "beacon")])  -> commitment
  value     = H(BEACON_DOMAIN || round_be || prev_value || aggregate.value)

So `value` is recomputable from (round, prev, aggregate_commitment) — the page's
in-browser verifier checks exactly that, plus that prev == the previous round's
value (the hash chain). Draws made off `value` are independently verifiable too.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from randomness.qrng import health
from randomness.qrng.aggregate import aggregate_entropy
from randomness.qrng.manager import get_manager
from randomness.qrng.public import build_quantum_beacon


class BeaconDriver:
    def __init__(
        self,
        *,
        interval_s: float = 30.0,
        n_bytes: int = 4096,
        history: int = 2000,
        min_entropy_per_byte: float = health.DEFAULT_MIN_ENTROPY_PER_BYTE,
        now_fn=None,
    ) -> None:
        self.interval = float(interval_s)
        self.n = int(n_bytes)
        self.maxh = int(history)
        self.min_h = float(min_entropy_per_byte)
        self._now = now_fn or time.time
        self._mgr = get_manager()
        self._rounds: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
        self._round = -1
        self._prev = b""
        self._lock = threading.RLock()
        self._stop: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._produce()  # round 0 immediately so the API has data on boot

    # --- production ---
    def _read_healthy(self):
        self._mgr.refresh()
        src = self._mgr.active_raw()
        data = b""
        rep = None
        for _ in range(8):
            data = src.random_bytes(self.n)
            rep = health.evaluate(data, min_entropy_per_byte=self.min_h)
            if rep.passed:
                break
        info = src.info()
        attested = bool(info.attested and info.is_hardware)
        return data, attested, info.name, (rep.min_entropy_per_byte if rep else 0.0)

    def _produce(self) -> Dict[str, Any]:
        with self._lock:
            data, attested, src_name, h_per_byte = self._read_healthy()
            agg = aggregate_entropy([(data, attested, "beacon")])
            rnd = self._round + 1
            bc = build_quantum_beacon(rnd, self._prev, agg)
            now = int(self._now())
            rec = {
                "round": rnd,
                "value": bc.value_hex,
                "prev": bc.prev_hex,
                "aggregate_commitment": agg.commitment,
                "randomness": bc.value_hex,  # drand-compatible alias
                "time": now,
                "next_round_time": int(now + self.interval),
                "interval": int(self.interval),
                "mode": self._mgr.mode,            # "pseudo" | "hardware"
                "attested": bool(bc.attested),
                "source": src_name,
                "min_entropy_per_byte": round(h_per_byte, 4),
            }
            self._rounds[rnd] = rec
            while len(self._rounds) > self.maxh:
                self._rounds.popitem(last=False)
            self._round = rnd
            self._prev = bc.value()
            return rec

    # --- queries ---
    def latest(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._rounds[self._round])

    def get(self, round_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            rec = self._rounds.get(int(round_id))
            return dict(rec) if rec else None

    def chain(self, start: Optional[int] = None, end: Optional[int] = None,
              limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            hi = self._round if end is None else min(int(end), self._round)
            lo = max(0, hi - limit + 1) if start is None else max(0, int(start))
            return [dict(self._rounds[r]) for r in range(lo, hi + 1) if r in self._rounds]

    def info(self) -> Dict[str, Any]:
        st = self._mgr.status()
        return {
            "name": "Animica Quantum Randomness Beacon",
            "interval_seconds": int(self.interval),
            "entropy_bytes_per_round": self.n,
            "min_entropy_per_byte_gate": self.min_h,
            "current_round": self._round,
            "mode": st["mode"],
            "real_provider": st["real_available"],
            "attested": st["attested"],
            "domains": {
                "beacon": "animica/qrng/beacon/v1",
                "public": "animica/qrng/public/v1",
                "aggregate": "animica/qrng/aggregate/v1",
            },
            "hash": "sha3-256 (FIPS 202)",
            "verify": "value = sha3_256(b'animica/qrng/beacon/v1' || round_be8 || prev || aggregate_commitment)",
        }

    # --- background loop ---
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = threading.Event()

        def _loop():
            while not self._stop.is_set():
                # sleep first: round 0 was produced in __init__
                if self._stop.wait(self.interval):
                    break
                try:
                    self._produce()
                except Exception:
                    pass

        self._thread = threading.Thread(target=_loop, name="quantum-beacon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._stop:
            self._stop.set()
