"""
execution.gas.table — load/resolve gas costs from spec/opcodes_vm_py.yaml + builtins.

Overview
--------
Gas costs are defined primarily in `spec/opcodes_vm_py.yaml`. This module loads
that file (if present), merges it with conservative in-code defaults, validates
the result, and exposes a small, typed API.

Schema (YAML)
-------------
meta:
  version: "v1"
  notes: "optional text"
opcodes:
  # VM core ops (IR-level)
  ADD: 3
  SUB: 3
  MUL: 5
  # ...
builtins:
  # stdlib/syscall-level prices (VM-visible helpers)
  keccak256: 30
  sha3_256: 20
  storage_get: 50
  storage_set: 200
  events_emit: 40
  blob_pin: 2000
  ai_enqueue: 5000
  quantum_enqueue: 5000
  zk_verify: 8000

You can also provide a JSON file with the same structure; the loader will infer
by extension if YAML libraries are unavailable.

Determinism notes
-----------------
* Unknown/negative/float costs are rejected.
* All keys are normalized to strings and merged with *last-wins* precedence:
  defaults < file contents < explicit overrides.
* The returned `GasTable` is immutable and stable to serialize.

Usage
-----
    from execution.gas.table import load_gas_table
    gas = load_gas_table()                 # auto-discover spec/opcodes_vm_py.yaml
    gas.cost("ADD")
    gas.builtin_cost("storage_set")
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import json

# Optional YAML support (graceful fallback to JSON-only)
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - environment without PyYAML
    yaml = None  # type: ignore

# Local helpers
from execution.types.gas import U256_MAX, is_u256


# ------------------------------ defaults -------------------------------------

_DEFAULT_META: Dict[str, Any] = {"version": "v1", "notes": "built-in defaults"}
_DEFAULT_OPCODES: Dict[str, int] = {
    # Arithmetic & logic (conservative dev defaults; tune via YAML)
    "NOP": 0,
    "ADD": 3,
    "SUB": 3,
    "MUL": 5,
    "DIV": 8,
    "MOD": 8,
    "NOT": 2,
    "AND": 3,
    "OR": 3,
    "XOR": 3,
    "SHL": 4,
    "SHR": 4,
    # Control flow
    "JUMP": 8,
    "JUMPI": 10,
    "CALL": 25,
    "RETURN": 5,
    "REVERT": 5,
    # Memory/stack
    "PUSH": 2,
    "POP": 2,
    "DUP": 2,
    "SWAP": 2,
    "LOAD": 8,
    "STORE": 20,
    # Crypto helpers (if exposed as ops in IR)
    "KECCAK": 24,
    "SHA3_256": 18,
    "SHA3_512": 24,
}

_DEFAULT_BUILTINS: Dict[str, int] = {
    # stdlib
    "keccak256": 24,
    "sha3_256": 18,
    "sha3_512": 24,
    "random_bytes": 10,  # deterministic PRNG in VM
    # storage/events
    "storage_get": 40,
    "storage_set": 180,
    "events_emit": 32,
    # treasury
    "treasury_balance": 8,
    "treasury_transfer": 200,
    # syscalls (capabilities)
    "blob_pin": 1500,
    "ai_enqueue": 5000,
    "quantum_enqueue": 5000,
    "read_result": 30,
    "zk_verify": 8000,
}


# ------------------------------ datatypes ------------------------------------

@dataclass(frozen=True)
class GasTable:
    """
    Immutable container of gas costs for VM opcodes and stdlib/syscall builtins.
    """
    meta: Tuple[Tuple[str, Any], ...]
    opcodes: Tuple[Tuple[str, int], ...]
    builtins: Tuple[Tuple[str, int], ...]


    # ---------- lookup ----------

    def cost(self, opcode: str) -> int:
        """Return gas cost for a VM opcode (case-sensitive key)."""
        d = dict(self.opcodes)
        try:
            return d[opcode]
        except KeyError as e:
            raise KeyError(f"unknown opcode: {opcode!r}") from e

    def builtin_cost(self, name: str) -> int:
        """Return gas cost for a builtin/stdlib call."""
        d = dict(self.builtins)
        try:
            return d[name]
        except KeyError as e:
            raise KeyError(f"unknown builtin: {name!r}") from e

    # ---------- conversions ----------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meta": dict(self.meta),
            "opcodes": dict(self.opcodes),
            "builtins": dict(self.builtins),
        }

    # ---------- construction ----------

    @staticmethod
    def _validate_costs(kind: str, table: Mapping[str, Any]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for k, v in table.items():
            if not isinstance(k, str):
                raise TypeError(f"{kind}: key must be str (got {type(k).__name__})")
            if isinstance(v, bool):
                raise TypeError(f"{kind}.{k}: cost must be int, not bool")
            if not isinstance(v, int):
                raise TypeError(f"{kind}.{k}: cost must be int")
            if v < 0:
                raise ValueError(f"{kind}.{k}: cost must be non-negative")
            if not is_u256(v):
                raise OverflowError(f"{kind}.{k}: cost must fit into u256 (<= {U256_MAX})")
            out[k] = int(v)
        # Canonicalize ordering
        return dict(sorted(out.items(), key=lambda kv: kv[0]))

    @classmethod
    def build(
        cls,
        *,
        meta: Mapping[str, Any],
        opcodes: Mapping[str, Any],
        builtins: Mapping[str, Any],
    ) -> "GasTable":
        meta_c = dict(sorted(((str(k), meta[k]) for k in meta.keys()), key=lambda kv: kv[0]))
        op_c = cls._validate_costs("opcodes", opcodes)
        bi_c = cls._validate_costs("builtins", builtins)
        return GasTable(
            meta=tuple(meta_c.items()),
            opcodes=tuple(op_c.items()),
            builtins=tuple(bi_c.items()),
        )


# ------------------------------ helpers --------------------------------------

def _merge_dicts(*dicts: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Merge multiple mappings with last-wins precedence. Keys are converted to str.
    """
    out: Dict[str, Any] = {}
    for d in dicts:
        for k, v in d.items():
            out[str(k)] = v
    return out


