"""Unified runner - drives the full Animica "mine + AI" flow from the GUI.

This is the backend that lets the GUI miner *do the mining* in the broad sense:
not PoW-only, but the whole unified pipeline (SHA3 proof-of-work + AICF
inference, ENA useful-work, and, on capable hardware, training / serving /
Bittensor) — everything bound to a single ANM payout address.

It is a thin wrapper around :mod:`animica.unified`; it does **not** duplicate
that module's capability-detection or plan-building logic. It only:

* resolves / auto-creates the ANM payout address (the same wallet flow),
* builds the deterministic launch plan for the detected capabilities,
* runs the :class:`animica.unified.Supervisor` in a background thread,
* exposes lightweight start/stop/status for the Qt layer to poll.

IMPORTANT: this module must import (and unit-test) **without** PySide6/Qt, so it
contains no Qt imports. The GUI consumes it through plain callbacks + polling.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# animica.unified is import-light (stdlib only) and is the single source of
# truth for capability detection + the launch plan. We reuse it directly.
from animica import unified
from animica.unified import (
    Capabilities,
    Component,
    Supervisor,
    UnifiedConfig,
    UNIFIED_VERSION,
    build_plan,
    detect_capabilities,
    plan_summary,
    resolve_address,
)

logger = logging.getLogger(__name__)


class UnifiedStatus(str, Enum):
    """Lifecycle status of the unified runner."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


# Type for a status-change callback (status_value, reason).
StatusCallback = Callable[[str, str], None]
# Type for a per-line log callback (component_name, line).
LogCallback = Callable[[str, str], None]


def make_unified_config(cfg: Dict[str, Any]) -> UnifiedConfig:
    """Build a :class:`animica.unified.UnifiedConfig` from a plain GUI dict.

    Accepts the fields the GUI cares about; everything else falls back to the
    ``UnifiedConfig`` defaults (pool.animica.org:3333, the one global model).

    Recognised keys: ``address``, ``pool_host``, ``pool_port``, ``pool_id``,
    ``worker_id``, ``threads``, ``serve_port``, ``run_node``.
    """
    address = str(cfg.get("address") or "").strip()

    def _int(key: str, default: int) -> int:
        try:
            return int(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    kwargs: Dict[str, Any] = {"address": address}
    if cfg.get("pool_host"):
        kwargs["pool_host"] = str(cfg["pool_host"]).strip()
    if cfg.get("pool_port") is not None:
        kwargs["pool_port"] = _int("pool_port", 3333)
    if cfg.get("pool_id"):
        kwargs["pool_id"] = str(cfg["pool_id"]).strip()
    if cfg.get("worker_id"):
        kwargs["worker_id"] = str(cfg["worker_id"]).strip()
    if cfg.get("serve_port") is not None:
        kwargs["serve_port"] = _int("serve_port", 8799)
    # threads: 0 means "miner default" in unified.
    kwargs["threads"] = _int("threads", 0)
    kwargs["run_node"] = bool(cfg.get("run_node", False))
    return UnifiedConfig(**kwargs)


def plan_preview(cfg: Dict[str, Any],
                 caps: Optional[Capabilities] = None) -> Dict[str, Any]:
    """Return the unified plan summary for ``cfg`` without launching anything.

    Pure: calls :func:`animica.unified.build_plan` + :func:`plan_summary`. The
    GUI uses this to show *which components will run* (mine + AI) for the
    detected hardware before the user commits. Pass ``caps`` to preview a
    specific capability tier (handy for tests); otherwise capabilities are
    auto-detected.

    The returned dict matches ``unified.plan_summary``: ``version``, ``address``,
    ``pool_host``, ``pool_id``, ``capabilities``, ``will_run``,
    ``enabled_but_pending`` and the full per-``components`` breakdown.
    """
    if caps is None:
        caps = detect_capabilities()
    ucfg = make_unified_config(cfg)
    plan = build_plan(caps, ucfg)
    return plan_summary(caps, ucfg, plan)


@dataclass
class UnifiedRunnerStats:
    """Snapshot of the unified runner state for the UI."""
    status: str
    address: str
    version: str
    will_run: List[str]
    enabled_but_pending: List[str]
    uptime_seconds: float
    components: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "address": self.address,
            "version": self.version,
            "will_run": self.will_run,
            "enabled_but_pending": self.enabled_but_pending,
            "uptime_seconds": self.uptime_seconds,
            "components": self.components,
        }


