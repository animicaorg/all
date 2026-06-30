"""
animica.cli.app_config
======================

The single top-level Animica config file (``~/.animica/config.toml``) introduced
in 5.2.0. It holds cross-command preferences that the ``animica ai`` namespace and
other commands read/write — e.g. the user's default AI mode, preferred model,
payout wallet, spending caps, and which workloads ``animica up`` should run.

This is intentionally small and dependency-free: TOML is read with the stdlib
``tomllib`` (Python 3.11+) and written with a tiny hand-rolled serializer (the
config is a flat set of tables of scalars/lists), so the base ``pip install
animica`` stays light. It mirrors the proven get/set/load/save shape of
``cli/state.py`` but persists TOML at the documented ``~/.animica/config.toml``
location (the path ``main.py`` always advertised but never actually loaded).

Resolution: an explicit ``$ANIMICA_CONFIG`` path wins; otherwise
``$ANIMICA_HOME/config.toml`` (default ``~/.animica/config.toml``). Reads never
raise on a missing/!malformed file — they return ``{}`` so commands degrade to
defaults. Writes create the file 0o600 (it can hold a payout address + caps).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .paths import ensure_file_dir, get_animica_home, secure_file

try:  # Python 3.11+
    import tomllib  # type: ignore
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore

CONFIG_BASENAME = "config.toml"


def config_path() -> Path:
    """Resolve the config file path: $ANIMICA_CONFIG > $ANIMICA_HOME/config.toml."""
    explicit = os.environ.get("ANIMICA_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    return get_animica_home() / CONFIG_BASENAME


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load the config as a dict. Returns {} if the file is missing/unreadable."""
    p = path or config_path()
    try:
        text = p.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    if tomllib is None:  # pragma: no cover — base requires 3.10; TOML read needs 3.11+
        return {}
    try:
        return dict(tomllib.loads(text))
    except Exception:
        return {}


def _toml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    # Fallback: stringify unknown types.
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_toml(data: dict[str, Any]) -> str:
    """Serialize a dict of scalars/lists/tables to TOML (one nesting level of tables).

    Top-level scalar/list keys are emitted first, then ``[table]`` sections. This
    covers the config shape we use; nested-table values are skipped rather than
    mis-serialized (callers keep the file flat-with-tables on purpose).
    """
    lines: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    for k, v in scalars.items():
        lines.append(f"{k} = {_toml_scalar(v)}")
    for name, tbl in tables.items():
        lines.append("")
        lines.append(f"[{name}]")
        for k, v in tbl.items():
            if isinstance(v, dict):
                continue  # one level only
            lines.append(f"{k} = {_toml_scalar(v)}")
    return "\n".join(lines).strip() + "\n"


def save_config(data: dict[str, Any], path: Path | None = None) -> Path:
    """Write the config as TOML (0o600 — may hold a payout address + spend caps)."""
    p = path or config_path()
    ensure_file_dir(p)
    header = (
        "# Animica config (~/.animica/config.toml). Written by `animica ai setup`.\n"
        "# Cross-command preferences: AI mode, default model, payout wallet, spend caps.\n\n"
    )
    p.write_text(header + dump_toml(data), encoding="utf-8")
    secure_file(p, 0o600)
    return p


def get(key: str, default: Any = None, *, section: str | None = None) -> Any:
    """Get a value, optionally from a ``[section]`` table. Returns ``default`` if absent."""
    cfg = load_config()
    if section is not None:
        return dict(cfg.get(section) or {}).get(key, default)
    return cfg.get(key, default)


def set_value(key: str, value: Any, *, section: str | None = None) -> Path:
    """Set a single value (optionally inside a ``[section]`` table) and persist."""
    cfg = load_config()
    if section is not None:
        tbl = dict(cfg.get(section) or {})
        tbl[key] = value
        cfg[section] = tbl
    else:
        cfg[key] = value
    return save_config(cfg)