def _load_yaml_or_json(path: Path) -> Dict[str, Any]:
    data: Dict[str, Any]
    txt = path.read_text(encoding="utf-8")
    # Try YAML first if library present and extension hints YAML
    if yaml is not None and path.suffix.lower() in {".yaml", ".yml"}:
        loaded = yaml.safe_load(txt)  # type: ignore
        if not isinstance(loaded, dict):
            raise ValueError(f"{path.name}: root must be a mapping")
        data = loaded  # type: ignore[assignment]
    else:
        # Try JSON as fallback
        try:
            data = json.loads(txt)
        except Exception as e:
            if yaml is None:
                raise RuntimeError(
                    f"Cannot parse {path.name}: PyYAML not installed and JSON parse failed."
                ) from e
            # last resort: try YAML even if extension is unexpected
            loaded = yaml.safe_load(txt)  # type: ignore
            if not isinstance(loaded, dict):
                raise ValueError(f"{path.name}: root must be a mapping")
            data = loaded  # type: ignore[assignment]
    return data


def _discover_default_path(start: Optional[Path] = None) -> Optional[Path]:
    """
    Attempt to find 'spec/opcodes_vm_py.yaml' by walking up from `start` (or this file)
    to repo root. Returns None if not found.
    """
    here = start or Path(__file__).resolve()
    for p in [here] + list(here.parents):
        candidate = p.parent / "spec" / "opcodes_vm_py.yaml"
        if candidate.exists():
            return candidate
        # Also try from potential repo root
        root_candidate = p / "spec" / "opcodes_vm_py.yaml"
        if root_candidate.exists():
            return root_candidate
    return None


# ------------------------------ public API -----------------------------------

@lru_cache(maxsize=8)
def load_gas_table(
    path: Optional[str | Path] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> GasTable:
    """
    Load and validate a `GasTable`.

    Parameters
    ----------
    path:
        Path to a YAML/JSON file with `meta`, `opcodes`, and `builtins`. If None,
        the loader attempts to auto-discover `spec/opcodes_vm_py.yaml`. If not
        found, returns a table built purely from in-code defaults.
    overrides:
        Optional mapping providing partial overrides:
            { "opcodes": {...}, "builtins": {...}, "meta": {...} }
        Applied with highest precedence (last-wins).

    Returns
    -------
    GasTable (immutable, hashable by identity due to lru_cache).
    """
    meta = dict(_DEFAULT_META)
    opcodes = dict(_DEFAULT_OPCODES)
    builtins = dict(_DEFAULT_BUILTINS)

    resolved_path: Optional[Path] = Path(path) if path is not None else _discover_default_path()
    if resolved_path and resolved_path.exists():
        data = _load_yaml_or_json(resolved_path)
        file_meta = dict(data.get("meta") or {})
        file_opcodes = dict(data.get("opcodes") or {})
        file_builtins = dict(data.get("builtins") or {})
        meta = _merge_dicts(meta, file_meta)
        opcodes = _merge_dicts(opcodes, file_opcodes)
        builtins = _merge_dicts(builtins, file_builtins)

    if overrides:
        meta = _merge_dicts(meta, dict(overrides.get("meta") or {}))
        opcodes = _merge_dicts(opcodes, dict(overrides.get("opcodes") or {}))
        builtins = _merge_dicts(builtins, dict(overrides.get("builtins") or {}))

    return GasTable.build(meta=meta, opcodes=opcodes, builtins=builtins)


__all__ = ["GasTable", "load_gas_table"]
