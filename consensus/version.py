"""
Consensus module versioning utilities.

- __version__: semantic base version for the consensus package
- git_describe(): best-effort `git describe` string for debugging builds
- build_meta(): returns a PEP 440–compatible version with local metadata when available
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

# Semantic base version for the consensus package.
# Bump this when making incompatible changes to consensus/ APIs or behavior.
__version__ = "0.1.0-dev"

_GIT_DESCRIBE_CACHE: Optional[str] = None


def _repo_root() -> Path:
    """Heuristic: repository root is the first ancestor containing a .git directory (or the project marker)."""
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / ".git").exists():
            return p
        # Fallback marker: top-level README or pyproject can also indicate root in exported sources
        if (p / "pyproject.toml").exists() and (p / "README.md").exists():
            return p
    return here.parent


def git_describe() -> Optional[str]:
    """
    Return a human-friendly Git description like:
      v0.1.0-23-gabc1234-dirty
    Falls back to None if not a Git checkout or Git is unavailable.
    Result is cached for process lifetime.
    """
    global _GIT_DESCRIBE_CACHE
    if _GIT_DESCRIBE_CACHE is not None:
        return _GIT_DESCRIBE_CACHE

    # Allow CI to inject an override (e.g., via env)
    env_override = os.getenv("ANIMICA_GIT_DESCRIBE")
    if env_override:
        _GIT_DESCRIBE_CACHE = env_override.strip()
        return _GIT_DESCRIBE_CACHE

    try:
        root = _repo_root()
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--dirty", "--always"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
        )
        _GIT_DESCRIBE_CACHE = out.decode("utf-8", "ignore").strip()
        return _GIT_DESCRIBE_CACHE or None
    except Exception:
        return None


_PEP440_LOCAL_SAFE = re.compile(r"[^0-9A-Za-z]+")


def _pep440_local(s: str) -> str:
    """
    Convert arbitrary string into a PEP 440 local version identifier segment.
    Example: 'v0.1.0-23-gabc1234-dirty' -> 'v0_1_0_23_gabc1234_dirty'
    """
    s = s.strip()
    s = s.replace(".", "_").replace("-", "_")
    return _PEP440_LOCAL_SAFE.sub("_", s)


def build_meta() -> str:
    """
    Compose a PEP 440–compatible version with local metadata when Git info is available.
    Example:
      __version__ == 0.1.0-dev
      git_describe() == v0.1.0-23-gabc1234-dirty
      -> '0.1.0-dev+v0_1_0_23_gabc1234_dirty'
    If ANIMICA_VERSION is set in the environment, that takes precedence verbatim.
    """
    env_version = os.getenv("ANIMICA_VERSION")
    if env_version:
        return env_version.strip()

    gd = git_describe()
    if gd:
        return f"{__version__}+{_pep440_local(gd)}"
    return __version__


def version_info() -> dict:
    """
    Structured dict for logging/metrics.
    {
      "module": "consensus",
      "version": "0.1.0-dev",
      "git": "v0.1.0-23-gabc1234",
      "full": "0.1.0-dev+v0_1_0_23_gabc1234"
    }
    """
    gd = git_describe()
    return {
        "module": "consensus",
        "version": __version__,
        "git": gd,
        "full": build_meta(),
    }


__all__ = ["__version__", "git_describe", "build_meta", "version_info"]
