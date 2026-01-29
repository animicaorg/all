"""
Version utilities for the Animica proofs module.

- __version__: semantic version of this module (PEP 440 core)
- version(): returns a PEP 440–compliant version string, optionally
  enriched with local git metadata (e.g., '0.1.0+g1a2b3c4.dirty')
- git_describe(): best-effort snapshot of the current git state
- runtime_banner(): short human-readable banner for logs

This file has *no* third-party deps and is safe to import in constrained runtimes.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional

# Bump this when you make breaking/feature/fix changes to proofs/.
# It can be overridden at build time with the env var ANIMICA_PROOFS_VERSION.
__version__ = os.getenv("ANIMICA_PROOFS_VERSION", "0.1.0")


@dataclass(frozen=True)
class GitInfo:
    tag: Optional[str]  # nearest tag (if any)
    commit: Optional[str]  # short commit hash (7+ chars)
    branch: Optional[str]  # current branch (if available)
    dirty: bool  # uncommitted changes?
    distance: Optional[int]  # commits since tag (if any)


def _run_git(args: list[str]) -> Optional[str]:
    """Run a git command; return stdout stripped or None if git not available/not a repo."""
    try:
        out = subprocess.check_output(
            ["git"] + args,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        )
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return None


def _in_git_repo() -> bool:
    return _run_git(["rev-parse", "--is-inside-work-tree"]) == "true"


def git_describe() -> GitInfo:
    """
    Best-effort description of the current git state (no exceptions).
    Works even if there are no tags.
    """
    if not _in_git_repo():
        return GitInfo(tag=None, commit=None, branch=None, dirty=False, distance=None)

    # Short hash
    commit = _run_git(["rev-parse", "--short", "HEAD"])

    # Dirty?
    dirty = False
    status = _run_git(["status", "--porcelain"])
    if status is not None and len(status) > 0:
        dirty = True

    # Branch
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch == "HEAD":
        # detached HEAD; leave as 'HEAD'
        pass

    # Nearest tag & distance
    tag = None
    distance: Optional[int] = None
    desc = _run_git(["describe", "--tags", "--abbrev=7", "--dirty", "--always"])
    # Examples:
    #  v0.1.0-3-g1a2b3c4
    #  v0.1.0
    #  1a2b3c4
    if desc:
        parts = desc.split("-")
        if len(parts) >= 3 and parts[-2].startswith("g"):
            # tagged + distance
            tag = "-".join(parts[:-2])
            try:
                distance = int(parts[-3])
            except Exception:
                distance = None
        elif len(parts) == 1:
            # either exact tag or raw hash
            if parts[0].startswith("v") or parts[0].startswith("V"):
                tag = parts[0]
                distance = 0
            else:
                # raw hash only; keep tag None
                pass

    return GitInfo(
        tag=tag, commit=commit, branch=branch, dirty=dirty, distance=distance
    )


def _pep440_local(metadata: list[str]) -> str:
    """Join metadata into a PEP 440 local version segment (+a.b.c). Filters empties."""
    cleaned = [m for m in metadata if m]
    return "+" + ".".join(cleaned) if cleaned else ""


def version() -> str:
    """
    Return a PEP 440–compliant version string.
    If git metadata is available, append a local segment with commit and flags.
    Examples:
      - '0.1.0'
      - '0.1.0+g1a2b3c4'
      - '0.1.0+g1a2b3c4.n3'   (n = commits since tag)
      - '0.1.0+g1a2b3c4.dirty'
    """
    base = __version__
    gi = git_describe()

    if gi.commit is None:
        return base

    meta = [f"g{gi.commit}"]
    if gi.distance and gi.distance > 0:
        meta.append(f"n{gi.distance}")
    if gi.dirty:
        meta.append("dirty")
    return f"{base}{_pep440_local(meta)}"


def runtime_banner(prefix: str = "animica-proofs") -> str:
    gi = git_describe()
    parts = [prefix, version()]
    if gi.branch:
        parts.append(f"branch={gi.branch}")
    if gi.tag:
        parts.append(f"tag={gi.tag}")
    if gi.commit:
        parts.append(f"commit={gi.commit}")
    if gi.dirty:
        parts.append("dirty")
    return " ".join(parts)


if __name__ == "__main__":
    # Print a simple banner for diagnostics
    print(runtime_banner())
