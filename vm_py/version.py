"""vm_py.version — semantic version & optional git-describe suffix.

This module exposes:
- __version__: a PEP 440-compliant version string
- git_describe(): best-effort 'git describe --tags --dirty --always'
- compute_version(): resolution order → env → package metadata → git → fallback

Environment overrides (first match wins):
- ANIMICA_VERSION
- VM_PY_VERSION
- GIT_DESCRIBE  (treated as the describe suffix, appended to BASE_VERSION)
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Optional

# Bump on consensus-affecting changes to the VM (IR/gas/ABI outputs).
BASE_VERSION = "0.1.0"

# --- helpers -----------------------------------------------------------------


def _pep440_local(s: str) -> str:
    """
    Convert a git-describe-style string into a safe PEP 440 local version.
    Example: 'v0.1.0-3-gabc1234-dirty' -> '0.1.0.3.gabc1234.dirty'
    """
    s = s.strip()
    s = s[1:] if s.startswith("v") else s
    s = s.replace("-", ".")
    # Allow only [A-Za-z0-9_.]; replace others with dots.
    s = re.sub(r"[^A-Za-z0-9_.]+", ".", s)
    # Collapse consecutive dots.
    s = re.sub(r"\.+", ".", s).strip(".")
    return s


@lru_cache(maxsize=1)
def git_describe(cwd: Optional[Path] = None) -> Optional[str]:
    """Return `git describe --tags --dirty --always` output, or None on failure."""
    try:
        repo = cwd or Path(__file__).resolve().parent
        out = subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--always"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=1.5,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
        if out.returncode != 0:
            return None
        desc = out.stdout.strip()
        return desc or None
    except Exception:
        return None


def _pkg_metadata_version(dist_name: str = "vm_py") -> Optional[str]:
    """Try to read installed package version; None if unavailable."""
    try:
        v = importlib_metadata.version(dist_name)
        return v if v and v != "0.0.0" else None
    except importlib_metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


@lru_cache(maxsize=1)
def compute_version() -> str:
    """
    Resolve a version string with this precedence:
      1) ANIMICA_VERSION or VM_PY_VERSION (exact value)
      2) Installed package metadata version for 'vm_py'
      3) BASE_VERSION + '+' + PEP440(git describe)
      4) BASE_VERSION + '+dev'
    """
    # 1) Env override (exact)
    for key in ("ANIMICA_VERSION", "VM_PY_VERSION"):
        val = os.getenv(key)
        if val:
            return val

    # 2) Installed metadata
    meta_v = _pkg_metadata_version()
    if meta_v:
        return meta_v

    # 3) git describe (env override for CI or actual git)
    desc_env = os.getenv("GIT_DESCRIBE")
    desc = desc_env or git_describe()
    if desc:
        return f"{BASE_VERSION}+{_pep440_local(desc)}"

    # 4) Fallback
    return f"{BASE_VERSION}+dev"


# Public constant
__version__ = compute_version()

__all__ = ["__version__", "BASE_VERSION", "git_describe", "compute_version"]
