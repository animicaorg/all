"""
DA module version utilities.

- __version__: base semantic version, optionally augmented with `git describe`.
- git_describe(): raw `git describe --tags --dirty --always` (str | None)
- version_with_git(): PEP 440-ish local version with commit count/hash, if available.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple

# Bump this when making a release of the DA package.
_BASE_SEMVER = "0.1.0"

# Public version string (may be augmented by Git metadata at import time)
__version__ = os.environ.get("ANIMICA_VERSION") or _BASE_SEMVER

# Parsed version info (major, minor, patch) from the base semver only.
def _parse_semver(v: str) -> Tuple[int, int, int]:
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


version_info: Tuple[int, int, int] = _parse_semver(_BASE_SEMVER)


def _repo_root() -> Optional[Path]:
    """
    Best-effort Git repo root for this file. Returns None if not in a Git repo.
    """
    try:
        here = Path(__file__).resolve()
        # Traverse upward looking for a .git directory (cheap and robust).
        for p in [here] + list(here.parents):
            if (p / ".git").exists():
                return p
    except Exception:
        pass
    return None


def git_describe() -> Optional[str]:
    """
    Return `git describe --tags --dirty --always` from the repo containing this file, if any.
    Respects env `ANIMICA_BUILD_NO_GIT=1` to disable.
    """
    if os.environ.get("ANIMICA_BUILD_NO_GIT") == "1":
        return None

    root = _repo_root()
    if root is None:
        return None

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "describe", "--tags", "--dirty", "--always"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        desc = (out.stdout or "").strip()
        return desc or None
    except Exception:
        return None


def _pep440_local_from_describe(desc: str) -> Optional[str]:
    """
    Convert common `git describe` formats to a PEP 440-compatible local suffix.

    Examples:
      v1.2.3            -> None (exact tag)
      1.2.3             -> None
      v1.2.3-5-gabc123  -> +5.gabc123
      1.2.3-5-gabc123   -> +5.gabc123
      gabc123           -> +gabc123
      v1.2.3-5-gabc123-dirty -> +5.gabc123.dirty
    """
    d = desc.strip()
    d = d[1:] if d.startswith("v") else d

    # Exact tag (no hyphens) -> no local suffix
    if re.fullmatch(r"\d+\.\d+\.\d+", d):
        return None

    # tag-commits-gSHA(-dirty)?
    m = re.match(r"^(\d+\.\d+\.\d+)-(\d+)-g([0-9a-fA-F]+)(-dirty)?$", d)
    if m:
        parts = [m.group(2), f"g{m.group(3)}"]
        if m.group(4):
            parts.append("dirty")
        return "+" + ".".join(parts)

    # Just a short hash, possibly with -dirty
    m2 = re.match(r"^g?([0-9a-fA-F]+)(-dirty)?$", d)
    if m2:
        parts2 = [f"g{m2.group(1)}"]
        if m2.group(2):
            parts2.append("dirty")
        return "+" + ".".join(parts2)

    return None


def version_with_git(base: Optional[str] = None) -> str:
    """
    Return the base semver, optionally augmented with a PEP 440 local suffix derived from git.
    Environment overrides:
      - ANIMICA_VERSION: if set, returned as-is.
      - ANIMICA_BUILD_NO_GIT=1: disables git probing.
    """
    # Hard override wins
    env_v = os.environ.get("ANIMICA_VERSION")
    if env_v:
        return env_v

    base_v = (base or _BASE_SEMVER).lstrip("v")
    desc = git_describe()
    if not desc:
        return base_v

    # If describe matches an exact tag, normalize and return the tag itself.
    tag = desc.lstrip("v")
    if re.fullmatch(r"\d+\.\d+\.\d+", tag):
        return tag

    local = _pep440_local_from_describe(desc)
    if local:
        return f"{base_v}{local}"

    # Fallback: return base + raw describe, sanitized
    safe = re.sub(r"[^A-Za-z0-9\.\+]+", ".", desc)
    return f"{base_v}+{safe}"


# Compute the public version at import time (cheap, no exception raising).
try:
    __version__ = version_with_git(__version__)
except Exception:
    # Never fail module import due to version decoration
    __version__ = __version__  # keep base/env value


def get_version() -> str:
    """Return the DA package version (possibly git-augmented)."""
    return __version__
