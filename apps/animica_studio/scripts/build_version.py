"""Build version string from git tags for use in packaging."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def get_version() -> str:
    """Return version string derived from the nearest git tag.

    Falls back to ``0.0.0+unknown`` if git is unavailable.
    """
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--dirty", "--always"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # Normalise: v1.2.3 → 1.2.3
        if tag.startswith("v"):
            tag = tag[1:]
        return tag
    except Exception:  # noqa: BLE001
        return "0.0.0+unknown"


def write_version_file(output_path: str) -> str:
    version = get_version()
    Path(output_path).write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    print(f"Version: {version} → {output_path}")
    return version


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "animica_studio/_version.py"
    write_version_file(out)
