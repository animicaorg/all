"""
execution.config — runtime configuration for the Animica execution layer.

This module centralizes knobs for:
  • Gas table source (resolved JSON of op costs for the Python VM)
  • Feature flags (strict VM mode, optimistic scheduler, optional DA/capability bridges)
  • Limits (tx/code sizes, logs, event bounds, refund caps)

Configuration may be provided via environment variables. Safe defaults are chosen so a
local developer run works out of the box.

Environment variables (all optional):
  ANIMICA_EXEC_GAS_TABLE           -> path to gas table JSON (default: vm_py/gas_table.json)
  ANIMICA_EXEC_STRICT              -> 0/1/true/false (default: 1)
  ANIMICA_EXEC_OPTIMISTIC          -> enable optimistic-parallel prototype (default: 0)
  ANIMICA_EXEC_ENABLE_DA           -> enable DA adapters/caps checks (default: 0)
  ANIMICA_EXEC_ENABLE_VM_ENTRY     -> enable VM entry adapter (default: 1)

  ANIMICA_EXEC_MAX_TX_BYTES        -> e.g. "256KiB", "131072" (default: 128KiB)
  ANIMICA_EXEC_MAX_CODE_BYTES      -> e.g. "64KiB" (default: 64KiB)
  ANIMICA_EXEC_MAX_LOGS_PER_TX     -> integer (default: 128)
  ANIMICA_EXEC_MAX_EVENT_TOPICS    -> integer (default: 4)
  ANIMICA_EXEC_MAX_EVENT_DATA      -> e.g. "64KiB" (default: 64KiB)
  ANIMICA_EXEC_MAX_ACCESSLIST_LEN  -> integer (default: 1024)
  ANIMICA_EXEC_REFUND_RATIO_CAP    -> float in [0,1] (default: 0.20)

Programmatic usage:
    from execution.config import get_config
    cfg = get_config()
    if cfg.features.optimistic_scheduler:
        ...

Note: This module does not perform any I/O beyond reading env vars and probing for a
default gas-table path. It does not parse or validate the gas JSON itself.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple, Union

# ----------------------------- helpers -------------------------------------


_BOOL_TRUE = {"1", "true", "t", "yes", "y", "on"}
_BOOL_FALSE = {"0", "false", "f", "no", "n", "off"}


def _bool_env(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in _BOOL_TRUE:
        return True
    if v in _BOOL_FALSE:
        return False
    # Be forgiving: non-empty → True, empty → default
    return bool(v) if v != "" else default


_SIZE_RE = re.compile(r"^\s*(\d+)\s*([kKmMgG]i?[bB])?\s*$")


def _parse_size_bytes(s: Union[str, int, float]) -> int:
    """
    Parse human-friendly byte sizes:
      "256KiB", "64KB", "1MiB", "131072", 131072 -> bytes (int)

    Units:
      B, KB, KiB, MB, MiB, GB, GiB (case-insensitive)
    """
    if isinstance(s, (int, float)):
        n = int(s)
        if n < 0:
            raise ValueError("size must be non-negative")
        return n

    m = _SIZE_RE.match(str(s))
    if not m:
        raise ValueError(f"invalid size: {s!r}")

    num = int(m.group(1))
    unit = (m.group(2) or "B").lower()

    mult = 1
    if unit in ("b",):
        mult = 1
    elif unit in ("kb",):
        mult = 1000
    elif unit in ("kib",):
        mult = 1024
    elif unit in ("mb",):
        mult = 1000**2
    elif unit in ("mib",):
        mult = 1024**2
    elif unit in ("gb",):
        mult = 1000**3
    elif unit in ("gib",):
        mult = 1024**3
    else:
        raise ValueError(f"unknown size unit: {unit}")

    out = num * mult
    if out < 0:
        raise OverflowError("size overflowed integer range")
    return out


def _first_existing(paths: Tuple[Path, ...]) -> Optional[Path]:
    for p in paths:
        try:
            if p.is_file():
                return p
        except Exception:
            # Permissions or odd filesystems shouldn't crash config resolution
            continue
    return None


def _resolve_default_gas_table_path(env: Mapping[str, str]) -> Path:
    """
    Heuristics to find a reasonable default gas table without hard-coding a repo layout.
    Order:
      1) ANIMICA_EXEC_GAS_TABLE if present
      2) ./vm_py/gas_table.json
      3) <this_file>/../../vm_py/gas_table.json
      4) fallback to a non-existent path 'vm_py/gas_table.json' (caller may handle)
    """
    if "ANIMICA_EXEC_GAS_TABLE" in env:
        return Path(env["ANIMICA_EXEC_GAS_TABLE"]).expanduser()

    cwd = Path.cwd()
    here = Path(__file__).resolve().parent

    candidates = (
        cwd / "vm_py" / "gas_table.json",
        here.parent / "vm_py" / "gas_table.json",
    )
    found = _first_existing(candidates)
    return found or Path("vm_py/gas_table.json")


# ------------------------------ dataclasses ---------------------------------


@dataclass(frozen=True)
class FeatureFlags:
    strict_vm: bool = True
    optimistic_scheduler: bool = False
    enable_da_caps: bool = False
    enable_vm_entry: bool = True


@dataclass(frozen=True)
class Limits:
    max_tx_size_bytes: int = 128 * 1024  # 128 KiB
    max_code_size_bytes: int = 64 * 1024  # 64 KiB
    max_logs_per_tx: int = 128
    max_event_topics: int = 4
    max_event_data_bytes: int = 64 * 1024  # 64 KiB
    max_access_list_len: int = 1024
    refund_ratio_cap: float = 0.20  # ≤ 20% of gas used


@dataclass(frozen=True)
class ExecutionConfig:
    gas_table_path: Path
    features: FeatureFlags
    limits: Limits

    def to_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d["gas_table_path"] = str(self.gas_table_path)
        return d


# ------------------------------ loader --------------------------------------


def _validate_limits(l: Limits) -> Limits:
    if l.max_tx_size_bytes <= 0:
        raise ValueError("max_tx_size_bytes must be > 0")
    if l.max_code_size_bytes <= 0:
        raise ValueError("max_code_size_bytes must be > 0")
    if l.max_logs_per_tx < 0:
        raise ValueError("max_logs_per_tx must be ≥ 0")
    if not (0.0 <= l.refund_ratio_cap <= 1.0):
        raise ValueError("refund_ratio_cap must be in [0,1]")
    if l.max_event_topics < 0:
        raise ValueError("max_event_topics must be ≥ 0")
    if l.max_event_data_bytes < 0:
        raise ValueError("max_event_data_bytes must be ≥ 0")
    if l.max_access_list_len < 0:
        raise ValueError("max_access_list_len must be ≥ 0")
    return l


def load_config(
    env: Optional[Mapping[str, str]] = None,
    *,
    overrides: Optional[Mapping[str, Union[str, int, float, bool, Path]]] = None,
) -> ExecutionConfig:
    """
    Build an ExecutionConfig from environment and optional overrides.

    Args:
        env: mapping to read variables from (default: os.environ)
        overrides: explicit field overrides; keys support:
          'gas_table_path', 'strict_vm', 'optimistic_scheduler', 'enable_da_caps',
          'enable_vm_entry', 'max_tx_size_bytes', 'max_code_size_bytes', 'max_logs_per_tx',
          'max_event_topics', 'max_event_data_bytes', 'max_access_list_len',
          'refund_ratio_cap'
    """
    env = os.environ if env is None else env
    overrides = dict(overrides or {})

    gas_table_path: Path = Path(
        overrides.get("gas_table_path", _resolve_default_gas_table_path(env))
    ).expanduser()

    features = FeatureFlags(
        strict_vm=_bool_env(
            env.get("ANIMICA_EXEC_STRICT"), bool(overrides.get("strict_vm", True))
        ),
        optimistic_scheduler=_bool_env(
            env.get("ANIMICA_EXEC_OPTIMISTIC"),
            bool(overrides.get("optimistic_scheduler", False)),
        ),
        enable_da_caps=_bool_env(
            env.get("ANIMICA_EXEC_ENABLE_DA"),
            bool(overrides.get("enable_da_caps", False)),
        ),
        enable_vm_entry=_bool_env(
            env.get("ANIMICA_EXEC_ENABLE_VM_ENTRY"),
            bool(overrides.get("enable_vm_entry", True)),
        ),
    )

    limits = Limits(
        max_tx_size_bytes=_parse_size_bytes(
            overrides.get(
                "max_tx_size_bytes", env.get("ANIMICA_EXEC_MAX_TX_BYTES", 128 * 1024)
            )
        ),
        max_code_size_bytes=_parse_size_bytes(
            overrides.get(
                "max_code_size_bytes", env.get("ANIMICA_EXEC_MAX_CODE_BYTES", 64 * 1024)
            )
        ),
        max_logs_per_tx=int(
            overrides.get(
                "max_logs_per_tx", env.get("ANIMICA_EXEC_MAX_LOGS_PER_TX", 128)
            )
        ),
        max_event_topics=int(
            overrides.get(
                "max_event_topics", env.get("ANIMICA_EXEC_MAX_EVENT_TOPICS", 4)
            )
        ),
        max_event_data_bytes=_parse_size_bytes(
            overrides.get(
                "max_event_data_bytes",
                env.get("ANIMICA_EXEC_MAX_EVENT_DATA", 64 * 1024),
            )
        ),
        max_access_list_len=int(
            overrides.get(
                "max_access_list_len", env.get("ANIMICA_EXEC_MAX_ACCESSLIST_LEN", 1024)
            )
        ),
        refund_ratio_cap=float(
            overrides.get(
                "refund_ratio_cap", env.get("ANIMICA_EXEC_REFUND_RATIO_CAP", 0.20)
            )
        ),
    )
    limits = _validate_limits(limits)

    return ExecutionConfig(
        gas_table_path=gas_table_path, features=features, limits=limits
    )


@lru_cache(maxsize=1)
def get_config() -> ExecutionConfig:
    """
    Cached global config. Suitable for application bootstraps and module-level consumers.
    """
    return load_config()


# ----------------------------- pretty-print ---------------------------------


def _fmt_bytes(n: int) -> str:
    for unit, div in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if n >= div and n % div == 0:
            return f"{n // div}{unit}"
    return f"{n}B"


def summary(cfg: Optional[ExecutionConfig] = None) -> str:
    """
    Return a human-friendly one-line summary of the most important execution knobs.
    """
    cfg = cfg or get_config()
    f = cfg.features
    l = cfg.limits
    return (
        "exec{"
        f"gas={cfg.gas_table_path}, "
        f"strict={int(f.strict_vm)}, opt={int(f.optimistic_scheduler)}, da={int(f.enable_da_caps)}, "
        f"vm_entry={int(f.enable_vm_entry)}, "
        f"tx={_fmt_bytes(l.max_tx_size_bytes)}, code={_fmt_bytes(l.max_code_size_bytes)}, "
        f"logs={l.max_logs_per_tx}, topics={l.max_event_topics}, event={_fmt_bytes(l.max_event_data_bytes)}, "
        f"alist={l.max_access_list_len}, refund_cap={l.refund_ratio_cap:.2f}"
        "}"
    )


__all__ = [
    "FeatureFlags",
    "Limits",
    "ExecutionConfig",
    "load_config",
    "get_config",
    "summary",
]
