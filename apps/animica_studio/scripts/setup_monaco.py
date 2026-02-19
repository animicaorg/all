"""Download and unpack Monaco Editor into the web assets directory.

Usage
-----
    python scripts/setup_monaco.py [--version 0.46.0]

Downloads the Monaco npm package and extracts the ``min/vs`` directory
into ``animica_studio/ui/web/monaco/vs``.

This step is optional: the IDE falls back to a plain-text QPlainTextEdit
editor when Monaco assets are absent.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_VERSION = "0.46.0"
_REGISTRY = "https://registry.npmjs.org"
_PACKAGE = "monaco-editor"

# Destination inside the source tree
_WEB_DIR = Path(__file__).parent.parent / "animica_studio" / "ui" / "web"
_DEST_DIR = _WEB_DIR / "monaco"


def _fetch_tarball_url(version: str) -> str:
    import json  # noqa: PLC0415
    url = f"{_REGISTRY}/{_PACKAGE}/{version}"
    log.info("Fetching package metadata from %s", url)
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        meta = json.loads(resp.read())
    tarball_url: str = meta["dist"]["tarball"]
    return tarball_url


def _download(url: str, dest: Path) -> None:
    log.info("Downloading %s …", url)
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        dest.write_bytes(resp.read())
    log.info("Downloaded → %s (%d bytes)", dest, dest.stat().st_size)


def _extract_vs(tarball: Path, out_dir: Path) -> None:
    """Extract only the ``package/min/vs`` subtree from the tarball."""
    log.info("Extracting Monaco vs/ assets…")
    out_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tarball, "r:gz") as tf:
        members = [m for m in tf.getmembers() if "min/vs" in m.name]
        for member in members:
            # Rewrite path: package/min/vs/... → vs/...
            parts = member.name.split("/")
            try:
                idx = parts.index("vs")
            except ValueError:
                continue
            rel = "/".join(parts[idx:])
            target = out_dir / rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as src, open(target, "wb") as dst:  # type: ignore[arg-type]
                    shutil.copyfileobj(src, dst)

    log.info("Extracted to %s", out_dir)


def setup_monaco(version: str = _DEFAULT_VERSION, force: bool = False) -> None:
    vs_dir = _DEST_DIR / "vs"
    if vs_dir.exists() and not force:
        print(f"Monaco already installed at {_DEST_DIR}. Use --force to reinstall.")
        return

    if _DEST_DIR.exists():
        shutil.rmtree(_DEST_DIR)

    with tempfile.TemporaryDirectory() as tmp:
        tarball_path = Path(tmp) / f"monaco-{version}.tgz"
        try:
            url = _fetch_tarball_url(version)
            _download(url, tarball_path)
            _extract_vs(tarball_path, _DEST_DIR)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"Monaco {version} installed → {_DEST_DIR}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Download Monaco Editor for Animica Studio IDE")
    parser.add_argument("--version", default=_DEFAULT_VERSION, help="Monaco npm version")
    parser.add_argument("--force", action="store_true", help="Reinstall even if already present")
    args = parser.parse_args()
    setup_monaco(version=args.version, force=args.force)


if __name__ == "__main__":
    main()
