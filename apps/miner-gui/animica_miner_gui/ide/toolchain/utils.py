"""Deterministic helpers for the Animica IDE toolchain."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

_JSON_SEPARATORS = (",", ":")


def canonical_json_str(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=_JSON_SEPARATORS,
        allow_nan=False,
    )


def canonical_json_bytes(data: Any) -> bytes:
    return canonical_json_str(data).encode("utf-8")


def sha3_256_hex(data: bytes | str) -> str:
    h = hashlib.sha3_256()
    if isinstance(data, str):
        data = data.encode("utf-8")
    h.update(data)
    return "0x" + h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_text(path: Path, data: str) -> None:
    atomic_write_bytes(path, data.encode("utf-8"))


def ensure_repo_on_path() -> Optional[Path]:
    """Ensure repo root is on sys.path so vm_py can be imported during dev."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "vm_py").is_dir():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return parent
    return None


def load_vm_py() -> Optional[ModuleType]:
    """Load vm_py if available, adding repo root to sys.path when needed."""
    if importlib.util.find_spec("vm_py") is None:
        ensure_repo_on_path()
    if importlib.util.find_spec("vm_py") is None:
        return None
    return importlib.import_module("vm_py")
