#!/usr/bin/env python3
"""Build platform-specific Animica Studio packaging artifacts."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from animica_studio.release_packaging import (  # noqa: E402
    build_linux_deb,
    expected_macos_app,
    expected_windows_exe,
    normalize_release_version,
    read_project_version,
    read_version_file,
)

HOST_PREFIX = {
    "linux": "linux",
    "macos": "darwin",
    "windows": "win32",
}
REQUIRED_RUNTIME_MODULES = ("PySide6", "requests")
MONACO_LOADER_PATH = APP_ROOT / "animica_studio" / "ui" / "web" / "monaco" / "vs" / "loader.js"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Animica Studio release artifacts for the current platform."
    )
    parser.add_argument("target", choices=sorted(HOST_PREFIX), help="Packaging target to produce.")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Reuse the existing dist output instead of rerunning PyInstaller.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_host(args.target)
    write_version()
    version = normalize_release_version(
        read_version_file(),
        fallback_version=read_project_version(),
    )

    if not args.skip_build:
        ensure_runtime_modules_installed()
        ensure_monaco_assets_installed()
        run_pyinstaller()

    if args.target == "linux":
        artifact = build_linux_deb(version)
    elif args.target == "macos":
        artifact = expected_macos_app()
        if not artifact.exists():
            raise FileNotFoundError(
                f"Expected macOS app bundle at {artifact}, but PyInstaller did not produce it."
            )
    else:
        artifact = expected_windows_exe()
        if not artifact.exists():
            raise FileNotFoundError(
                f"Expected Windows executable at {artifact}, but PyInstaller did not produce it."
            )

    print(f"==> Done. Artifact: {artifact}")
    return 0


def ensure_host(target: str) -> None:
    expected_prefix = HOST_PREFIX[target]
    if not sys.platform.startswith(expected_prefix):
        raise SystemExit(
            f"{target} packaging must be run on a matching host platform. "
            f"Current host: {sys.platform}"
        )


def write_version() -> None:
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "build_version.py"),
            str(APP_ROOT / "animica_studio" / "_version.py"),
        ],
        cwd=APP_ROOT,
        check=True,
    )


def ensure_runtime_modules_installed() -> None:
    missing = [name for name in REQUIRED_RUNTIME_MODULES if importlib.util.find_spec(name) is None]
    if not missing:
        return
    missing_str = ", ".join(missing)
    raise SystemExit(
        "Packaging environment is missing required runtime modules: "
        f"{missing_str}. Install them with:\n"
        "  pip install -e 'apps/animica_studio[dev,package]'"
    )


def ensure_monaco_assets_installed() -> None:
    if MONACO_LOADER_PATH.exists():
        return
    print("==> Monaco assets missing; running setup_monaco.py")
    try:
        subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "setup_monaco.py")],
            cwd=APP_ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            "Failed to prepare Monaco assets for packaging. "
            "Run `python scripts/setup_monaco.py` and retry."
        ) from exc
    if not MONACO_LOADER_PATH.exists():
        raise SystemExit(
            f"Monaco setup completed but {MONACO_LOADER_PATH} is still missing."
        )


def run_pyinstaller() -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            str(SCRIPT_DIR / "pyinstaller.spec"),
        ],
        cwd=APP_ROOT,
        check=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
