from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from animica_studio.models.wallet_models import is_valid_address

log = logging.getLogger(__name__)


@dataclass
class WalletRecord:
    label: str
    address: str
    sig_scheme: str | None = None
    wallet_id: str | None = None
    public_key: str | None = None


class WalletRepository:
    """Safe local wallets.json loader (source of truth in local mode)."""

    def __init__(self, wallets_path: Path | None = None) -> None:
        self.wallets_path = wallets_path or (Path.home() / ".animica" / "wallets.json")
        self.last_error: str | None = None

    def load_wallets(self) -> list[WalletRecord]:
        self.last_error = None
        if not self.wallets_path.exists():
            return []
        try:
            payload = json.loads(self.wallets_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.last_error = f"Invalid wallets.json: {exc}"
            log.warning("WalletRepository: invalid json: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Failed reading wallets.json: {exc}"
            log.warning("WalletRepository: read failure: %s", exc)
            return []

        wallets_any: list[Any] = []
        if isinstance(payload, dict):
            maybe = payload.get("wallets", payload.get("accounts", []))
            if isinstance(maybe, list):
                wallets_any = maybe
        elif isinstance(payload, list):
            wallets_any = payload

        records: list[WalletRecord] = []
        for idx, raw in enumerate(wallets_any):
            if not isinstance(raw, dict):
                continue
            address = str(raw.get("address") or "").strip()
            if not address or not is_valid_address(address):
                continue
            label = str(raw.get("label") or raw.get("name") or f"Wallet {idx + 1}")
            sig_scheme = raw.get("sig_scheme") or raw.get("alg") or raw.get("alg_name") or raw.get("algorithm")
            wallet_id = raw.get("id") or raw.get("wallet_id")
            public_key = raw.get("public_key") or raw.get("pubkey")
            records.append(
                WalletRecord(
                    label=label,
                    address=address,
                    sig_scheme=str(sig_scheme) if sig_scheme else None,
                    wallet_id=str(wallet_id) if wallet_id else None,
                    public_key=str(public_key) if public_key else None,
                )
            )

        if not records and wallets_any:
            self.last_error = "wallets.json has unexpected schema"
        return records
