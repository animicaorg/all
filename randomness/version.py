"""
Version helpers for the Animica Randomness package.

This module tries, in order:
1) importlib.metadata (if the package is installed),
2) `git describe --tags --long --dirty --match "v*"` from the repo,
3) a static fallback BASE_VERSION.

All returned versions are normalized to PEP 440.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import subprocess
import sys
from typing import Optional

try:  # Python 3.8+
    from importlib.metadata import PackageNotFoundError, version as _pkg_version  # type: ignore
except Exception:  # pragma: no cover
    PackageNotFoundError = Exception  # type: ignore
    def _pkg_version(_: str) -> str:  # type: ignore
        raise PackageNotFoundError  # type: ignore

# Bump this when making intentional, source-level releases.
BASE_VERSION = "0.1.0"

_PKG_NAME = "animica-randomness"  # if published, otherwise harmless


@dataclass(frozen=True)
class GitInfo:
    tag: str
    distance: int
    commit: str
    dirty: bool


_GIT_DESCRIBE_RE = re.compile(
    r"""^
        v(?P<tag>\d+\.\d+\.\d+(?:[abrc]\d+)?)  # v1.2.3 or v1.2.3a1
        -
        (?P<distance>\d+)
        -
        g(?P<commit>[0-9a-fA-F]+)
        (?P<dirty>-dirty)?
        $
    """,
    re.VERBOSE,
)


def _find_git_root(start: Path) -> Optional[Path]:
    cur = start
    for _ in range(8):  # don't walk too far
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


@lru_cache(maxsize=1)
def _git_describe() -> Optional[GitInfo]:
    root = _find_git_root(Path(__file__).resolve())
    if not root:
        return None
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "describe",
                "--tags",
                "--long",
                "--dirty",
                "--always",
                '--match',
                'v*',
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None

    m = _GIT_DESCRIBE_RE.match(out)
    if not m:
        return None
    return GitInfo(
        tag=m.group("tag"),
        distance=int(m.group("distance")),
        commit=m.group("commit"),
        dirty=bool(m.group("dirty")),
    )


def _pep440_from_git(info: GitInfo) -> str:
    """
    Convert a `git describe` triplet to a PEP 440 version.

    Rules:
      - exact tag (distance == 0) -> tag (e.g., 1.2.3 or 1.2.3a1)
      - otherwise -> {tag}.post{distance}+g{commit}[.dirty]
    """
    v = info.tag
    if info.distance == 0 and not info.dirty:
        return v
    local = f"+g{info.commit}"
    if info.dirty:
        local += ".dirty"
    return f"{v}.post{info.distance}{local}"


@lru_cache(maxsize=1)
def get_version() -> str:
    # 1) If installed, prefer the package registry version
    try:
        return _pkg_version(_PKG_NAME)
    except PackageNotFoundError:
        pass

    # 2) Try to derive from git describe
    gi = _git_describe()
    if gi:
        return _pep440_from_git(gi)

    # 3) Fallback to a dev-suffixed base
    py = f".py{sys.version_info.major}{sys.version_info.minor}"
    return f"{BASE_VERSION}.post0+no-git{py}"


__version__ = get_version()
__all__ = ["__version__", "get_version", "GitInfo"]
