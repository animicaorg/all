"""Wallet repository helpers for loading local ``wallets.json``."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def _load_wallet_serialization() -> tuple[type[Exception], Callable[..., Any]]:
    try:
        from animica.wallet.serialization import WalletParseError, parse_wallets_text

        return WalletParseError, parse_wallets_text
    except ModuleNotFoundError:
        here = Path(__file__).resolve()
        root = here.parents[4]
        module_path = root / "python" / "animica" / "wallet" / "serialization.py"
        spec = importlib.util.spec_from_file_location("animica_wallet_serialization", module_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod.WalletParseError, mod.parse_wallets_text


WalletParseError, _parse_wallets_text = _load_wallet_serialization()


@dataclass
class WalletRecord:
    """Canonical wallet record loaded from ``wallets.json``."""

    wallet_id: str
    address: str
    label: str
    algorithm: str | None = None
    created_at: str | None = None


class WalletStore:
    """Single source of truth for local wallet-file reads."""

    def load_local_wallets(self, wallets_path: Path) -> list[WalletRecord]:
        if not wallets_path.exists():
            raise FileNotFoundError(wallets_path)

        text = wallets_path.read_text(encoding="utf-8")
        parsed = _parse_wallets_text(text, source=str(wallets_path))
        source_wallets = parsed.store.get("wallets", [])
        if not source_wallets:
            try:
                raw = json.loads(text)
                if isinstance(raw, dict) and isinstance(raw.get("wallets"), list):
                    source_wallets = raw.get("wallets", [])
            except Exception:
                pass

        records: list[WalletRecord] = []
        for idx, wallet in enumerate(source_wallets):
            if not isinstance(wallet, dict):
                continue
            label = str(wallet.get("label") or wallet.get("name") or f"wallet-{idx + 1}")
            address = str(wallet.get("address") or "")
            algorithm = wallet.get("alg_name") or wallet.get("algorithm")
            records.append(
                WalletRecord(
                    wallet_id=address or label,
                    address=address,
                    label=label,
                    algorithm=str(algorithm) if algorithm else None,
                    created_at=str(wallet.get("created_at")) if wallet.get("created_at") else None,
                )
            )
        return records


__all__ = ["WalletStore", "WalletRecord", "WalletParseError"]
