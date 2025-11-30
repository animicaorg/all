"""
Version helpers for the Animica Python SDK.
We keep a static __version__ (PEP 440) and expose utilities to enrich it with
`git describe` metadata when available (useful in dev builds).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

# Bump this when publishing
__version__ = "0.1.0"


@dataclass(frozen=True)
class VersionInfo:
    base: str
    git: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.base if not self.git else f"{self.base} ({self.git})"


def _git_describe(cwd: Optional[str] = None) -> Optional[str]:
    """
    Return `git describe --tags --dirty --always` if we appear to be in a git repo,
    otherwise None. Safe to call in packaged environments.
    """
    cwd = cwd or os.path.dirname(os.path.abspath(__file__))
    # Fast check: is there a .git directory somewhere up the tree?
    root = cwd
    for _ in range(6):  # don't walk forever
        if os.path.isdir(os.path.join(root, ".git")):
            break
        parent = os.path.dirname(root)
        if parent == root:
            root = None
            break
        root = parent
    if not root:
        return None
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--always"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        desc = out.stdout.strip()
        return desc or None
    except Exception:
        return None


def version_info() -> VersionInfo:
    """Structured version info (base PEP440 plus optional git describe)."""
    return VersionInfo(base=__version__, git=_git_describe())


def version() -> str:
    """Human-friendly string, e.g. '0.1.0 (v0.1.0-3-gabc1234)'."""
    return str(version_info())


__all__ = ["__version__", "VersionInfo", "version_info", "version"]
