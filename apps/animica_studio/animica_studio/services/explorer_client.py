"""Explorer HTTP client for read-only balance fallback."""

from __future__ import annotations

import logging
from typing import Any

import requests

from animica_studio.models.wallet_models import ANM_DECIMALS, BalanceSource, BalanceState, format_amount

log = logging.getLogger(__name__)


class ExplorerClient:
    """Small explorer API client for balance lookups."""

    def __init__(self, base_url: str, *, connect_timeout_s: float = 3.0, total_timeout_s: float = 8.0) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._connect_timeout_s = connect_timeout_s
        self._total_timeout_s = total_timeout_s

    def get_balance(self, address: str, decimals: int = ANM_DECIMALS) -> BalanceState:
        if not self._base_url:
            raise RuntimeError("Explorer URL is not configured")

        url = f"{self._base_url}/api/address/{address}"
        try:
            resp = requests.get(url, timeout=(self._connect_timeout_s, self._total_timeout_s))
        except requests.RequestException as exc:
            raise RuntimeError(f"Explorer request failed: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(f"Explorer HTTP {resp.status_code} for {url}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Explorer JSON parse error: {exc}") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Explorer returned non-object JSON payload")

        raw = self._extract_balance_wei(payload)
        if raw is None:
            raise RuntimeError("Explorer payload missing confirmedBalance/balance")
        raw = max(0, int(raw))
        return BalanceState(
            address=address,
            balance_wei=raw,
            formatted=format_amount(raw, decimals),
            error=None,
            source=BalanceSource.EXPLORER,
            tooltip="RPC unavailable; showing explorer balance",
        )

    @staticmethod
    def _extract_balance_wei(payload: dict[str, Any]) -> int | None:
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
                    continue
            if isinstance(value, (int, float)):
                return int(value)
        return None
