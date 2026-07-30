#!/usr/bin/env python3
"""Print the Animica Miner GUI version from apps/miner-gui/pyproject.toml.

The build scripts used to inline this as::

    VERSION="$($PY -c "import tomllib; print(tomllib.load(open(r'$APP_DIR/...')))" \
              2>/dev/null || echo "0.1.0")"

which is broken in two ways on Windows CI. `$APP_DIR` there is an MSYS path
(``/d/a/all/all/apps/miner-gui``) that native Windows Python cannot open, so the
command raised — and the ``|| echo "0.1.0"`` swallowed the failure and stamped
the release ``0.1.0``. That is exactly how a 9.0.8 build shipped as
``AnimicaMiner-0.1.0-windows-x64.zip``.

Resolving the path from ``__file__`` sidesteps path-flavour translation
entirely, and failing loudly means a broken probe stops the build instead of
silently mislabelling the artifact.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


def main() -> int:
    # build-scripts/gui_version.py -> apps/miner-gui/pyproject.toml
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.is_file():
        print(f"[gui-version] not found: {pyproject}", file=sys.stderr)
        return 1
    try:
        with pyproject.open("rb") as fh:
            version = tomllib.load(fh)["project"]["version"]
    except Exception as exc:
        print(f"[gui-version] cannot read {pyproject}: {exc}", file=sys.stderr)
        return 1
    if not version:
        print(f"[gui-version] empty version in {pyproject}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
