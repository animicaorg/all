"""
Version helpers for Animica core.

- Exposes __version__ (PEP 440–compatible when possible).
- Best-effort detection from:
    1) ANIMICA_VERSION env var (authoritative override)
    2) `git describe --tags --dirty --long`
    3) fallback DEFAULT_VERSION
- Provides helpers to introspect git metadata without raising on failure.

This module has **no external dependencies** and is safe to import very early.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Project default if no git / env available
DEFAULT_VERSION = "0.1.0"

# Accept tags like v1.2.3 or 1.2.3 (semantic version core)
_SEMVER_TAG = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:[-+].*)?$"
)


@dataclass(frozen=True)
class GitInfo:
    tag: Optional[str]
    distance: Optional[int]
    commit: Optional[str]
    dirty: bool
    describe: Optional[str]


def _repo_root() -> Path:
    # Resolve repo root by walking up until we find a .git folder; otherwise use file's parent.
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / ".git").exists():
            return p
    return here.parent


def _run_git_describe(cwd: Path) -> Optional[str]:
    try:
        cp = subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--long", "--always"],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=1.2,
        )
        out = cp.stdout.strip()
        return out or None
    except Exception:
        return None


def _parse_git_describe(desc: str) -> GitInfo:
    """
    Parse outputs like:
      - v1.2.3-0-gdeadbeef
      - v1.2.3-4-gdeadbeef
      - 1.2.3-7-gabc1234-dirty
      - deadbeef (no tags)
    """
    dirty = desc.endswith("-dirty")
    if dirty:
        desc = desc[: -len("-dirty")]

    parts = desc.split("-")
    # Tag-based form: <tag>-<distance>-g<hash>
    if len(parts) >= 3 and parts[-2].isdigit() and parts[-1].startswith("g"):
        tag = "-".join(parts[:-2])
        distance = int(parts[-2])
        commit = parts[-1][1:]
        return GitInfo(
            tag=tag or None,
            distance=distance,
            commit=commit,
            dirty=dirty,
            describe=desc + ("-dirty" if dirty else ""),
        )
    # Plain hash or something else
    if re.fullmatch(r"[0-9a-f]{7,}", parts[-1]):
        return GitInfo(
            tag=None,
            distance=None,
            commit=parts[-1],
            dirty=dirty,
            describe=desc + ("-dirty" if dirty else ""),
        )
    # Unknown form
    return GitInfo(
        tag=None,
        distance=None,
        commit=None,
        dirty=dirty,
        describe=desc + ("-dirty" if dirty else ""),
    )


def _pep440_from_git(info: GitInfo) -> Optional[str]:
    """
    Convert GitInfo into a PEP 440 compliant version:
      - If tag is semver-ish and distance==0 and not dirty: return tag (without 'v')
      - If tag is semver-ish and distance>0:      tag.post{distance}+g{sha7}[.dirty]
      - If no tag but hash present:               0!0.0.post0+g{sha7}[.dirty]
      - Else: None (caller will fall back)
    """
    local = []
    if info.commit:
        local.append(f"g{info.commit[:7]}")
    if info.dirty:
        local.append("dirty")

    local_suffix = f"+{'.'.join(local)}" if local else ""

    if info.tag and _SEMVER_TAG.match(info.tag):
        base = _SEMVER_TAG.match(info.tag).group(0).lstrip("v")  # type: ignore
        if (info.distance or 0) == 0 and not info.dirty:
            return base
        distance = info.distance if info.distance is not None else 0
        return f"{base}.post{distance}{local_suffix}"

    if info.commit:
        # Epoch 0; pseudo-version when there is no tag
        return f"0!0.0.post0{local_suffix}"

    return None


def git_info() -> GitInfo:
    """Return best-effort GitInfo for the current repository."""
    desc = _run_git_describe(_repo_root())
    if not desc:
        return GitInfo(tag=None, distance=None, commit=None, dirty=False, describe=None)
    return _parse_git_describe(desc)


def resolve_version() -> str:
    """
    Determine the version string in priority:
      1) ANIMICA_VERSION environment variable (verbatim)
      2) git describe → PEP 440 string
      3) DEFAULT_VERSION
    """
    env = os.getenv("ANIMICA_VERSION")
    if env:
        return env.strip()

    info = git_info()
    pep = _pep440_from_git(info)
    if pep:
        return pep

    return DEFAULT_VERSION


# Compute at import; cheap & side-effect free
__version__ = resolve_version()


def describe() -> str:
    """
    Human-friendly description string combining version and git info (if any).
    Example: "0.3.1.post5+gabc1234 (dirty)"
    """
    v = __version__
    info = git_info()
    if info.describe:
        return f"{v} [{info.describe}]"
    return v


if __name__ == "__main__":
    # CLI usage:
    #   python -m core.version            -> prints version
    #   python core/version.py            -> prints version and git describe info
    print(__version__)
