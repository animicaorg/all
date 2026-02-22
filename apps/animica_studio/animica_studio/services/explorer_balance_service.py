"""ExplorerBalanceService — non-blocking, cached balance fetcher backed by Explorer API.

Used by both the Dashboard (total balance) and Wallet page (per-wallet balance).
All network I/O is dispatched to a thread pool; results are delivered via callbacks
on the Qt main thread.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Callable

try:
    from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False

import requests

from animica_studio.models.profile_models import RpcProfile
from animica_studio.models.wallet_models import BalanceSource, BalanceState, format_amount
from animica_studio.services.activity_store import ActivityStore

log = logging.getLogger(__name__)

# Cache TTL in seconds
_CACHE_TTL_S = 20.0
# Connect / read timeouts
_CONNECT_TIMEOUT_S = 3.0
_TOTAL_TIMEOUT_S = 8.0
# How many chars of response to log on parse errors
_LOG_SNIPPET_LEN = 200


@dataclass
class BalanceResult:
    address: str
    balance_wei: int = 0
    formatted: str = "—"
    ok: bool = False
    error: str = ""
    source: str = "explorer"
    updated_ts: float = field(default_factory=time.time)


@dataclass
class TotalBalanceResult:
    total_wei: int = 0
    formatted: str = "—"
    wallet_count: int = 0
    ok_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    updated_ts: float = field(default_factory=time.time)


def _fetch_balance_sync(address: str, base_url: str, decimals: int = 18) -> BalanceResult:
    """Blocking balance fetch — must be called from a worker thread."""
    url = f"{base_url}/api/address/{address}"
    t0 = time.monotonic()
    log.debug("ExplorerBalanceService: GET %s", url)
    try:
        resp = requests.get(url, timeout=(_CONNECT_TIMEOUT_S, _TOTAL_TIMEOUT_S))
    except requests.RequestException as exc:
        log.warning("ExplorerBalanceService: request failed for %s: %s (%.2fs)", address, exc, time.monotonic() - t0)
        return BalanceResult(address=address, ok=False, error=f"Request failed: {exc}")

    duration = time.monotonic() - t0
    log.debug("ExplorerBalanceService: HTTP %d for %s in %.2fs", resp.status_code, address, duration)

    if resp.status_code != 200:
        return BalanceResult(address=address, ok=False, error=f"HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        snippet = resp.text[:_LOG_SNIPPET_LEN]
        log.warning("ExplorerBalanceService: JSON parse error for %s: %s — response: %r", address, exc, snippet)
        return BalanceResult(address=address, ok=False, error=f"JSON parse error: {exc}")

    if not isinstance(payload, dict):
        return BalanceResult(address=address, ok=False, error="Unexpected response shape")

    raw = _extract_wei(payload)
    if raw is None:
        snippet = str(payload)[:_LOG_SNIPPET_LEN]
        log.warning("ExplorerBalanceService: missing balance field for %s — payload: %r", address, snippet)
        return BalanceResult(address=address, ok=False, error="Balance field missing in response")

    raw = max(0, int(raw))
    return BalanceResult(
        address=address,
        balance_wei=raw,
        formatted=format_amount(raw, decimals),
        ok=True,
        error="",
        source="explorer",
        updated_ts=time.time(),
    )


def _extract_wei(payload: dict) -> int | None:
    for key in ("confirmedBalance", "balance", "available_balance"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.startswith("0x"):
                try:
                    return int(cleaned, 16)
                except ValueError:
                    continue
            try:
                return int(cleaned)
            except ValueError:
                pass
            # Try Decimal (e.g. "1.5" in whole-unit format — treat as already scaled)
            try:
                d = Decimal(cleaned)
                return int(d)
            except InvalidOperation:
                continue
        if isinstance(value, (int, float)):
            return int(value)
    return None


if _QT_AVAILABLE:
    from PySide6.QtCore import QRunnable  # noqa: PLC0415

    class _BalanceFetchTask(QRunnable):
        """QRunnable that fetches a single balance and emits via signals."""

        def __init__(
            self,
            address: str,
            base_url: str,
            decimals: int,
            signals: "_FetchSignals",
        ) -> None:
            super().__init__()
            self._address = address
            self._base_url = base_url
            self._decimals = decimals
            self._signals = signals

        def run(self) -> None:
            result = _fetch_balance_sync(self._address, self._base_url, self._decimals)
            self._signals.done.emit(result)

    class _FetchSignals(QObject):
        done = Signal(object)

    class ExplorerBalanceService(QObject):
        """Qt-integrated non-blocking balance service with cache and request coalescing.

        Usage
        -----
        ::

            svc = ExplorerBalanceService.instance()
            svc.get_balance(address, profile, on_result=my_callback)
        """

        _instance: "ExplorerBalanceService | None" = None

        def __init__(self, parent: QObject | None = None) -> None:
            super().__init__(parent)
            self._lock = Lock()
            # Cache: address -> (BalanceResult, fetched_ts)
            self._cache: dict[str, BalanceResult] = {}
            # In-flight: address -> list of waiting callbacks
            self._in_flight: dict[str, list[Callable[[BalanceResult], None]]] = {}
            self._pool = QThreadPool.globalInstance()

        @classmethod
        def instance(cls) -> "ExplorerBalanceService":
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        def get_balance(
            self,
            address: str,
            profile: RpcProfile,
            *,
            on_result: Callable[[BalanceResult], None] | None = None,
            force_refresh: bool = False,
            decimals: int = 18,
        ) -> None:
            """Non-blocking balance fetch.  *on_result* is called on the Qt main thread."""
            base_url = (profile.explorer_base_url or "").strip().rstrip("/")
            if not base_url:
                result = BalanceResult(address=address, ok=False, error="Explorer not configured")
                if on_result:
                    QTimer.singleShot(0, lambda: on_result(result))
                return

            with self._lock:
                cached = self._cache.get(address)
                if (
                    not force_refresh
                    and cached is not None
                    and (time.time() - cached.updated_ts) <= _CACHE_TTL_S
                ):
                    result = cached
                    if on_result:
                        QTimer.singleShot(0, lambda: on_result(result))
                    return

                if address in self._in_flight:
                    # Coalesce: just add to waiting list
                    if on_result:
                        self._in_flight[address].append(on_result)
                    return

                # New in-flight request
                waiters: list[Callable[[BalanceResult], None]] = []
                if on_result:
                    waiters.append(on_result)
                self._in_flight[address] = waiters

            signals = _FetchSignals()
            task = _BalanceFetchTask(address, base_url, decimals, signals)
            signals.done.connect(lambda r: self._on_result(address, r))
            self._pool.start(task)

        def get_balances(
            self,
            addresses: list[str],
            profile: RpcProfile,
            *,
            on_each: Callable[[str, BalanceResult], None] | None = None,
            on_all: Callable[[dict[str, BalanceResult]], None] | None = None,
            force_refresh: bool = False,
            decimals: int = 18,
        ) -> None:
            """Request balances for multiple addresses.

            *on_each* is called per address as each result arrives.
            *on_all*  is called once all addresses have resolved.
            """
            if not addresses:
                if on_all:
                    QTimer.singleShot(0, lambda: on_all({}))
                return

            results: dict[str, BalanceResult] = {}
            remaining = [len(addresses)]  # mutable counter
            lock = Lock()

            def _handle(addr: str, result: BalanceResult) -> None:
                if on_each:
                    on_each(addr, result)
                with lock:
                    results[addr] = result
                    remaining[0] -= 1
                    done = remaining[0] == 0
                if done and on_all:
                    on_all(dict(results))

            for addr in addresses:
                self.get_balance(addr, profile, on_result=lambda r, a=addr: _handle(a, r), force_refresh=force_refresh, decimals=decimals)

        def sum_balances(
            self,
            addresses: list[str],
            profile: RpcProfile,
            *,
            on_result: Callable[[TotalBalanceResult], None] | None = None,
            force_refresh: bool = False,
            decimals: int = 18,
        ) -> None:
            """Fetch all addresses and return a TotalBalanceResult."""
            if not addresses:
                total = TotalBalanceResult(wallet_count=0, formatted="0 ANM", ok_count=0)
                if on_result:
                    QTimer.singleShot(0, lambda: on_result(total))
                return

            def _on_all(results: dict[str, BalanceResult]) -> None:
                total_wei = 0
                ok_count = 0
                err_count = 0
                errors: list[str] = []
                for r in results.values():
                    if r.ok:
                        total_wei += r.balance_wei
                        ok_count += 1
                    else:
                        err_count += 1
                        if r.error not in errors:
                            errors.append(r.error)

                formatted = format_amount(total_wei, decimals)
                total = TotalBalanceResult(
                    total_wei=total_wei,
                    formatted=formatted,
                    wallet_count=len(addresses),
                    ok_count=ok_count,
                    error_count=err_count,
                    errors=errors,
                    updated_ts=time.time(),
                )
                ActivityStore.instance().record_balance_fetch(
                    f"Total balance for {len(addresses)} wallet(s): {formatted}",
                    ok=err_count == 0,
                    detail="; ".join(errors) if errors else "",
                )
                if on_result:
                    on_result(total)

            self.get_balances(addresses, profile, on_all=_on_all, force_refresh=force_refresh, decimals=decimals)

        def invalidate(self, address: str | None = None) -> None:
            """Invalidate cache for one address (or all if address is None)."""
            with self._lock:
                if address is None:
                    self._cache.clear()
                else:
                    self._cache.pop(address, None)

        def _on_result(self, address: str, result: BalanceResult) -> None:
            with self._lock:
                self._cache[address] = result
                waiters = self._in_flight.pop(address, [])

            for cb in waiters:
                try:
                    cb(result)
                except Exception:  # noqa: BLE001
                    log.exception("ExplorerBalanceService: callback error for %s", address)

else:
    # Headless stub — only for non-Qt environments (unit tests)
    class ExplorerBalanceService:  # type: ignore[no-redef]
        _instance: "ExplorerBalanceService | None" = None

        def __init__(self) -> None:
            self._lock = Lock()
            self._cache: dict[str, BalanceResult] = {}

        @classmethod
        def instance(cls) -> "ExplorerBalanceService":
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        def get_balance(self, address: str, profile: RpcProfile, *, on_result=None, force_refresh=False, decimals=18):
            base_url = (profile.explorer_base_url or "").strip().rstrip("/")
            if not base_url:
                result = BalanceResult(address=address, ok=False, error="Explorer not configured")
            else:
                result = _fetch_balance_sync(address, base_url, decimals)
            if on_result:
                on_result(result)

        def get_balances(self, addresses, profile, *, on_each=None, on_all=None, force_refresh=False, decimals=18):
            results: dict[str, BalanceResult] = {}
            for addr in addresses:
                self.get_balance(addr, profile, on_result=lambda r, a=addr: results.__setitem__(a, r), force_refresh=force_refresh, decimals=decimals)
                if on_each:
                    on_each(addr, results.get(addr))
            if on_all:
                on_all(results)

        def sum_balances(self, addresses, profile, *, on_result=None, force_refresh=False, decimals=18):
            def _on_all(results):
                total_wei = sum(r.balance_wei for r in results.values() if r.ok)
                ok_count = sum(1 for r in results.values() if r.ok)
                errors = [r.error for r in results.values() if not r.ok]
                total = TotalBalanceResult(
                    total_wei=total_wei,
                    formatted=format_amount(total_wei, decimals),
                    wallet_count=len(addresses),
                    ok_count=ok_count,
                    error_count=len(errors),
                    errors=errors,
                )
                if on_result:
                    on_result(total)
            self.get_balances(addresses, profile, on_all=_on_all, force_refresh=force_refresh, decimals=decimals)

        def invalidate(self, address=None):
            if address is None:
                self._cache.clear()
            else:
                self._cache.pop(address, None)


__all__ = ["ExplorerBalanceService", "BalanceResult", "TotalBalanceResult"]
