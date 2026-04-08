"""Centralized settings, onboarding, and wallet-contact helpers for Studio."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from animica_studio.models.profile_models import RpcProfile, validate_explorer_base_url, validate_rpc_url
from animica_studio.storage.config import Config, save_config
from animica_studio.util.paths import animica_wallets_file


@dataclass(frozen=True)
class NetworkPreset:
    key: str
    label: str
    chain_id: int
    remote_rpc_url: str
    local_rpc_url: str
    explorer_url: str = ""
    bootstrap_command: str = ""


_PRESETS = [
    NetworkPreset(
        key="mainnet",
        label="Mainnet",
        chain_id=1,
        remote_rpc_url="https://mainnet.animica.org/rpc",
        local_rpc_url="http://127.0.0.1:8545/rpc",
        explorer_url="https://explorer.animica.org",
        bootstrap_command="animica node bootstrap --network mainnet",
    ),
    NetworkPreset(
        key="testnet",
        label="Testnet",
        chain_id=2,
        remote_rpc_url="https://rpc.testnet.animica.org/rpc",
        local_rpc_url="http://127.0.0.1:18546/rpc",
        bootstrap_command="animica node bootstrap --network testnet",
    ),
    NetworkPreset(
        key="devnet",
        label="Devnet",
        chain_id=1337,
        remote_rpc_url="http://127.0.0.1:28545/rpc",
        local_rpc_url="http://127.0.0.1:28545/rpc",
        bootstrap_command="animica node bootstrap --network devnet",
    ),
    NetworkPreset(
        key="local-devnet",
        label="Local Devnet",
        chain_id=1337,
        remote_rpc_url="http://127.0.0.1:38545/rpc",
        local_rpc_url="http://127.0.0.1:38545/rpc",
        bootstrap_command="animica node bootstrap --network local-devnet",
    ),
]


class SettingsService:
    def __init__(self, config: Config) -> None:
        self._config = config

    def network_presets(self) -> list[NetworkPreset]:
        return list(_PRESETS)

    def get_network_preset(self, key: str) -> NetworkPreset | None:
        for preset in _PRESETS:
            if preset.key == key:
                return preset
        return None

    def detect_network(self, profile: RpcProfile) -> str:
        for preset in _PRESETS:
            if profile.chain_id_expected != preset.chain_id:
                continue
            rpc_candidates = {preset.remote_rpc_url, preset.local_rpc_url}
            if profile.effective_rpc_url() in rpc_candidates or profile.rpc_url in rpc_candidates:
                return preset.key
        return str(((self._config.onboarding or {}).get("last_network")) or "custom")

    def apply_network_preset(self, profile: RpcProfile, preset_key: str, *, local_node: bool) -> RpcProfile:
        preset = self.get_network_preset(preset_key)
        if preset is None:
            return profile
        profile.chain_id_expected = preset.chain_id
        if local_node:
            profile.rpc_url = preset.local_rpc_url
            profile.node_rpc_url = preset.local_rpc_url
            if not profile.node_start_cmd:
                profile.node_start_cmd = ["animica", "node", "start"]
        else:
            profile.rpc_url = preset.remote_rpc_url
            profile.node_rpc_url = profile.node_rpc_url or preset.local_rpc_url
        if preset.explorer_url and not profile.explorer_base_url:
            profile.explorer_base_url = preset.explorer_url
        self._config.onboarding = {
            **(self._config.onboarding or {}),
            "last_network": preset.key,
        }
        return profile

    def rerun_onboarding(self) -> None:
        self._config.first_run_completed = False
        self._config.onboarding = {
            **(self._config.onboarding or {}),
            "completed_at": None,
        }
        save_config(self._config)

    def mark_onboarding_complete(self, network_key: str | None = None) -> None:
        self._config.first_run_completed = True
        current = dict(self._config.onboarding or {})
        current["wizard_version"] = 2
        current["completed_at"] = time.time()
        if network_key:
            current["last_network"] = network_key
        self._config.onboarding = current
        save_config(self._config)

    def list_wallet_contacts(self) -> list[dict[str, str]]:
        rows = list(self._config.wallet_contacts or [])
        return [
            {"label": str(row.get("label") or "").strip(), "address": str(row.get("address") or "").strip()}
            for row in rows
            if isinstance(row, dict) and str(row.get("label") or "").strip() and str(row.get("address") or "").strip()
        ]

    def add_wallet_contact(self, label: str, address: str) -> None:
        clean_label = label.strip()
        clean_address = address.strip()
        rows = [row for row in self.list_wallet_contacts() if row["address"] != clean_address]
        rows.append({"label": clean_label, "address": clean_address})
        rows.sort(key=lambda row: row["label"].lower())
        self._config.wallet_contacts = rows
        save_config(self._config)

    def remove_wallet_contact(self, address: str) -> None:
        clean_address = address.strip()
        self._config.wallet_contacts = [
            row for row in self.list_wallet_contacts() if row["address"] != clean_address
        ]
        save_config(self._config)

    def last_selected_wallet(self) -> str | None:
        value = str(self._config.wallet_last_selected_address or "").strip()
        return value or None

    def set_last_selected_wallet(self, address: str | None) -> None:
        clean = str(address or "").strip() or None
        if self._config.wallet_last_selected_address == clean:
            return
        self._config.wallet_last_selected_address = clean
        save_config(self._config)

    def import_wallet_store(self, source_path: str | Path) -> tuple[int, Path]:
        src = Path(source_path).expanduser()
        if not src.exists():
            raise FileNotFoundError(f"Wallet file not found: {src}")

        dst = animica_wallets_file()
        dst.parent.mkdir(parents=True, exist_ok=True)

        if src.resolve() == dst.resolve():
            return self._wallet_count(dst), dst

        incoming = self._load_wallet_store(src)
        if not incoming:
            raise ValueError("The selected file does not contain any wallet entries.")

        existing_payload = self._load_wallet_store_raw(dst) if dst.exists() else {"wallets": []}
        existing_wallets = existing_payload.get("wallets", []) if isinstance(existing_payload, dict) else []
        if not isinstance(existing_wallets, list):
            existing_wallets = []

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in [*existing_wallets, *incoming]:
            if not isinstance(raw, dict):
                continue
            address = str(raw.get("address") or "").strip()
            if not address or address in seen:
                continue
            seen.add(address)
            merged.append(dict(raw))

        if not dst.exists() and len(merged) == len(incoming):
            shutil.copyfile(src, dst)
            return len(incoming), dst

        dst.write_text(json.dumps({"wallets": merged}, indent=2), encoding="utf-8")
        return len(merged), dst

    def save_active_profile_settings(
        self,
        profile: RpcProfile,
        *,
        rpc_url: str,
        explorer_url: str,
        chain_id: int,
        node_start_cmd: str,
        node_datadir: str,
    ) -> RpcProfile:
        profile.rpc_url = validate_rpc_url(rpc_url)
        profile.explorer_base_url = validate_explorer_base_url(explorer_url)
        profile.chain_id_expected = int(chain_id)
        profile.node_datadir = str(node_datadir).strip() or profile.node_datadir
        tokens = [token for token in str(node_start_cmd).strip().split() if token]
        profile.node_start_cmd = tokens or ["animica", "node", "start"]
        return profile

    def _load_wallet_store_raw(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"wallets": []}
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {"wallets": payload}
        return {"wallets": []}

    def _load_wallet_store(self, path: Path) -> list[dict[str, Any]]:
        payload = self._load_wallet_store_raw(path)
        wallets = payload.get("wallets", payload.get("accounts", []))
        if isinstance(wallets, list):
            return [row for row in wallets if isinstance(row, dict)]
        return []

    def _wallet_count(self, path: Path) -> int:
        return len(self._load_wallet_store(path))
