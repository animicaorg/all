"""
vm_py.config — runtime feature flags, gas-table location, and numeric caps.

This module centralizes configuration for the deterministic Python VM. It has
NO third-party deps and is safe to import very early.

Configuration precedence:
  1) Environment variables (ANIMICA_VM_* / VM_PY_*)
  2) Hardcoded safe defaults below

Key env vars (case-insensitive where boolean):
  - ANIMICA_VM_STRICT                (bool)   default: true
  - ANIMICA_VM_GAS_TABLE             (path)   default: packaged vm_py/gas_table.json
  - ANIMICA_VM_MAX_OPS               (int)    default: 2_000_000
  - ANIMICA_VM_MAX_CALL_DEPTH        (int)    default: 64
  - ANIMICA_VM_MAX_CODE_BYTES        (int)    default: 512_000
  - ANIMICA_VM_MAX_ABI_BYTES         (int)    default: 256_000
  - ANIMICA_VM_MAX_RETURN_BYTES      (int)    default: 256_000
  - ANIMICA_VM_MAX_EVENT_ARGS_BYTES  (int)    default: 256_000
  - ANIMICA_VM_MAX_STORAGE_KEY_BYTES (int)    default: 64
  - ANIMICA_VM_MAX_STORAGE_VAL_BYTES (int)    default: 131_072   (128 KiB)
  - ANIMICA_VM_MAX_LOGS_PER_TX       (int)    default: 1024
  - ANIMICA_VM_MAX_SYSCALL_BYTES     (int)    default: 131_072   (128 KiB)
  - ANIMICA_VM_ENABLE_EXPERIMENTAL   (bool)   default: false

These caps are intentionally conservative and should be reviewed alongside
spec/params.yaml and spec/opcodes_vm_py.yaml.

Usage:
    from vm_py.config import load_config
    CFG = load_config()
    if CFG.strict_mode: ...
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Optional, Dict, Any
import os


# ----------------------------- helpers ---------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        # Secondary prefix (legacy)
        raw = os.getenv(name.replace("ANIMICA_", "VM_").replace("VM_", "VM_PY_"))
        if raw is None:
            return default
    val = raw.strip().lower()
    return val in ("1", "true", "t", "yes", "y", "on")


def _env_int(name: str, default: int, *, min_v: int, max_v: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        raw = os.getenv(name.replace("ANIMICA_", "VM_").replace("VM_", "VM_PY_"))
        if raw is None:
            return default
    try:
        v = int(raw, 0)
    except Exception:
        return default
    if v < min_v:
        return min_v
    if v > max_v:
        return max_v
    return v


def _env_path(name: str) -> Optional[Path]:
    raw = os.getenv(name) or os.getenv(name.replace("ANIMICA_", "VM_").replace("VM_", "VM_PY_"))
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def _packaged_gas_table() -> Optional[Path]:
    """
    Return a filesystem Path to the packaged vm_py/gas_table.json if present.
    Uses importlib.resources so it works from wheels/zip imports.
    """
    try:
        # Prefer materialized files() Path (Python >=3.9)
        pkg_root = importlib_resources.files(__package__.split(".")[0])
        candidate = pkg_root / "gas_table.json"
        if candidate.is_file():
            return Path(str(candidate))
        # Fallback: extract to a temp file handle
        if importlib_resources.is_resource(__package__.split(".")[0], "gas_table.json"):  # type: ignore[arg-type]
            with importlib_resources.as_file(candidate) as tmp:
                return Path(tmp)
    except Exception:
        pass
    return None


# ------------------------------- config --------------------------------------


@dataclass(frozen=True)
class VMConfig:
    # Feature flags
    strict_mode: bool
    enable_experimental: bool

    # Gas table location (JSON mapping of opcode -> gas)
    gas_table_path: Optional[Path]

    # Numeric caps / limits (enforced by validator/compiler/runtime)
    max_ops_per_call: int
    max_call_depth: int
    max_code_bytes: int
    max_abi_payload_bytes: int
    max_return_bytes: int
    max_event_args_bytes: int
    max_storage_key_bytes: int
    max_storage_value_bytes: int
    max_logs_per_tx: int
    max_syscall_payload_bytes: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strict_mode": self.strict_mode,
            "enable_experimental": self.enable_experimental,
            "gas_table_path": str(self.gas_table_path) if self.gas_table_path else None,
            "max_ops_per_call": self.max_ops_per_call,
            "max_call_depth": self.max_call_depth,
            "max_code_bytes": self.max_code_bytes,
            "max_abi_payload_bytes": self.max_abi_payload_bytes,
            "max_return_bytes": self.max_return_bytes,
            "max_event_args_bytes": self.max_event_args_bytes,
            "max_storage_key_bytes": self.max_storage_key_bytes,
            "max_storage_value_bytes": self.max_storage_value_bytes,
            "max_logs_per_tx": self.max_logs_per_tx,
            "max_syscall_payload_bytes": self.max_syscall_payload_bytes,
        }


@lru_cache(maxsize=1)
def load_config() -> VMConfig:
    """
    Build and cache a VMConfig from environment + safe defaults.
    """
    # Defaults (align with execution/specs/* where applicable)
    strict = _env_bool("ANIMICA_VM_STRICT", True)
    experimental = _env_bool("ANIMICA_VM_ENABLE_EXPERIMENTAL", False)

    # Gas table path: env override → packaged default → None (caller handles)
    gas_env = _env_path("ANIMICA_VM_GAS_TABLE")
    gas_path = gas_env if gas_env and gas_env.exists() else _packaged_gas_table()

    cfg = VMConfig(
        strict_mode=strict,
        enable_experimental=experimental,
        gas_table_path=gas_path,
        max_ops_per_call=_env_int("ANIMICA_VM_MAX_OPS", 2_000_000, min_v=10_000, max_v=50_000_000),
        max_call_depth=_env_int("ANIMICA_VM_MAX_CALL_DEPTH", 64, min_v=8, max_v=1024),
        max_code_bytes=_env_int("ANIMICA_VM_MAX_CODE_BYTES", 512_000, min_v=4_096, max_v=8_388_608),
        max_abi_payload_bytes=_env_int("ANIMICA_VM_MAX_ABI_BYTES", 256_000, min_v=1_024, max_v=8_388_608),
        max_return_bytes=_env_int("ANIMICA_VM_MAX_RETURN_BYTES", 256_000, min_v=1_024, max_v=8_388_608),
        max_event_args_bytes=_env_int("ANIMICA_VM_MAX_EVENT_ARGS_BYTES", 256_000, min_v=1_024, max_v=8_388_608),
        max_storage_key_bytes=_env_int("ANIMICA_VM_MAX_STORAGE_KEY_BYTES", 64, min_v=1, max_v=256),
        max_storage_value_bytes=_env_int("ANIMICA_VM_MAX_STORAGE_VAL_BYTES", 131_072, min_v=32, max_v=1_048_576),
        max_logs_per_tx=_env_int("ANIMICA_VM_MAX_LOGS_PER_TX", 1024, min_v=1, max_v=10_000),
        max_syscall_payload_bytes=_env_int("ANIMICA_VM_MAX_SYSCALL_BYTES", 131_072, min_v=1_024, max_v=1_048_576),
    )
    return cfg


# Eagerly construct a module-level singleton for convenience, but keep load_config()
# as the canonical accessor (cached).
CFG: VMConfig = load_config()

__all__ = ["VMConfig", "load_config", "CFG"]
