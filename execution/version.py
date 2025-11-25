"""
execution.version — semantic version string and VCS describe helper.

This module is intentionally tiny and dependency-free so it can be imported very early
during process startup (including in packaging or frozen environments).

Usage:
    from execution.version import __version__, git_describe, version_metadata
"""

from __future__ import annotations

from functools import lru_cache
from datetime import datetime, timezone
import os
import subprocess
from typing import Dict

# Bump this when making a tagged release. Use semver (major.minor.patch).
__version__ = "0.1.0"


@lru_cache(maxsize=1)
def git_describe() -> str:
    """
    Return a best-effort 'git describe' style string.

    Resolution order:
      1) Environment override ANIMICA_GIT_DESCRIBE (useful in containers).
      2) `git describe --tags --dirty --always` (if .git and git available).
      3) Fallback to __version__.

    Returns:
        str: e.g., 'v0.1.0-3-gdeadbee' or '0.1.0' or '0.1.0+local'
    """
    # Explicit override for hermetic builds
    override = os.getenv("ANIMICA_GIT_DESCRIBE")
    if override:
        return override.strip()

    # Try invoking git if available and we're in a repo
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--dirty", "--always"],
            stderr=subprocess.DEVNULL,
        )
        desc = out.decode("utf-8", "replace").strip()
        # Normalize common cases (ensure something non-empty)
        if desc:
            return desc
    except Exception:
        pass

    # Last resort: an identifiable local build string
    return f"{__version__}+local"


def _is_dirty(desc: str) -> bool:
    # git appends '-dirty' when working tree has uncommitted changes
    return desc.endswith("-dirty") or "-dirty-" in desc


@lru_cache(maxsize=1)
def version_metadata() -> Dict[str, str]:
    """
    Structured version info for logs / diagnostics.

    Keys:
        version     -> semantic version (from __version__)
        describe    -> git describe or fallback
        dirty       -> 'true' if working tree dirty (best-effort)
        build_time  -> UTC ISO8601 timestamp
    """
    desc = git_describe()
    return {
        "version": __version__,
        "describe": desc,
        "dirty": "true" if _is_dirty(desc) else "false",
        "build_time": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["__version__", "git_describe", "version_metadata"]
