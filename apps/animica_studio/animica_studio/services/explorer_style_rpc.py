"""Explorer2-parity RPC balance client.

Canonical Explorer2 contract mirrored here:
- Endpoint: POST <rpc_url> (Explorer2 normalizes bare host/path '/' -> '/rpc').
- JSON-RPC payload: {"jsonrpc":"2.0","id":<int>,"method":"state.getBalance","params":["<address>","latest"]}
- Headers: Content-Type: application/json
- Response.result: either balance string (typically hex like "0x..."), or object { balance: "0x..." }.
- Units: smallest unit is nANM. Explorer2 converts nANM -> ANM with 10^9 divisor and exact integer math.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import requests


_NANM_PER_ANM = 10**9


@dataclass
class BalanceResult:
    address: str
    raw_nanm: int = 0
    formatted_anm: str = "—"
    updated_ts: float = 0.0
    error: str | None = None
    tooltip: str | None = None
    from_cache: bool = False


class ExplorerStyleRpcClient:
    def __init__(
        self,
        *,
        cache_ttl_s: float = 15.0,
        max_concurrency: int = 6,
        min_interval_s: float = 1.0,
    ) -> None:
        self._cache_ttl_s = cache_ttl_s
        self._max_concurrency = max(1, max_concurrency)
        self._min_interval_s = max(0.0, min_interval_s)
        self._cache: dict[tuple[str, str], BalanceResult] = {}
        self._last_hit: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._last_hit.clear()

    def get_balance(self, address: str, rpc_url: str, *, force_refresh: bool = False) -> BalanceResult:
        key = (rpc_url, address)
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and not force_refresh and (now - cached.updated_ts) <= self._cache_ttl_s:
                return BalanceResult(**{**cached.__dict__, "from_cache": True})
            last = self._last_hit.get(key, 0.0)
            if not force_refresh and (now - last) < self._min_interval_s and cached:
                return BalanceResult(**{**cached.__dict__, "from_cache": True})
            self._last_hit[key] = now

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": int(now * 1000),
                "method": "state.getBalance",
                "params": [address, "latest"],
            }
            resp = requests.post(
                _normalize_rpc_url(rpc_url),
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=(3.0, 5.0),
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("error"):
                raise RuntimeError(body["error"].get("message") or str(body["error"]))
            result = body.get("result")
            if isinstance(result, dict):
                result = result.get("balance", "0x0")
            raw_nanm = _parse_balance_value(result)
            formatted = _format_anm(raw_nanm)
            out = BalanceResult(
                address=address,
                raw_nanm=raw_nanm,
                formatted_anm=f"{formatted} ANM",
                updated_ts=time.time(),
                tooltip="RPC balance (Explorer2 parity)",
            )
            with self._lock:
                self._cache[key] = out
            return out
        except Exception as exc:  # noqa: BLE001
            msg = f"RPC error: {exc}"
            with self._lock:
                cached = self._cache.get(key)
            if cached:
                return BalanceResult(
                    address=address,
                    raw_nanm=cached.raw_nanm,
                    formatted_anm=cached.formatted_anm,
                    updated_ts=cached.updated_ts,
                    error=msg,
                    tooltip=msg,
                    from_cache=True,
                )
            return BalanceResult(address=address, error=msg, tooltip=msg)

    def get_balances(self, addresses: list[str], rpc_url: str, *, force_refresh: bool = False) -> dict[str, BalanceResult]:
        if not addresses:
            return {}
        results: dict[str, BalanceResult] = {}
        with ThreadPoolExecutor(max_workers=self._max_concurrency) as pool:
            futs = {
                pool.submit(self.get_balance, addr, rpc_url, force_refresh=force_refresh): addr
                for addr in addresses
            }
            for fut in as_completed(futs):
                addr = futs[fut]
                try:
                    results[addr] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    results[addr] = BalanceResult(address=addr, error=f"RPC error: {exc}", tooltip=f"RPC error: {exc}")
        return results


def _normalize_rpc_url(raw: str) -> str:
    text = (raw or "").strip() or "http://127.0.0.1:8545/rpc"
    if text.startswith("ws://") or text.startswith("wss://"):
        return text
    if "://" not in text:
        text = f"http://{text}"
    parsed = requests.utils.urlparse(text)
    path = parsed.path or ""
    if path in {"", "/"}:
        path = "/rpc"
    normalized = parsed._replace(path=path.rstrip("/") or "/rpc")
    return requests.utils.urlunparse(normalized)


def _parse_balance_value(value: Any) -> int:
    if isinstance(value, str):
        val = value.strip()
        if val.startswith("0x"):
            return max(0, int(val, 16))
        return max(0, int(val))
    if isinstance(value, int):
        return max(0, value)
    raise ValueError(f"Unexpected balance result type: {type(value).__name__}")


def _format_anm(raw_nanm: int) -> str:
    whole = raw_nanm // _NANM_PER_ANM
    rem = raw_nanm % _NANM_PER_ANM
    if rem == 0:
        return f"{whole:,}"
    frac = str(rem).rjust(9, "0").rstrip("0")
    return f"{whole:,}.{frac}"
