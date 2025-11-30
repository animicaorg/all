from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Semantic version of the p2p module. Bump on API/behavior changes.
__version__ = "0.1.0"

__all__ = [
    "__version__",
    "git_describe",
    "build_meta",
    "version_with_git",
]


def _repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """
    Try to find the repository root (directory containing .git).
    Works both in editable installs and when running from source.
    """
    cur = (start or Path(__file__)).resolve()
    for p in [cur] + list(cur.parents):
        if (p / ".git").exists():
            return p
    # Fall back to the package directory (still ok if not a git checkout)
    return Path(__file__).resolve().parent.parent


def _run_git_describe(root: Path) -> Optional[str]:
    """
    Run `git describe --always --dirty --tags` in the given root.
    Returns the raw string (e.g., 'v0.1.0-3-gabc1234' or 'abc1234-dirty') or None.
    """
    try:
        # Prefer using -C <path> so it works from any CWD.
        out = subprocess.check_output(
            ["git", "-C", str(root), "describe", "--always", "--dirty", "--tags"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", "ignore").strip()
    except Exception:
        return None


_tag_re = re.compile(r"^v?(?P<maj>\d+)\.(?P<min>\d+)\.(?P<pat>\d+)(?P<rest>.*)$")


@dataclass(frozen=True)
class BuildMeta:
    """
    Build metadata we can surface to logs/metrics:
    - base_version: the library's semantic version (__version__)
    - git: output of `git describe` (or env override) if available
    - root: resolved repo root used for the git call
    """

    base_version: str
    git: Optional[str]
    root: Optional[Path]


def git_describe(default: str = "nogit") -> str:
    """
    Return a human-friendly git description if available, else `default`.

    Resolution order:
      1) ANIMICA_GIT_DESCRIBE env var (useful in containers/CI)
      2) `git describe --always --dirty --tags` at repo root
      3) `default` (defaults to "nogit")
    """
    # 1) CI/containers can pin this explicitly
    env_val = os.getenv("ANIMICA_GIT_DESCRIBE")
    if env_val:
        return env_val

    # 2) Try to run git describe
    root = _repo_root()
    if root:
        raw = _run_git_describe(root)
        if raw:
            return raw

    # 3) Fallback
    return default


def version_with_git() -> str:
    """
    Combine the semantic version with a git description when it adds value.

    Examples:
      __version__ = 0.1.0, git = v0.1.0        → "0.1.0"
      __version__ = 0.1.0, git = v0.1.0-3-gabc → "0.1.0+3.gabc"
      __version__ = 0.1.0, git = abc1234       → "0.1.0+abc1234"
      git unavailable                           → "0.1.0"
    """
    desc = git_describe(default="")
    if not desc:
        return __version__

    m = _tag_re.match(desc)
    if m and m.group("rest"):
        # Tag + distance + hash form, e.g. v0.1.0-3-gabc1234(-dirty)
        rest = m.group("rest").lstrip("-")
        # Normalize hyphens to dots for a PEP 440-ish local version
        rest = rest.replace("-", ".")
        return f"{__version__}+{rest}"
    if m and not m.group("rest"):
        # Exactly on a tag → just return semantic version
        return __version__
    # Plain hash (and maybe -dirty)
    norm = desc.replace("-", ".")
    return f"{__version__}+{norm}"


def build_meta() -> BuildMeta:
    """Return structured build metadata for logs/metrics UIs."""
    root = _repo_root()
    return BuildMeta(
        base_version=__version__, git=git_describe(default=None) or None, root=root
    )


# CLI helper: `python -m p2p.version`
if __name__ == "__main__":
    meta = build_meta()
    print(f"p2p.__version__ = {meta.base_version}")
    print(f"git_describe    = {meta.git or 'nogit'}")
    print(f"version+git     = {version_with_git()}")
    print(f"repo_root       = {meta.root}")
