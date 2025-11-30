"""
zk.tests helpers

Lightweight utilities and environment defaults shared by zk/* tests.
This module is intentionally importable without optional native deps.

Exports:
- REPO_ROOT, TEST_ROOT
- fixture_path(*parts) -> Path
- read_json(path_or_name) -> Any
- canonical_json_bytes(obj) -> bytes
- normalize_hex(x) -> "0x..." (lower, even-length)
- sha3_256_hex(b: bytes) -> "0x..."
- env_flag(name, default=False) -> bool
- is_ci() -> bool
- configure_test_logging() -> None

Environment toggles (optional, used by verifiers/adapters if present):
- ZK_FORCE_PYECC=1        → force py_ecc backend for BN254
- ZK_DISABLE_NATIVE=1     → disable any native EC/hash backends
- ZK_TEST_LOG=1           → enable INFO logging for zk.*
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Union

# --- Paths --------------------------------------------------------------------

TEST_ROOT: Path = Path(__file__).resolve().parent


# Try to locate the repository root heuristically: look for top-level "zk" or a VCS marker
def _find_repo_root(start: Path) -> Path:
    p = start
    for _ in range(10):
        if (
            (p / ".git").exists()
            or (p / "pyproject.toml").exists()
            or (p / "zk").is_dir()
        ):
            return p
        if p.parent == p:
            break
        p = p.parent
    return start


REPO_ROOT: Path = _find_repo_root(TEST_ROOT)


def fixture_path(*parts: Union[str, Path]) -> Path:
    """
    Return a path under zk/tests/fixtures (create the dir lazily if asked for it explicitly).
    """
    base = TEST_ROOT / "fixtures"
    return base.joinpath(*map(lambda x: Path(x), parts))


# --- JSON & hashing ------------------------------------------------------------


def canonical_json_bytes(obj: Any) -> bytes:
    """
    Serialize obj to canonical JSON bytes: sorted keys, no extra whitespace, UTF-8.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def read_json(path_or_name: Union[str, Path]) -> Any:
    """
    Read and parse JSON from a path. If a bare name is given, resolve under fixtures/.
    """
    p = Path(path_or_name)
    if not p.suffix and not p.exists():
        p = fixture_path(str(p))
    if not p.exists():
        raise FileNotFoundError(f"JSON file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_hex(x: Union[str, int, bytes]) -> str:
    """
    Normalize int/bytes/hex-string to 0x-prefixed lower-case hex of even length.
    Does NOT perform field reduction; callers decide the semantics.
    """
    if isinstance(x, int):
        s = hex(x)[2:]
    elif isinstance(x, bytes):
        s = x.hex()
    elif isinstance(x, str):
        s = x[2:] if x.startswith(("0x", "0X")) else x
    else:
        raise TypeError(f"Unsupported type for normalize_hex: {type(x)}")
    if len(s) % 2 == 1:
        s = "0" + s
    return "0x" + s.lower()


def sha3_256_hex(b: bytes) -> str:
    """
    sha3-256 digest of bytes, returned as 0x-prefixed hex.
    """
    return "0x" + hashlib.sha3_256(b).hexdigest()


# --- Env & logging -------------------------------------------------------------


def env_flag(name: str, default: bool = False) -> bool:
    """
    Read an environment flag in a truthy/falsey way: "1", "true", "yes" → True.
    """
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def is_ci() -> bool:
    """
    Detect common CI environments.
    """
    return any(
        env_flag(k) for k in ("CI", "GITHUB_ACTIONS", "BUILDkite", "TEAMCITY_VERSION")
    )


def configure_test_logging(level: int | None = None) -> None:
    """
    Configure basic logging for zk.* loggers when ZK_TEST_LOG is set.
    """
    if level is None:
        level = logging.INFO
    if env_flag("ZK_TEST_LOG", False):
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        logging.getLogger("zk").setLevel(level)
        logging.getLogger("zk.*").setLevel(level)


# Apply a few deterministic defaults early (best-effort; harmless if unused)
# Note: PYTHONHASHSEED must be set before interpreter start to be effective; we log the state.
if "PYTHONHASHSEED" not in os.environ:
    os.environ["PYTHONHASHSEED"] = "0"

# Optional backend toggles (verifiers may read these)
os.environ.setdefault("ZK_FORCE_PYECC", "0")
os.environ.setdefault("ZK_DISABLE_NATIVE", "0")

# Enable logging if requested
configure_test_logging()

__all__ = [
    "REPO_ROOT",
    "TEST_ROOT",
    "fixture_path",
    "read_json",
    "canonical_json_bytes",
    "normalize_hex",
    "sha3_256_hex",
    "env_flag",
    "is_ci",
    "configure_test_logging",
]
