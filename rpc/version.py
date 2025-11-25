"""
RPC module version helpers.

- __version__: base semantic version for the RPC package.
- git_describe(): returns `git describe --tags --dirty --always` (or None if unavailable).
- version_with_git(): combines __version__ with git describe for diagnostics.
"""
from __future__ import annotations

from functools import lru_cache
import subprocess
from typing import Optional

__version__ = "0.1.0-dev"


@lru_cache(maxsize=1)
def git_describe() -> Optional[str]:
    """
    Best-effort: return something like 'v0.1.0-12-gabcdef1-dirty'
    or None if we're not in a git repo or git is missing.
    """
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--dirty", "--always"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8").strip()
    except Exception:
        return None


def version_with_git() -> str:
    """
    Return a helpful version string for logs/metrics.
    e.g., '0.1.0-dev+v0.1.0-12-gabcdef1' or just '0.1.0-dev' if git not present.
    """
    desc = git_describe()
    return f"{__version__}+{desc}" if desc else __version__


__all__ = ["__version__", "git_describe", "version_with_git"]
