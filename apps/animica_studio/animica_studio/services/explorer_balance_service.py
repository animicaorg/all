"""ExplorerBalanceService — non-blocking, cached balance fetcher backed by Explorer API.

Used by both the Dashboard (total balance) and Wallet page (per-wallet balance).
Qt network I/O runs on the main event loop via QNetworkAccessManager (no Python worker threads).
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from threading import Lock
from typing import Callable
from urllib.parse import quote

try:
    from PySide6.QtCore import QObject, QTimer, QUrl, Signal
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False

import requests

from animica_studio.models.profile_models import RpcProfile
from animica_studio.models.wallet_models import ANM_DECIMALS, format_amount
from animica_studio.services.activity_store import ActivityStore

log = logging.getLogger(__name__)

_CACHE_TTL_S = 20.0
_CONNECT_TIMEOUT_S = 3.0
_TOTAL_TIMEOUT_S = 8.0
_LOG_SNIPPET_LEN = 200
_MAX_CONCURRENT_REQUESTS = 4


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


def _fetch_balance_sync(address: str, base_url: str, decimals: int = ANM_DECIMALS) -> BalanceResult:
    """Blocking fallback fetch for headless environments."""
    url = f"{base_url}/api/address/{address}"
    t0 = time.monotonic()
    try:
        resp = requests.get(url, timeout=(_CONNECT_TIMEOUT_S, _TOTAL_TIMEOUT_S))
    except requests.RequestException as exc:
        log.warning("ExplorerBalanceService: request failed for %s: %s (%.2fs)", address, exc, time.monotonic() - t0)
        return BalanceResult(address=address, ok=False, error=f"Request failed: {exc}")

    if resp.status_code != 200:
        return BalanceResult(address=address, ok=False, error=f"HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        snippet = resp.text[:_LOG_SNIPPET_LEN]
        log.warning("ExplorerBalanceService: JSON parse error for %s: %s — response: %r", address, exc, snippet)
        return BalanceResult(address=address, ok=False, error=f"JSON parse error: {exc}")

    return _result_from_payload(address, payload, decimals)


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
            try:
                d = Decimal(cleaned)
                return int(d)
            except InvalidOperation:
                continue
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _result_from_payload(address: str, payload: object, decimals: int) -> BalanceResult:
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


if _QT_AVAILABLE:
    @dataclass
    class _PendingRequest:
        address: str
        base_url: str
        decimals: int

    class ExplorerBalanceService(QObject):
        _instance: "ExplorerBalanceService | None" = None

        balanceReady = Signal(str, object)
        balanceError = Signal(str, str)

        def __init__(self, parent: QObject | None = None) -> None:
            super().__init__(parent)
            self._lock = Lock()
            self._cache: dict[str, BalanceResult] = {}
            self._in_flight: dict[str, list[Callable[[BalanceResult], None]]] = {}
            self._active_replies: dict[object, _PendingRequest] = {}
            self._queue: deque[_PendingRequest] = deque()
            self._manager = QNetworkAccessManager(self)

        @classmethod
        def instance(cls) -> "ExplorerBalanceService":
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        def _dispatch_next(self) -> None:
            while self._queue and len(self._active_replies) < _MAX_CONCURRENT_REQUESTS:
                pending = self._queue.popleft()
                encoded = quote(pending.address, safe="")
                url = QUrl(f"{pending.base_url}/api/address/{encoded}")
                req = QNetworkRequest(url)
                req.setTransferTimeout(int(_TOTAL_TIMEOUT_S * 1000))
                reply = self._manager.get(req)
                self._active_replies[reply] = pending
                reply.finished.connect(lambda r=reply: self._on_reply_finished(r))

        def get_balance(
            self,
            address: str,
            profile: RpcProfile,
            *,
            on_result: Callable[[BalanceResult], None] | None = None,
            force_refresh: bool = False,
            decimals: int = ANM_DECIMALS,
        ) -> None:
            base_url = (profile.explorer_base_url or "").strip().rstrip("/")
            if not base_url:
                result = BalanceResult(address=address, ok=False, error="Explorer not configured")
                if on_result:
                    QTimer.singleShot(0, lambda: on_result(result))
                return

            with self._lock:
                cached = self._cache.get(address)
                if not force_refresh and cached is not None and (time.time() - cached.updated_ts) <= _CACHE_TTL_S:
                    if on_result:
                        QTimer.singleShot(0, lambda: on_result(cached))
                    return

                if address in self._in_flight:
                    if on_result:
                        self._in_flight[address].append(on_result)
                    return

                self._in_flight[address] = [on_result] if on_result else []

            self._queue.append(_PendingRequest(address=address, base_url=base_url, decimals=decimals))
            self._dispatch_next()

        def _on_reply_finished(self, reply: object) -> None:
            pending = self._active_replies.pop(reply, None)
            if pending is None:
                return

            address = pending.address
            result: BalanceResult
            if reply.error():
                err = reply.errorString() or "Network error"
                result = BalanceResult(address=address, ok=False, error=err)
                self.balanceError.emit(address, err)
            else:
                raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                    result = _result_from_payload(address, payload, pending.decimals)
                except json.JSONDecodeError as exc:
                    snippet = raw[:_LOG_SNIPPET_LEN]
                    log.warning("ExplorerBalanceService: JSON parse error for %s: %s — response: %r", address, exc, snippet)
                    result = BalanceResult(address=address, ok=False, error=f"JSON parse error: {exc}")

            reply.deleteLater()
            self._on_result(address, result)
            self._dispatch_next()

        def get_balances(self, addresses: list[str], profile: RpcProfile, *, on_each=None, on_all=None, force_refresh=False, decimals=ANM_DECIMALS) -> None:
            if not addresses:
                if on_all:
                    QTimer.singleShot(0, lambda: on_all({}))
                return

            results: dict[str, BalanceResult] = {}
            remaining = [len(addresses)]

            def _handle(addr: str, result: BalanceResult) -> None:
                if on_each:
                    on_each(addr, result)
                results[addr] = result
                remaining[0] -= 1
                if remaining[0] == 0 and on_all:
                    on_all(dict(results))

            for addr in addresses:
                self.get_balance(addr, profile, on_result=lambda r, a=addr: _handle(a, r), force_refresh=force_refresh, decimals=decimals)

        def sum_balances(self, addresses: list[str], profile: RpcProfile, *, on_result=None, force_refresh=False, decimals=ANM_DECIMALS) -> None:
            if not addresses:
                total = TotalBalanceResult(wallet_count=0, formatted="0 ANM", ok_count=0)
                if on_result:
                    QTimer.singleShot(0, lambda: on_result(total))
                return

            def _on_all(results: dict[str, BalanceResult]) -> None:
                total_wei = 0
                ok_count = 0
                errors: list[str] = []
                for item in results.values():
                    if item.ok:
                        total_wei += item.balance_wei
                        ok_count += 1
                    elif item.error and item.error not in errors:
                        errors.append(item.error)
                total = TotalBalanceResult(
                    total_wei=total_wei,
                    formatted=format_amount(total_wei, decimals),
                    wallet_count=len(addresses),
                    ok_count=ok_count,
                    error_count=len(addresses) - ok_count,
                    errors=errors,
                    updated_ts=time.time(),
                )
                ActivityStore.instance().record_balance_fetch(
                    f"Total balance for {len(addresses)} wallet(s): {total.formatted}",
                    ok=total.error_count == 0,
                    detail="; ".join(errors) if errors else "",
                )
                if on_result:
                    on_result(total)

            self.get_balances(addresses, profile, on_all=_on_all, force_refresh=force_refresh, decimals=decimals)

        def invalidate(self, address: str | None = None) -> None:
            with self._lock:
                if address is None:
                    self._cache.clear()
                else:
                    self._cache.pop(address, None)

        def _on_result(self, address: str, result: BalanceResult) -> None:
            with self._lock:
                self._cache[address] = result
                waiters = self._in_flight.pop(address, [])
            if result.ok:
                self.balanceReady.emit(address, result)
            else:
                self.balanceError.emit(address, result.error)
            for cb in waiters:
                try:
                    cb(result)
                except Exception:
                    log.exception("ExplorerBalanceService: callback error for %s", address)
else:
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

        def get_balance(self, address: str, profile: RpcProfile, *, on_result=None, force_refresh=False, decimals=ANM_DECIMALS):
            base_url = (profile.explorer_base_url or "").strip().rstrip("/")
            result = BalanceResult(address=address, ok=False, error="Explorer not configured") if not base_url else _fetch_balance_sync(address, base_url, decimals)
            if on_result:
                on_result(result)

        def get_balances(self, addresses, profile, *, on_each=None, on_all=None, force_refresh=False, decimals=ANM_DECIMALS):
            results: dict[str, BalanceResult] = {}
            for addr in addresses:
                self.get_balance(addr, profile, on_result=lambda r, a=addr: results.__setitem__(a, r), force_refresh=force_refresh, decimals=decimals)
                if on_each:
                    on_each(addr, results.get(addr))
            if on_all:
                on_all(results)

        def sum_balances(self, addresses, profile, *, on_result=None, force_refresh=False, decimals=ANM_DECIMALS):
            def _on_all(results):
                total_wei = sum(r.balance_wei for r in results.values() if r.ok)
                ok_count = sum(1 for r in results.values() if r.ok)
                errors = [r.error for r in results.values() if not r.ok]
                total = TotalBalanceResult(total_wei=total_wei, formatted=format_amount(total_wei, decimals), wallet_count=len(addresses), ok_count=ok_count, error_count=len(errors), errors=errors)
                if on_result:
                    on_result(total)
            self.get_balances(addresses, profile, on_all=_on_all, force_refresh=force_refresh, decimals=decimals)

        def invalidate(self, address=None):
            if address is None:
                self._cache.clear()
            else:
                self._cache.pop(address, None)


__all__ = ["ExplorerBalanceService", "BalanceResult", "TotalBalanceResult"]