class UnifiedRunner:
    """Drives the unified mine+AI flow via ``animica.unified.Supervisor``.

    The supervisor runs in a daemon background thread (it blocks while
    monitoring/restarting its subprocesses), so ``start()`` returns promptly and
    the Qt event loop stays responsive. Call :meth:`status` from a Qt timer to
    poll; register a callback with :meth:`add_status_callback` to be notified on
    status changes.
    """

    def __init__(self) -> None:
        self.status = UnifiedStatus.STOPPED
        self._lock = threading.RLock()
        self._supervisor: Optional[Supervisor] = None
        self._thread: Optional[threading.Thread] = None
        self._start_time = 0.0
        self._error: str = ""
        self._summary: Dict[str, Any] = {}
        self._status_callbacks: List[StatusCallback] = []

    # -- callbacks ---------------------------------------------------------

    def add_status_callback(self, cb: StatusCallback) -> None:
        with self._lock:
            self._status_callbacks.append(cb)

    def remove_status_callback(self, cb: StatusCallback) -> None:
        with self._lock:
            if cb in self._status_callbacks:
                self._status_callbacks.remove(cb)

    def _change_status(self, new: UnifiedStatus, reason: str = "") -> None:
        with self._lock:
            old = self.status
            self.status = new
            callbacks = list(self._status_callbacks)
        logger.info("Unified runner status: %s -> %s%s", old.value, new.value,
                    f" ({reason})" if reason else "")
        for cb in callbacks:
            try:
                cb(new.value, reason)
            except Exception:  # noqa: BLE001 - never let a UI callback crash us
                logger.exception("Error in unified status callback")

    # -- lifecycle ---------------------------------------------------------

    def resolve_payout_address(self, address: str = "", *,
                               create: bool = True) -> str:
        """Resolve (and, if needed, auto-create) the ANM payout address.

        Delegates to :func:`animica.unified.resolve_address`, which checks the
        explicit value, env vars, the default wallet, then optionally creates a
        new wallet — the same flow as ``animica up``. Everything the runner
        launches binds to this one address.
        """
        addr, source = resolve_address(address or None, create=create)
        logger.info("Resolved payout address (source=%s)", source)
        return addr

    def start(self, cfg: Dict[str, Any]) -> bool:
        """Start the unified flow for ``cfg``.

        ``cfg`` is a plain dict (see :func:`make_unified_config`). If no address
        is supplied it is resolved / auto-created via the wallet flow so the GUI
        is zero-config like ``animica up``. Returns True if the supervisor
        thread was launched.
        """
        with self._lock:
            if self.status not in (UnifiedStatus.STOPPED, UnifiedStatus.ERROR):
                logger.warning("Cannot start unified runner: status is %s",
                               self.status.value)
                return False
            self._error = ""

        self._change_status(UnifiedStatus.STARTING)

        try:
            address = str(cfg.get("address") or "").strip()
            if not address:
                address = self.resolve_payout_address("", create=True)
            else:
                # Normalise via resolver too (covers env/wallet fallthrough and
                # validates that *something* is bound) but keep the explicit one.
                address = self.resolve_payout_address(address, create=True)

            run_cfg = dict(cfg)
            run_cfg["address"] = address
            ucfg = make_unified_config(run_cfg)

            caps = detect_capabilities()
            plan = build_plan(caps, ucfg)
            summary = plan_summary(caps, ucfg, plan)

            with self._lock:
                self._summary = summary

            supervisor = Supervisor(plan)
            with self._lock:
                self._supervisor = supervisor
                self._start_time = time.time()

            self._thread = threading.Thread(
                target=self._run_supervisor,
                args=(supervisor,),
                daemon=True,
                name="UnifiedRunner",
            )
            self._thread.start()

            # Brief settle so we surface immediate failures as ERROR.
            time.sleep(0.2)
            with self._lock:
                if self.status == UnifiedStatus.STARTING and not self._error:
                    self._change_status(UnifiedStatus.RUNNING)
            return True

        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to start unified runner")
            with self._lock:
                self._error = str(exc)
            self._change_status(UnifiedStatus.ERROR, str(exc))
            return False

    def _run_supervisor(self, supervisor: Supervisor) -> None:
        """Body of the background thread: runs the supervisor (blocking)."""
        logger.info("Unified supervisor thread started")
        try:
            supervisor.run()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unified supervisor crashed")
            with self._lock:
                self._error = str(exc)
            self._change_status(UnifiedStatus.ERROR, str(exc))
        finally:
            logger.info("Unified supervisor thread finished")
            with self._lock:
                still_supervisor = self._supervisor is supervisor
            # If we exited the run loop without an explicit stop(), reflect it.
            if still_supervisor and self.status not in (
                    UnifiedStatus.STOPPING, UnifiedStatus.STOPPED,
                    UnifiedStatus.ERROR):
                self._change_status(UnifiedStatus.STOPPED, "supervisor exited")

    def stop(self, timeout: float = 15.0) -> bool:
        """Stop the unified flow and all its components."""
        with self._lock:
            if self.status == UnifiedStatus.STOPPED:
                return True
            supervisor = self._supervisor
            thread = self._thread
        self._change_status(UnifiedStatus.STOPPING)

        if supervisor is not None:
            try:
                supervisor.stop()
            except Exception:  # noqa: BLE001
                logger.exception("Error stopping unified supervisor")

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("Unified supervisor thread did not finish in time")

        with self._lock:
            self._supervisor = None
            self._thread = None
        self._change_status(UnifiedStatus.STOPPED)
        return True

    # -- introspection -----------------------------------------------------

    def is_running(self) -> bool:
        with self._lock:
            return self.status in (UnifiedStatus.RUNNING, UnifiedStatus.STARTING)

    def status_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable status snapshot for the UI."""
        with self._lock:
            uptime = time.time() - self._start_time if self._start_time else 0.0
            summary = dict(self._summary)
            stats = UnifiedRunnerStats(
                status=self.status.value,
                address=str(summary.get("address", "")),
                version=str(summary.get("version", UNIFIED_VERSION)),
                will_run=list(summary.get("will_run", [])),
                enabled_but_pending=list(summary.get("enabled_but_pending", [])),
                uptime_seconds=uptime,
                components=list(summary.get("components", [])),
            )
            result = stats.to_dict()
            if self._error:
                result["error"] = self._error
            return result


# Global runner instance (mirrors miner_runner.get_runner()).
_runner: Optional[UnifiedRunner] = None


def get_unified_runner() -> UnifiedRunner:
    """Get the process-global :class:`UnifiedRunner` instance."""
    global _runner
    if _runner is None:
        _runner = UnifiedRunner()
    return _runner
