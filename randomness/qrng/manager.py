"""
randomness.qrng.manager
======================

A self-healing entropy-source manager that makes the quantum lane *always work*:
it serves a **pseudo-quantum** source when no real provider is present, and
automatically **flips to a real quantum/QRNG provider the moment one connects** —
whether that's a hardware device appearing (e.g. /dev/qrandom0 from an IDQ Quantis
driver) or a provider registered at runtime (network QRNG appliance, SDK).

Priority: real quantum hardware (is_quantum & is_hardware) > real hardware >
pseudo-quantum. Flips are recorded with timestamps so operators/RPC can see the
transition. ``refresh()`` is cheap and idempotent — call it on a loop (the QUW
worker does, per round) or use ``start_watch()`` for a background poller.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import register_backend
from . import providers as _p
from .pseudo import PseudoQuantumSource


def _rank(info) -> tuple:
    """Higher is better: real quantum hw > real hw > anything else."""
    return (1 if (info.is_hardware and info.is_quantum) else 0,
            1 if info.is_hardware else 0,
            1 if info.attested else 0)


class EntropySourceManager:
    def __init__(
        self,
        *,
        pseudo_theta: float = 0.0,
        health_gated: bool = True,
        min_entropy_per_byte: Optional[float] = None,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self._pseudo = PseudoQuantumSource(bias_theta=pseudo_theta)
        self._gated = bool(health_gated)
        self._min_h = min_entropy_per_byte
        self._now = now_fn or time.time
        self._registered: List[Any] = []      # runtime-connected real providers
        self._active = self._pseudo
        self._mode = "pseudo"
        self._flips: List[Dict[str, Any]] = []
        self._on_flip: List[Callable[[Dict[str, Any]], None]] = []
        self._watch_stop: Optional[threading.Event] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.refresh()

    # --- discovery ---
    def _discover_real(self):
        candidates = []
        for s in self._registered:
            try:
                if s.info().is_hardware:
                    candidates.append(s)
            except Exception:
                continue
        for s in _p.detect_sources():
            try:
                if s.info().is_hardware:
                    candidates.append(s)
            except Exception:
                continue
        if not candidates:
            return None
        return max(candidates, key=lambda s: _rank(s.info()))

    # --- flip logic ---
    def refresh(self) -> Dict[str, Any]:
        with self._lock:
            real = self._discover_real()
            if real is not None:
                new_active, new_mode = real, "hardware"
            else:
                new_active, new_mode = self._pseudo, "pseudo"

            prev_name = self._active.info().name if self._active else None
            new_name = new_active.info().name
            if new_mode != self._mode or new_name != prev_name:
                flip = {
                    "at": int(self._now()),
                    "from_mode": self._mode, "to_mode": new_mode,
                    "from_source": prev_name, "to_source": new_name,
                    "attested": bool(new_active.info().attested),
                    "reason": ("real provider connected" if new_mode == "hardware"
                               else "no real provider; serving pseudo-quantum"),
                }
                self._flips.append(flip)
                self._active, self._mode = new_active, new_mode
                for cb in list(self._on_flip):
                    try:
                        cb(flip)
                    except Exception:
                        pass
            return self.status()

    # --- runtime connect of a real provider ---
    def connect_provider(self, source, *, name: Optional[str] = None,
                         register: bool = True) -> Dict[str, Any]:
        """Connect a real provider at runtime (network QRNG, SDK, …) and flip to it."""
        with self._lock:
            self._registered.append(source)
            if register:
                try:
                    register_backend(name or source.info().name, source)
                except Exception:
                    pass
        return self.refresh()

    def disconnect_all(self) -> Dict[str, Any]:
        with self._lock:
            self._registered.clear()
        return self.refresh()

    # --- active source ---
    def current(self):
        with self._lock:
            src = self._active
        if self._gated:
            kwargs = {} if self._min_h is None else {"min_entropy_per_byte": self._min_h}
            return _p.HealthGatedSource(src, **kwargs)
        return src

    def active_raw(self):
        """The current active source WITHOUT the health-gate wrapper (for workers
        that run their own per-batch health retry loop)."""
        with self._lock:
            return self._active

    @property
    def mode(self) -> str:
        return self._mode

    def on_flip(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        self._on_flip.append(cb)

    # --- background watcher (optional) ---
    def start_watch(self, interval_s: float = 5.0) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_stop = threading.Event()

        def _loop():
            while not self._watch_stop.is_set():
                try:
                    self.refresh()
                except Exception:
                    pass
                self._watch_stop.wait(interval_s)

        self._watch_thread = threading.Thread(target=_loop, name="qrng-source-watch", daemon=True)
        self._watch_thread.start()

    def stop_watch(self) -> None:
        if self._watch_stop:
            self._watch_stop.set()

    # --- status ---
    def status(self) -> Dict[str, Any]:
        info = self._active.info()
        return {
            "mode": self._mode,                       # "pseudo" | "hardware"
            "pseudo": self._mode == "pseudo",
            "real_available": self._mode == "hardware",
            "attested": bool(info.attested),
            "active_source": info.as_dict(),
            "registered_providers": [s.info().name for s in self._registered],
            "flips": list(self._flips),
        }


_MANAGER: Optional[EntropySourceManager] = None


def get_manager() -> EntropySourceManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = EntropySourceManager()
    return _MANAGER
