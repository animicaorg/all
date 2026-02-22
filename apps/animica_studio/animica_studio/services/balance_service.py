"""Balance service with RPC-first and explorer fallback behavior."""

from __future__ import annotations

import logging
import time

from animica_studio.models.profile_models import RpcProfile
from animica_studio.models.wallet_models import BalanceSource, BalanceState, format_amount
from animica_studio.services.error_format import format_rpc_error
from animica_studio.services.explorer_client import ExplorerClient
from animica_studio.services.rpc_client import RpcClient

log = logging.getLogger(__name__)


class BalanceService:
    def __init__(self, *, rpc_ttl_s: float = 10.0, explorer_ttl_s: float = 20.0) -> None:
        self._rpc_ttl_s = rpc_ttl_s
        self._explorer_ttl_s = explorer_ttl_s
        self._cache: dict[tuple[str, str, str], BalanceState] = {}

    def clear(self) -> None:
        self._cache.clear()

    def get_balance(self, address: str, profile: RpcProfile, decimals: int = 18, *, force_refresh: bool = False) -> BalanceState:
        rpc_url = profile.effective_rpc_url()
        rpc_state = self._get_rpc_balance(address, rpc_url, decimals, force_refresh=force_refresh)
        if rpc_state and rpc_state.error is None and rpc_state.formatted not in {"", "—"}:
            return rpc_state

        explorer_url = (profile.explorer_base_url or "").strip().rstrip("/")
        if explorer_url:
            explorer_state = self._get_explorer_balance(address, explorer_url, decimals, force_refresh=force_refresh)
            if explorer_state and explorer_state.error is None and explorer_state.formatted not in {"", "—"}:
                return explorer_state
            err = explorer_state.error if explorer_state else "Explorer request failed"
            return BalanceState(address=address, formatted="—", error=err)

        if rpc_state is not None:
            return BalanceState(address=address, formatted="—", error=rpc_state.error or "RPC unavailable")
        return BalanceState(address=address, formatted="—", error="RPC unavailable and explorer not configured")

    def _get_rpc_balance(self, address: str, rpc_url: str, decimals: int, *, force_refresh: bool) -> BalanceState | None:
        key = ("rpc", rpc_url, address)
        cached = self._cache.get(key)
        if cached and not force_refresh and (time.time() - cached.updated_ts) <= self._rpc_ttl_s:
            cached.is_stale = False
            return cached
        try:
            with RpcClient(rpc_url, connect_timeout=3.0, read_timeout=5.0, max_retries=1) as client:
                raw = max(0, int(client.get_balance(address)))
            state = BalanceState(
                address=address,
                balance_wei=raw,
                formatted=format_amount(raw, decimals),
                updated_ts=time.time(),
                error=None,
                source=BalanceSource.RPC,
                is_stale=False,
                tooltip="RPC balance",
            )
            self._cache[key] = state
            return state
        except Exception as exc:  # noqa: BLE001
            msg = format_rpc_error(exc)
            if cached:
                stale = BalanceState(
                    address=address,
                    balance_wei=max(0, int(cached.balance_wei)),
                    formatted=cached.formatted,
                    updated_ts=cached.updated_ts,
                    error=msg,
                    source=cached.source,
                    is_stale=True,
                    tooltip=f"Cached value due to RPC error: {msg}",
                )
                return stale
            return BalanceState(address=address, formatted="—", error=msg, source=BalanceSource.RPC)

    def _get_explorer_balance(self, address: str, explorer_url: str, decimals: int, *, force_refresh: bool) -> BalanceState | None:
        key = ("explorer", explorer_url, address)
        cached = self._cache.get(key)
        if cached and not force_refresh and (time.time() - cached.updated_ts) <= self._explorer_ttl_s:
            cached.is_stale = False
            return cached
        client = ExplorerClient(explorer_url)
        try:
            state = client.get_balance(address, decimals=decimals)
            state.updated_ts = time.time()
            self._cache[key] = state
            return state
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if cached:
                stale = BalanceState(
                    address=address,
                    balance_wei=max(0, int(cached.balance_wei)),
                    formatted=cached.formatted,
                    updated_ts=cached.updated_ts,
                    error=msg,
                    source=BalanceSource.EXPLORER,
                    is_stale=True,
                    tooltip=f"Cached explorer value: {msg}",
                )
                return stale
            return BalanceState(address=address, formatted="—", error=msg, source=BalanceSource.EXPLORER)
