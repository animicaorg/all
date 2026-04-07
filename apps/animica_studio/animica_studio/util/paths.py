"""Per-OS application-data directory helpers."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

_APP_NAME_LINUX = "animica-studio"
_APP_NAME_MAC = "Animica Studio"
_APP_NAME_WIN = "Animica Studio"
_APP_DATA_DIR_ENV = "ANIMICA_STUDIO_APP_DATA_DIR"
_WALLETS_FILE_ENV = "ANIMICA_WALLETS_FILE"

log = logging.getLogger(__name__)


def _ensure_writable_dir(path: Path) -> Path:
    path = path.expanduser()
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        probe.unlink(missing_ok=True)
        raise
    return path


def _fallback_app_data_dir() -> Path:
    runtime_root = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    candidates: list[Path] = []
    if runtime_root:
        candidates.append(Path(runtime_root) / _APP_NAME_LINUX)
    candidates.append(Path(tempfile.gettempdir()) / _APP_NAME_LINUX)

    last_error: OSError | None = None
    for candidate in candidates:
        try:
            return _ensure_writable_dir(candidate)
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise OSError("No writable fallback application data directory available")


def app_data_dir() -> Path:
    """Return the per-OS application-data directory and ensure it exists.

    * Linux  : ``~/.local/share/animica-studio``
    * macOS  : ``~/Library/Application Support/Animica Studio``
    * Windows: ``%APPDATA%\\Animica Studio``
    """
    override = os.environ.get(_APP_DATA_DIR_ENV, "").strip()
    if override:
        return _ensure_writable_dir(Path(override))

    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        path = base / _APP_NAME_WIN
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / _APP_NAME_MAC
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        path = base / _APP_NAME_LINUX

    try:
        return _ensure_writable_dir(path)
    except OSError as exc:
        fallback = _fallback_app_data_dir()
        log.warning(
            "App data directory %s is not writable (%s); falling back to %s",
            path,
            exc,
            fallback,
        )
        return fallback


def logs_dir() -> Path:
    """Return the log directory (inside the app-data dir) and ensure it exists."""
    d = app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    """Return the full path to the JSON config file."""
    return app_data_dir() / "config.json"


def default_chain_data_dir(chain_id: int) -> Path:
    """Return the canonical chain data directory for *chain_id*.

    Format: ``~/.animica/chain-<chain_id>`` for the current OS user.
    """
    return Path.home() / ".animica" / f"chain-{int(chain_id)}"


def default_da_contrib_dir() -> Path:
    """Return the default Studio-side DA contribution directory."""
    return Path.home() / ".animica" / "da"


def animica_wallets_file() -> Path:
    """Return the effective wallets.json path respected by the CLI and Studio."""
    override = os.environ.get(_WALLETS_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".animica" / "wallets.json"


def running_as_root() -> bool:
    """Return ``True`` when running as root on POSIX systems."""
    if os.name != "posix":
        return False
    return hasattr(os, "geteuid") and os.geteuid() == 0
