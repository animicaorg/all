"""JSON configuration storage with per-OS app-data directory."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from animica_studio.util.paths import config_file, app_data_dir

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Timeouts:
    connect_timeout_ms: int = 3000
    request_timeout_ms: int = 15000


@dataclass
class NodeConfig:
    """Local node process configuration."""

    start_cmd: list[str] = field(default_factory=lambda: ["animica", "node", "start"])
    rpc_local_url: str = "http://127.0.0.1:8545/rpc"
    log_file_name: str = "node.log"
    pid_file_name: str = "node.pid"


@dataclass
class CliConfig:
    """CLI tooling configuration."""

    animica_bin: str = "animica"


@dataclass
class Profile:
    name: str = "Mainnet"
    rpc_url: str = "https://mainnet.animica.org/rpc"
    chain_id_expected: int = 1
    timeouts: Timeouts = field(default_factory=Timeouts)
    node: NodeConfig = field(default_factory=NodeConfig)
    cli: CliConfig = field(default_factory=CliConfig)


@dataclass
class Config:
    active_profile: str = "Mainnet"
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])

    # ---------------------------------------------------------------------------
    # New fields: wizard + RpcProfile system
    # ---------------------------------------------------------------------------
    first_run_completed: bool = False
    active_profile_id: str | None = None
    rpc_profiles: list[dict[str, Any]] = field(default_factory=list)

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


def _node_config_from_dict(d: dict[str, Any]) -> NodeConfig:
    raw_cmd = d.get("start_cmd", ["animica", "node", "start"])
    start_cmd = list(raw_cmd) if isinstance(raw_cmd, list) else ["animica", "node", "start"]
    return NodeConfig(
        start_cmd=start_cmd,
        rpc_local_url=str(d.get("rpc_local_url", "http://127.0.0.1:8545/rpc")),
        log_file_name=str(d.get("log_file_name", "node.log")),
        pid_file_name=str(d.get("pid_file_name", "node.pid")),
    )


def _cli_config_from_dict(d: dict[str, Any]) -> CliConfig:
    return CliConfig(
        animica_bin=str(d.get("animica_bin", "animica")),
    )


def _profile_from_dict(d: dict[str, Any]) -> Profile:
    timeouts_dict = d.get("timeouts", {})
    timeouts = Timeouts(
        connect_timeout_ms=int(timeouts_dict.get("connect_timeout_ms", 3000)),
        request_timeout_ms=int(timeouts_dict.get("request_timeout_ms", 15000)),
    )
    node = _node_config_from_dict(d.get("node", {}))
    cli = _cli_config_from_dict(d.get("cli", {}))
    return Profile(
        name=str(d.get("name", "Mainnet")),
        rpc_url=str(d.get("rpc_url", "https://mainnet.animica.org/rpc")),
        chain_id_expected=int(d.get("chain_id_expected", 1)),
        timeouts=timeouts,
        node=node,
        cli=cli,
    )


def _config_from_dict(d: dict[str, Any]) -> Config:
    profiles_raw = d.get("profiles", [])
    profiles = [_profile_from_dict(p) for p in profiles_raw] if profiles_raw else [Profile()]
    return Config(
        active_profile=str(d.get("active_profile", "Mainnet")),
        profiles=profiles,
        first_run_completed=bool(d.get("first_run_completed", False)),
        active_profile_id=d.get("active_profile_id") or None,
        rpc_profiles=list(d.get("rpc_profiles") or []),
    )


def _config_to_dict(cfg: Config) -> dict[str, Any]:
    d = asdict(cfg)
    # rpc_profiles is already a list[dict] — asdict wraps it as-is
    return d


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------


def _migrate_legacy_profiles(cfg: Config) -> bool:
    """Migrate old-style ``profiles`` (list[Profile]) into ``rpc_profiles`` (list[RpcProfile]).

    Returns ``True`` if migration was performed and the config should be saved.
    """
    from animica_studio.models.profile_models import RpcProfile, ProfileType  # noqa: PLC0415

    if cfg.rpc_profiles:
        # Already has new-style profiles; nothing to migrate
        return False

    legacy = cfg.profiles
    if not legacy:
        return False

    migrated: list[dict] = []
    for lp in legacy:
        # Determine profile type from rpc_url
        is_local = "127.0.0.1" in lp.rpc_url or "localhost" in lp.rpc_url

        node_datadir: str | None = None
        if is_local:
            # Default node datadir to <app_data_dir>/node
            node_datadir = str(app_data_dir() / "node")

        ptype = ProfileType.LOCAL_NODE if is_local else ProfileType.REMOTE_RPC

        rp = RpcProfile(
            id=str(uuid.uuid4()),
            name=lp.name,
            type=ptype,
            rpc_url=lp.rpc_url,
            chain_id_expected=lp.chain_id_expected,
            node_start_cmd=list(lp.node.start_cmd) if is_local else None,
            node_datadir=node_datadir,
            node_rpc_url=lp.node.rpc_local_url if is_local else None,
        )
        migrated.append(rp.to_dict())

    cfg.rpc_profiles = migrated
    if migrated:
        cfg.active_profile_id = migrated[0]["id"]

    log.info("Migrated %d legacy profile(s) to rpc_profiles", len(migrated))
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config() -> Config:
    """Load and return the :class:`Config` from disk.

    * Creates a default config if the file is absent.
    * Recovers to defaults (and backs up the corrupt file) on JSON parse errors.
    * Migrates legacy ``profiles`` data to ``rpc_profiles`` on first load.
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
        # Run migration silently; save if changes were made
        if _migrate_legacy_profiles(cfg):
            save_config(cfg)
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
