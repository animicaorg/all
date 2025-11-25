"""
capabilities.version
--------------------

Module semantic version and an optional Git describe suffix for build metadata.

- __version__: semantic version string (e.g., "0.1.0+gabcdef1").
- version(): callable that returns the same value (cached).

The Git suffix is appended when available (tags preferred, falls back to commit),
and may include "-dirty" when the working tree has changes. All failures fall
back to the plain semantic version so packaging works outside a git checkout.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path
from typing import Optional

# Bump this on intentional, user-visible releases/changes.
_SEMVER_BASE = "0.1.0"


def _find_repo_root(start: Path) -> Path:
    """
    Walk up from `start` to find a directory containing `.git`.
    Returns `start`'s parent if not found, which is fine for non-git installs.
    """
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return start


@functools.lru_cache(maxsize=1)
def _git_describe() -> Optional[str]:
    """
    Return `git describe --tags --always --dirty` (stripped) or None on failure.
    Cached to avoid repeated subprocess calls.
    """
    # Allow CI/build systems to inject a value explicitly.
    injected = os.getenv("ANIMICA_GIT_DESCRIBE")
    if injected:
        return injected.strip()

    try:
        here = Path(__file__).resolve()
        repo_root = _find_repo_root(here.parent)
        # Prefer tags; include abbreviated commit and "-dirty" if needed.
        proc = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=0.8,
            check=False,
        )
        if proc.returncode == 0:
            out = proc.stdout.strip()
            return out if out else None
    except Exception:
        # Any failure → no suffix; we keep packaging robust.
        return None
    return None


@functools.lru_cache(maxsize=1)
def version() -> str:
    """
    Return the module version string, optionally suffixed with +<git-describe>.
    """
    desc = _git_describe()
    return f"{_SEMVER_BASE}+{desc}" if desc else _SEMVER_BASE


__version__ = version()

__all__ = ["__version__", "version"]
