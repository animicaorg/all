"""
Version helpers for Animica Studio Services.

- ``__version__`` is the semantic version for packaging.
- ``git_describe()`` attempts to return ``git describe`` metadata if available.
- ``build_version()`` composes a PEP 440–compatible local version with git info.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Bump this when making a release; use semver (MAJOR.MINOR.PATCH)
__version__ = "0.1.0"


@dataclass(frozen=True)
class BuildMeta:
    version: str
    git: Optional[str]
    commit: Optional[str]
    dirty: bool
    timestamp: int


def _repo_root() -> Path:
    # Best-effort: repo root is two levels up from this file in a monorepo,
    # but fallback to current file directory if not present.
    here = Path(__file__).resolve()
    for p in [here.parent, here.parent.parent, here.parent.parent.parent]:
        if (p / ".git").exists():
            return p
    return here.parent


def git_describe(match: Optional[str] = None) -> Optional[str]:
    """
    Return `git describe --tags --long --dirty --always` output if available.

    Parameters
    ----------
    match : Optional[str]
        Optional pattern to limit tags matched (e.g., "studio-services*").

    Returns
    -------
    Optional[str]
        Description string or None if git is unavailable or not a repo.
    """
    root = _repo_root()
    cmd = [
        "git",
        "-C",
        str(root),
        "describe",
        "--tags",
        "--long",
        "--dirty",
        "--always",
    ]
    if match:
        cmd.extend(["--match", match])
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=2.0)
        return out.decode().strip()
    except Exception:
        # CI may provide environment variables instead of a git checkout
        env_desc = os.getenv("GIT_DESCRIBE") or os.getenv("GIT_REF")
        if env_desc:
            return env_desc
        return None


def _commit_short() -> Optional[str]:
    root = _repo_root()
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=1.5,
        )
        return out.decode().strip()
    except Exception:
        return os.getenv("GIT_COMMIT") or os.getenv("BUILD_SHA") or None


def build_version(base: str = __version__) -> str:
    """
    Compose a PEP 440–compatible version that includes git info when present.

    Examples
    --------
    - "0.1.0"                       (no git available)
    - "0.1.0+gabc1234"              (commit attached)
    - "0.1.0+gabc1234.dirty"        (worktree is dirty)
    """
    desc = git_describe()
    commit = _commit_short()
    if not desc and not commit:
        return base

    # Heuristic: mark dirty if `-dirty` suffix present in describe
    dirty = bool(desc and desc.endswith("-dirty"))

    # Prefer commit from rev-parse; otherwise try to peel from describe tail
    if not commit and desc:
        # Formats like: v0.1.0-3-gabc1234[-dirty] OR just abc1234[-dirty]
        token = desc.split("-")[-1]
        if token.startswith("g"):
            token = token[1:]
        commit = token.replace("-dirty", "")

    local_parts = []
    if commit:
        local_parts.append(f"g{commit}")
    if dirty:
        local_parts.append("dirty")

    if not local_parts:
        return base
    return f"{base}+{'.'.join(local_parts)}"


def build_meta() -> BuildMeta:
    return BuildMeta(
        version=build_version(),
        git=git_describe(),
        commit=_commit_short(),
        dirty=bool((git_describe() or "").endswith("-dirty")),
        timestamp=int(time.time()),
    )


def version() -> str:
    """Return the composed version string (with git metadata when available)."""
    return build_version()


__all__ = [
    "__version__",
    "git_describe",
    "build_version",
    "build_meta",
    "version",
    "BuildMeta",
]
