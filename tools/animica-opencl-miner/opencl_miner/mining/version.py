from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Optional

# Bump this when making backwards-incompatible changes to the mining module.
__version__ = "0.1.0"


def _git_describe(repo_root: Optional[pathlib.Path] = None) -> Optional[str]:
    """
    Return `git describe --tags --dirty --always` for the repository that
    contains this file. If git/metadata is unavailable (e.g., sdist/wheel),
    return None.
    """
    try:
        root = repo_root or pathlib.Path(__file__).resolve().parents[1]
        # Ensure we're actually inside a git repo before calling git
        if not (root / ".git").exists():
            return None
        out = subprocess.check_output(
            ["git", "-C", str(root), "describe", "--tags", "--dirty", "--always"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return None


def get_version() -> str:
    """
    Best-effort human-friendly version string:
      1) Respect env override ANIMICA_MINING_VERSION if set
      2) Use `git describe` when available (editable/dev installs)
      3) Fall back to semantic __version__
    """
    env = os.getenv("ANIMICA_MINING_VERSION")
    if env:
        return env
    desc = _git_describe()
    if desc:
        return desc
    return f"v{__version__}"


__all__ = ["__version__", "get_version"]
