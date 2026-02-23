from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

import requests
from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.models.profile_models import RpcProfile
from animica_studio.services.explorer_style_rpc import _format_anm, _normalize_rpc_url, _parse_balance_value

log = logging.getLogger(__name__)


@dataclass
class BalanceResult:
    ok: bool
    amount_smallest: int | None
    formatted: str | None
    error_reason: str | None
    source: str = "rpc"


class BalanceService(QObject):
    balance_ready = Signal(str, object)
    rpc_status_changed = Signal(bool, str)

    def __init__(self, parent: QObject | None = None, *, cache_ttl_s: float = 20.0, max_concurrency: int = 4) -> None:
        super().__init__(parent)
        self._cache_ttl_s = cache_ttl_s
        self._pool = ThreadPoolExecutor(max_workers=max(1, max_concurrency), thread_name_prefix="balance-rpc")
        self._cache: dict[tuple[str, str], tuple[float, BalanceResult]] = {}
        self._inflight: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def get_balance(
        self,
        address: str,
        profile: RpcProfile,
        *,
        force_refresh: bool = False,
        on_result: Callable[[BalanceResult], None] | None = None,
    ) -> BalanceResult:
        rpc_url = profile.effective_rpc_url()
        key = (rpc_url, address)
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and not force_refresh and (now - cached[0]) <= self._cache_ttl_s:
                if on_result:
                    QTimer.singleShot(0, lambda c=cached[1]: on_result(c))
                return cached[1]
            if key in self._inflight:
                return BalanceResult(False, None, None, "Balance request in progress")
            self._inflight.add(key)

        fut = self._pool.submit(self._fetch_balance_sync, address, rpc_url)

        def _done() -> None:
            result: BalanceResult
            try:
                result = fut.result(timeout=0)
            except Exception as exc:  # noqa: BLE001
                result = BalanceResult(ok=False, amount_smallest=None, formatted=None, error_reason=f"RPC unreachable: {exc}")
            with self._lock:
                self._cache[key] = (time.time(), result)
                self._inflight.discard(key)
            self.balance_ready.emit(address, result)
            self.rpc_status_changed.emit(result.ok, result.error_reason or "RPC Online")
            if on_result:
                on_result(result)

        QTimer.singleShot(0, lambda: fut.add_done_callback(lambda _f: QTimer.singleShot(0, _done)))
        return BalanceResult(False, None, None, "Unavailable: fetching")

    def get_balances(self, addresses: list[str], profile: RpcProfile, *, force_refresh: bool = False) -> dict[str, BalanceResult]:
        out: dict[str, BalanceResult] = {}
        for addr in addresses:
            out[addr] = self.get_balance(addr, profile, force_refresh=force_refresh)
        return out

    def _fetch_balance_sync(self, address: str, rpc_url: str) -> BalanceResult:
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "state.getBalance",
            "params": [address, "latest"],
        }
        try:
            response = requests.post(
                _normalize_rpc_url(rpc_url),
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=(3.0, 8.0),
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except requests.Timeout:
            return BalanceResult(False, None, None, "Timeout")
        except requests.RequestException:
            return BalanceResult(False, None, None, "RPC unreachable")
        except Exception as exc:  # noqa: BLE001
            return BalanceResult(False, None, None, f"RPC unreachable: {exc}")

        if body.get("error"):
            err = str(body.get("error"))
            if "method" in err.lower() and "not" in err.lower():
                return BalanceResult(False, None, None, "RPC method not supported by this node")
            return BalanceResult(False, None, None, err)

        result = body.get("result")
        if isinstance(result, dict):
            result = result.get("balance", "0x0")
        try:
            raw = _parse_balance_value(result)
        except Exception as exc:  # noqa: BLE001
            return BalanceResult(False, None, None, f"Invalid RPC response: {exc}")

        formatted = f"{_format_anm(raw)} ANM"
        log.debug("Balance RPC parity: addr=%s amount_smallest=%s formatted=%s", address, raw, formatted)
        return BalanceResult(True, raw, formatted, None)
