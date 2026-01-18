from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _parse_csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class SnapshotPolicy:
    auto_enabled: bool
    manifest_urls: list[str] = field(default_factory=list)
    fallback_urls: list[str] = field(default_factory=list)
    trusted_pubkeys: list[str] = field(default_factory=list)
    cooldown_secs: int = 1800
    min_advance_blocks: int = 500
    require_signature: bool = False
    network: Optional[str] = None

    @classmethod
    def from_env(cls, *, chain_id: Optional[int] = None) -> "SnapshotPolicy":
        network = (os.environ.get("ANIMICA_NETWORK") or "").strip().lower() or None
        if network is None and chain_id is not None:
            if chain_id == 1:
                network = "mainnet"
            elif chain_id == 2:
                network = "testnet"
            elif chain_id == 1337:
                network = "devnet"

        auto_raw = os.environ.get("ANIMICA_SNAPSHOT_AUTO")
        if auto_raw is None:
            auto_enabled = network in {"mainnet", "testnet"}
        else:
            auto_enabled = auto_raw.strip().lower() in {"1", "true", "yes", "on"}

        manifest_urls = _parse_csv(os.environ.get("ANIMICA_SNAPSHOT_MANIFEST_URLS"))
        if not manifest_urls and network == "mainnet":
            manifest_urls = _parse_csv(
                os.environ.get("ANIMICA_SNAPSHOT_MANIFEST_URL_MAINNET")
            )
        if not manifest_urls and network == "testnet":
            manifest_urls = _parse_csv(
                os.environ.get("ANIMICA_SNAPSHOT_MANIFEST_URL_TESTNET")
            )
        fallback_urls = _parse_csv(
            os.environ.get("ANIMICA_SNAPSHOT_MANIFEST_URL_FALLBACKS")
        )
        trusted_pubkeys = _parse_csv(
            os.environ.get("ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS")
        )
        require_signature = os.environ.get(
            "ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        try:
            cooldown_secs = int(os.environ.get("ANIMICA_SNAPSHOT_COOLDOWN_SECS", "1800"))
        except ValueError:
            cooldown_secs = 1800
        try:
            min_advance_blocks = int(
                os.environ.get("ANIMICA_SNAPSHOT_MIN_ADVANCE_BLOCKS", "500")
            )
        except ValueError:
            min_advance_blocks = 500

        return cls(
            auto_enabled=auto_enabled,
            manifest_urls=manifest_urls,
            fallback_urls=fallback_urls,
            trusted_pubkeys=trusted_pubkeys,
            cooldown_secs=cooldown_secs,
            min_advance_blocks=min_advance_blocks,
            require_signature=require_signature,
            network=network,
        )
