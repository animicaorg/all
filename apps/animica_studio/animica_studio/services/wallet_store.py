"""Wallet repository helpers for loading local ``wallets.json``."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class _FallbackWalletParseError(Exception):
    """Stub used when the animica.wallet.serialization module is unavailable."""


def _fallback_parse_wallets_text(text: str, **kwargs: Any) -> Any:
    """No-op stub: always returns an object with an empty store.

    The ``text`` and keyword arguments are accepted but ignored; this stub is
    only used when the real serialization module is unavailable.
    """

    class _R:
        store: dict = {}

    return _R()


def _load_wallet_serialization() -> tuple[type[Exception], Callable[..., Any]]:
    try:
        from animica.wallet.serialization import WalletParseError, parse_wallets_text

        return WalletParseError, parse_wallets_text
    except ModuleNotFoundError:
        here = Path(__file__).resolve()
        root = here.parents[4]
        module_path = root / "python" / "animica" / "wallet" / "serialization.py"
        try:
            spec = importlib.util.spec_from_file_location("animica_wallet_serialization", module_path)
            if spec is None or spec.loader is None:
                raise ModuleNotFoundError(f"wallet serialization module not found at {module_path}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod.WalletParseError, mod.parse_wallets_text
        except Exception:  # noqa: BLE001
            # Serialization helpers unavailable — fall back to plain JSON parsing in WalletStore
            return _FallbackWalletParseError, _fallback_parse_wallets_text


WalletParseError, _parse_wallets_text = _load_wallet_serialization()
log = logging.getLogger(__name__)


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

    def __init__(self) -> None:
        self.last_warning: str | None = None

    def load_local_wallets(self, wallets_path: Path) -> list[WalletRecord]:
        self.last_warning = None
        if not wallets_path.exists():
            return []

        try:
            text = wallets_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.warning("WalletStore: failed to read wallets file %s: %s", wallets_path, exc)
            self.last_warning = f"Failed to read wallets file: {exc}"
            return []

        source_wallets: list[Any] = []
        try:
            parsed = _parse_wallets_text(text, source=str(wallets_path))
            wallets = parsed.store.get("wallets", []) if hasattr(parsed, "store") else []
            if isinstance(wallets, list):
                source_wallets = wallets
        except WalletParseError as exc:
            log.warning("WalletStore: strict wallet parse failed for %s: %s", wallets_path, exc)
            self.last_warning = f"Wallet format warning: {exc}"

        if not source_wallets:
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                log.warning("WalletStore: invalid wallets.json at %s: %s", wallets_path, exc)
                self.last_warning = f"Invalid wallets.json: {exc}"
                return []
            except Exception as exc:  # noqa: BLE001
                log.warning("WalletStore: failed to decode wallet JSON at %s: %s", wallets_path, exc)
                self.last_warning = f"Failed to decode wallets.json: {exc}"
                return []

            if isinstance(raw, dict):
                wallets = raw.get("wallets", [])
                if isinstance(wallets, list):
                    source_wallets = wallets
                else:
                    log.warning("WalletStore: wallets key has unexpected type: %s", type(wallets).__name__)
                    self.last_warning = "wallets.json has unexpected schema: wallets is not a list"
                    return []
            else:
                log.warning("WalletStore: root JSON is not an object: %s", type(raw).__name__)
                self.last_warning = "wallets.json has unexpected schema: root is not an object"
                return []

        records: list[WalletRecord] = []
        for idx, wallet in enumerate(source_wallets):
            if not isinstance(wallet, dict):
                log.warning("WalletStore: skipping non-object wallet entry at index %d", idx)
                self.last_warning = "wallets.json contains non-object wallet entries"
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


def load_wallets(wallets_path: Path) -> list[WalletRecord]:
    """Load wallets from *wallets_path* safely.

    Returns an empty list when the file is missing, contains invalid JSON,
    or has an unexpected schema.  Never raises.
    """
    log.info("Loading wallets from: %s", wallets_path)
    records = WalletStore().load_local_wallets(wallets_path)
    log.info("Loaded %d wallets", len(records))
    return records


__all__ = ["WalletStore", "WalletRecord", "WalletParseError", "load_wallets"]
