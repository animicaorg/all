# -*- coding: utf-8 -*-
"""
Contracts tooling helpers used by Makefile tasks and CI:
- Canonical JSON encode/decode for deterministic artifacts
- Simple SHA3-256 hashing helpers (hex/bytes)
- Tiny env/config accessors (RPC_URL, CHAIN_ID, etc.)
- Lightweight version discovery (git describe → fallback "0.0.0")
- FS utilities (atomic writes, mkdir -p)

This module is intentionally dependency-free so it can be imported from
build/deploy/verify scripts without creating a virtualenv first.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Final, Optional, Union

__all__ = [
    "__version__",
    "canonical_json_str",
    "canonical_json_bytes",
    "sha3_256_hex",
    "sha3_256_bytes",
    "ensure_dir",
    "atomic_write_bytes",
    "atomic_write_text",
    "read_bytes",
    "read_text",
    "env",
    "rpc_url",
    "chain_id",
    "project_root",
]

# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------


def _git_describe_version() -> Optional[str]:
    """
    Try to derive a human-friendly version from `git describe --always --dirty`.
    Returns None if not a git repo or git is unavailable.
    """
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty", "--abbrev=8"],
            stderr=subprocess.DEVNULL,
        )
        v = out.decode("utf-8", "replace").strip()
        # Normalize to something semver-ish when possible
        return v or None
    except Exception:
        return None


__version__: Final[str] = _git_describe_version() or os.getenv(
    "CONTRACTS_VERSION", "0.0.0"
)


# ---------------------------------------------------------------------------
# Canonical JSON (deterministic, stable across Python versions)
# ---------------------------------------------------------------------------

_JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")


def canonical_json_str(obj: Any) -> str:
    """
    Serialize an object to a canonical JSON string:
    - UTF-8 safe, no whitespace, sorted keys
    - Ensures stable hashing across platforms
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=_JSON_SEPARATORS,
        allow_nan=False,
    )


def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical JSON encoded as UTF-8 bytes."""
    return canonical_json_str(obj).encode("utf-8")


# ---------------------------------------------------------------------------
# Hashing (SHA3-256) helpers
# ---------------------------------------------------------------------------

_BytesLike = Union[bytes, bytearray, memoryview]


def _to_bytes(data: Union[str, _BytesLike, Any]) -> bytes:
    """
    Accepts:
      - bytes/bytearray/memoryview → as-is
      - str → UTF-8
      - other (dict/list/number/bool/None) → canonical JSON first
    """
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    # Fallback: canonical JSON for structured data
    return canonical_json_bytes(data)


def sha3_256_hex(data: Union[str, _BytesLike, Any]) -> str:
    """Return a hex string (0x-prefixed) of SHA3-256(data)."""
    h = hashlib.sha3_256()
    h.update(_to_bytes(data))
    return "0x" + h.hexdigest()


def sha3_256_bytes(data: Union[str, _BytesLike, Any]) -> bytes:
    """Return raw 32-byte SHA3-256 digest."""
    h = hashlib.sha3_256()
    h.update(_to_bytes(data))
    return h.digest()


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def ensure_dir(p: Union[str, os.PathLike[str]]) -> Path:
    """
    mkdir -p for a directory path; returns Path. No error if exists.
    """
    path = Path(p)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Propagate errors that aren't "already exists as dir"
        if e.errno != errno.EEXIST:
            raise
    return path


def atomic_write_bytes(path: Union[str, os.PathLike[str]], data: _BytesLike) -> Path:
    """
    Write bytes atomically: path.tmp → fsync → rename.
    Safe across crashes and for readers picking up complete artifacts only.
    """
    target = Path(path)
    ensure_dir(target.parent)
    with tempfile.NamedTemporaryFile(dir=str(target.parent), delete=False) as tf:
        tf.write(data)
        tf.flush()
        os.fsync(tf.fileno())
        tmp_name = tf.name
    os.replace(tmp_name, target)  # atomic on POSIX
    return target


def atomic_write_text(path: Union[str, os.PathLike[str]], text: str) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"))


def read_bytes(path: Union[str, os.PathLike[str]]) -> bytes:
    return Path(path).read_bytes()


def read_text(path: Union[str, os.PathLike[str]]) -> str:
    return Path(path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Env/config helpers
# ---------------------------------------------------------------------------


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Read environment variable with support for simple "file ref" notation:
    If value starts with '@', treat the rest as a path and read contents.
    """
    val = os.getenv(name, default)
    if isinstance(val, str) and val.startswith("@"):
        return read_text(val[1:]).strip()
    return val


def rpc_url(default: str = "http://127.0.0.1:8545") -> str:
    """
    Resolve RPC URL for local build/test/deploy tools.
    Environment variables checked (in order):
      - CONTRACTS_RPC_URL
      - RPC_URL
    """
    return env("CONTRACTS_RPC_URL") or env("RPC_URL") or default


def chain_id(default: int = 1337) -> int:
    """
    Resolve ChainId used for building SignBytes and deploys.
    Environment variables checked (in order):
      - CONTRACTS_CHAIN_ID
      - CHAIN_ID
    """
    cid = env("CONTRACTS_CHAIN_ID") or env("CHAIN_ID")
    try:
        return int(cid) if cid is not None else default
    except ValueError:
        return default


def project_root(start: Optional[Union[str, os.PathLike[str]]] = None) -> Path:
    """
    Walk up from `start` (or CWD) to locate a directory containing 'contracts/'.
    Returns the directory where 'contracts/' lives; if not found, returns CWD.
    """
    here = Path(start) if start else Path.cwd()
    for p in [here, *here.parents]:
        if (p / "contracts").is_dir():
            return p
    return here
