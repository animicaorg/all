"""JSON configuration storage with per-OS app-data directory."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from animica_studio.util.paths import config_file

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Timeouts:
    connect_timeout_ms: int = 3000
    request_timeout_ms: int = 15000


@dataclass
class Profile:
    name: str = "Mainnet"
    rpc_url: str = "https://mainnet.animica.org/rpc"
    chain_id_expected: int = 1
    timeouts: Timeouts = field(default_factory=Timeouts)


@dataclass
class Config:
    active_profile: str = "Mainnet"
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])

    # ---------------------------------------------------------------------------
    # Convenience helpers
    # ---------------------------------------------------------------------------

    def get_active_profile(self) -> Profile:
        """Return the active :class:`Profile`, falling back to the first one."""
        for p in self.profiles:
            if p.name == self.active_profile:
                return p
        if self.profiles:
            return self.profiles[0]
        default = Profile()
        self.profiles.append(default)
        return default


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _profile_from_dict(d: dict[str, Any]) -> Profile:
    timeouts_dict = d.get("timeouts", {})
    timeouts = Timeouts(
        connect_timeout_ms=int(timeouts_dict.get("connect_timeout_ms", 3000)),
        request_timeout_ms=int(timeouts_dict.get("request_timeout_ms", 15000)),
    )
    return Profile(
        name=str(d.get("name", "Mainnet")),
        rpc_url=str(d.get("rpc_url", "https://mainnet.animica.org/rpc")),
        chain_id_expected=int(d.get("chain_id_expected", 1)),
        timeouts=timeouts,
    )


def _config_from_dict(d: dict[str, Any]) -> Config:
    profiles_raw = d.get("profiles", [])
    profiles = [_profile_from_dict(p) for p in profiles_raw] if profiles_raw else [Profile()]
    return Config(
        active_profile=str(d.get("active_profile", "Mainnet")),
        profiles=profiles,
    )


def _config_to_dict(cfg: Config) -> dict[str, Any]:
    return asdict(cfg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config() -> Config:
    """Load and return the :class:`Config` from disk.

    * Creates a default config if the file is absent.
    * Recovers to defaults (and backs up the corrupt file) on JSON parse errors.
    """
    path: Path = config_file()

    if not path.exists():
        log.info("Config file not found — creating default at %s", path)
        cfg = Config()
        save_config(cfg)
        return cfg

    try:
        raw = path.read_text(encoding="utf-8")
        data: Any = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Config root must be a JSON object")
        cfg = _config_from_dict(data)
        log.debug("Config loaded from %s", path)
        return cfg
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load config (%s) — reverting to defaults", exc)
        _backup_corrupt_config(path)
        cfg = Config()
        save_config(cfg)
        return cfg


def save_config(cfg: Config) -> None:
    """Persist *cfg* to disk as pretty-printed JSON."""
    path: Path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(_config_to_dict(cfg), indent=2), encoding="utf-8")
        tmp.replace(path)
        log.debug("Config saved to %s", path)
    except OSError as exc:
        log.error("Could not save config: %s", exc)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _backup_corrupt_config(path: Path) -> None:
    backup = path.with_suffix(".json.bak")
    try:
        shutil.copy2(path, backup)
        log.info("Backed up corrupt config to %s", backup)
    except OSError as exc:
        log.warning("Could not back up corrupt config: %s", exc)
