from __future__ import annotations

"""
aicf.version — semantic version string with optional git-describe suffix.

Rules:
- BASE_VERSION is the semver for this module.
- If ANIMICA_VERSION or AICF_VERSION is set in the environment, that wins.
- If we're inside a git repo, append a PEP440-compatible local suffix derived
  from `git describe --tags --dirty --always --abbrev=7`, e.g.:
    0.1.0+v0.1.0.3.gabc1234          (3 commits after tag, clean)
    0.1.0+gabc1234.dirty             (no tag, dirty tree)
- If git is unavailable, fall back to BASE_VERSION.
"""


import os
import re
import subprocess
from typing import Optional

# Bump this on intentional releases.
BASE_VERSION = "0.1.0"


def _run_git(*args: str) -> Optional[str]:
    try:
        out = subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL)
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return None


def _git_describe() -> Optional[str]:
    """
    Return `git describe` string or None if not in a git repo.
    Examples:
      v0.1.0-3-gabc1234
      gabc1234
      v0.1.0-3-gabc1234-dirty
    """
    return _run_git("describe", "--tags", "--dirty", "--always", "--abbrev=7")


_PEP440_LOCAL_CLEAN = re.compile(r"[^a-zA-Z0-9.]+")


def _pep440_local_from_describe(desc: str) -> str:
    """
    Convert a `git describe` string to a safe PEP440 local version suffix.
    Strategy:
      - replace '-' and '+' with '.'
      - drop a leading 'v' if followed by a digit
      - collapse any other non [a-zA-Z0-9.] to '.'
      - trim repeated dots
    """
    s = desc.replace("-", ".").replace("+", ".")
    if s.startswith("v") and len(s) > 1 and s[1].isdigit():
        s = s[1:]
    s = _PEP440_LOCAL_CLEAN.sub(".", s)
    s = re.sub(r"\.{2,}", ".", s).strip(".")
    # Prefix to make it obvious this is derived from git.
    if not s.lower().startswith(("git.", "g")):
        # common forms already contain 'g<sha>'; otherwise, add a neutral 'git' segment.
        s = f"git.{s}"
    return s


def build_version() -> str:
    # Env overrides (useful for packaging/CI)
    for key in ("ANIMICA_VERSION", "AICF_VERSION"):
        v = os.getenv(key)
        if v:
            return v

    desc = _git_describe()
    if not desc:
        return BASE_VERSION

    local = _pep440_local_from_describe(desc)
    return f"{BASE_VERSION}+{local}"


__version__ = build_version()


def get_version() -> str:
    """Public helper returning the resolved version string."""
    return __version__


__all__ = ["__version__", "get_version", "BASE_VERSION"]
