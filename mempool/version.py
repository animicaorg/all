"""
mempool.version
---------------

Single source of truth for the mempool package version.

- BASE_VERSION: the semantic version for this module (manually bumped).
- __version__: BASE_VERSION optionally suffixed with local build metadata derived
  from `git describe` (PEP 440 local version, e.g. "0.1.0+gabc1234" or
  "0.1.0+gabc1234.dirty").

Resolution order for the final __version__:
1) Environment override ANIMICA_VERSION_OVERRIDE (exact value used as-is).
2) BASE_VERSION plus git metadata (if Git repo and `git` available).
3) Plain BASE_VERSION.

A tiny CLI is provided:
    python -m mempool.version         # prints version string
    python -m mempool.version --json  # prints JSON with details
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Bump this when you cut a release of the mempool module.
BASE_VERSION = "0.1.0"

# --- Git helpers -------------------------------------------------------------

_GIT_DESCRIBE_RE = re.compile(
    r"^(?:(?P<tag>v?\d+\.\d+\.\d+))?(?:-(?P<commits>\d+)-g(?P<sha>[0-9a-f]{7,}))?(?P<dirty>-dirty)?$"
)


@dataclass(frozen=True)
class GitInfo:
    describe: Optional[str]
    tag: Optional[str]
    sha: Optional[str]
    commits_ahead: Optional[int]
    dirty: Optional[bool]


def _git(args: list[str]) -> Optional[str]:
    try:
        # Run in repository root if possible
        here = Path(__file__).resolve()
        repo_root = (
            here.parent.parent
        )  # ~/animica/mempool/ -> repo root is likely ~/animica
        res = subprocess.run(
            ["git", "-C", str(repo_root)] + args,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return res.stdout.strip()
    except Exception:
        return None


def git_info() -> GitInfo:
    desc = _git(["describe", "--tags", "--dirty", "--always"])
    if not desc:
        return GitInfo(None, None, None, None, None)

    m = _GIT_DESCRIBE_RE.match(desc)
    if not m:
        # Fallback: at least try to get SHA
        sha = _git(["rev-parse", "--short", "HEAD"])
        return GitInfo(desc, None, sha, None, "-dirty" in desc)

    tag = m.group("tag")
    sha = m.group("sha")
    commits = int(m.group("commits")) if m.group("commits") else None
    dirty = bool(m.group("dirty"))
    return GitInfo(desc, tag, sha, commits, dirty)


# --- Version construction ----------------------------------------------------


def _compose_version(base: str, gi: GitInfo) -> str:
    """
    Build a PEP 440 compliant version string using local version metadata:
      {base}+g{sha}[.dirty]
    """
    if not gi.sha:
        return base
    local = f"g{gi.sha}"
    if gi.dirty:
        local = f"{local}.dirty"
    return f"{base}+{local}"


def build_version() -> str:
    # 1) Environment override (use as-is)
    override = os.environ.get("ANIMICA_VERSION_OVERRIDE")
    if override:
        return override

    # 2) BASE_VERSION with git metadata (if available)
    gi = git_info()
    if gi.describe:
        return _compose_version(BASE_VERSION, gi)

    # 3) Plain base version
    return BASE_VERSION


def version_tuple() -> tuple[int, int, int]:
    major, minor, patch = BASE_VERSION.split(".")
    return int(major), int(minor), int(patch)


def describe() -> dict:
    """Return a structured dict with version and git fields (best-effort)."""
    gi = git_info()
    return {
        "base_version": BASE_VERSION,
        "version": _compose_version(BASE_VERSION, gi) if gi.describe else BASE_VERSION,
        "git": {
            "describe": gi.describe,
            "tag": gi.tag,
            "sha": gi.sha,
            "commits_ahead": gi.commits_ahead,
            "dirty": gi.dirty,
        },
        "source": (
            "env"
            if os.environ.get("ANIMICA_VERSION_OVERRIDE")
            else ("git" if gi.describe else "base")
        ),
    }


# Public: imported by mempool.__init__
__version__ = build_version()

# --- CLI ---------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    if "--json" in argv:
        print(json.dumps(describe(), indent=2, sort_keys=True))
    else:
        print(__version__)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(os.sys.argv[1:]))
