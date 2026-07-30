#!/usr/bin/env python3
"""Resolve and verify the `animica` version bundled into a Miner GUI build.

The GUI freezes whatever `animica` happens to be importable at PyInstaller
time. That used to be invisible: nothing recorded it, so the shipped binary and
the download page could (and did) drift apart by several major versions.

This script closes that gap. It compares the installed `animica` distribution
against ``animica_miner_gui.BUNDLED_ANIMICA_VERSION`` and fails the build on a
mismatch, then prints the resolved version on stdout so the build script can
stamp it into the artifact manifest.

Usage (from a build script)::

    BUNDLED_ANIMICA="$("$PY" build-scripts/bundle_version.py)" || exit 1

Env:
    ANIMICA_ALLOW_BUNDLE_MISMATCH=1   warn instead of failing (dev builds)
"""
from __future__ import annotations

import os
import sys


def _expected() -> str:
    try:
        from animica_miner_gui import BUNDLED_ANIMICA_VERSION

        return BUNDLED_ANIMICA_VERSION
    except Exception as exc:  # pragma: no cover - import guard
        print(
            f"[bundle-version] cannot import animica_miner_gui: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _installed() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("animica")
        except PackageNotFoundError:
            return None
    except Exception:  # pragma: no cover - ancient interpreters
        return None


def main() -> int:
    expected = _expected()
    installed = _installed()
    lenient = os.environ.get("ANIMICA_ALLOW_BUNDLE_MISMATCH") == "1"

    if installed is None:
        msg = (
            "[bundle-version] the `animica` package is NOT installed; the GUI "
            "would ship without the node/miner runtime"
        )
        if not lenient:
            print(msg, file=sys.stderr)
            return 1
        print(f"{msg} (continuing: ANIMICA_ALLOW_BUNDLE_MISMATCH=1)", file=sys.stderr)
        print(expected)
        return 0

    if installed != expected:
        msg = (
            f"[bundle-version] installed animica {installed} != expected "
            f"{expected} (animica_miner_gui.BUNDLED_ANIMICA_VERSION). Update the "
            f"constant or install the matching package."
        )
        if not lenient:
            print(msg, file=sys.stderr)
            return 1
        print(f"{msg} (continuing: ANIMICA_ALLOW_BUNDLE_MISMATCH=1)", file=sys.stderr)

    print(installed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
