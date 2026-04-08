#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _assert_exists(path: Path, errors: list[str], label: str) -> None:
    if not path.exists():
        errors.append(f"missing {label}: {path}")


def verify_macos(path: Path) -> list[str]:
    errors: list[str] = []
    _assert_exists(path, errors, "app bundle")
    _assert_exists(path / "Contents" / "MacOS" / "AnimicaWallet", errors, "wallet executable")
    _assert_exists(path / "Contents" / "Info.plist", errors, "Info.plist")
    _assert_exists(path / "Contents" / "Resources" / "animica.icns", errors, "bundle icon")
    _assert_exists(path / "Contents" / "Resources" / "node" / "venv" / "bin" / "python", errors, "bundled Python")
    _assert_exists(path / "Contents" / "Resources" / "node" / "assets" / "spec" / "params.yaml", errors, "bundled params")
    _assert_exists(path / "Contents" / "Resources" / "node" / "assets" / "genesis" / "devnet.json", errors, "bundled devnet genesis")
    _assert_exists(path / "Contents" / "PlugIns" / "platforms" / "libqcocoa.dylib", errors, "Qt cocoa platform plugin")
    return errors


def verify_windows(path: Path) -> list[str]:
    root = path if path.is_dir() else path.parent
    errors: list[str] = []
    _assert_exists(root / "animica-wallet.exe", errors, "wallet executable")
    _assert_exists(root / "node" / "venv" / "Scripts" / "python.exe", errors, "bundled Python")
    _assert_exists(root / "node" / "assets" / "spec" / "params.yaml", errors, "bundled params")
    _assert_exists(root / "node" / "assets" / "genesis" / "devnet.json", errors, "bundled devnet genesis")
    _assert_exists(root / "Qt6Core.dll", errors, "Qt6Core runtime")
    _assert_exists(root / "platforms" / "qwindows.dll", errors, "Qt windows platform plugin")
    return errors


def verify_linux(path: Path) -> list[str]:
    root = path if path.is_dir() else path.parent
    errors: list[str] = []

    build_exe = root / "bin" / "animica-wallet"
    build_python = root / "bin" / "node" / "venv" / "bin" / "python"
    build_params = root / "bin" / "node" / "assets" / "spec" / "params.yaml"

    install_exe = root / "bin" / "animica-wallet"
    install_python = root / "lib" / "animica-wallet" / "node" / "venv" / "bin" / "python"
    install_params = root / "lib" / "animica-wallet" / "node" / "assets" / "spec" / "params.yaml"

    appdir_exe = root / "usr" / "bin" / "animica-wallet"
    appdir_python = root / "usr" / "lib" / "node" / "venv" / "bin" / "python"
    appdir_params = root / "usr" / "lib" / "node" / "assets" / "spec" / "params.yaml"

    if build_python.exists():
        _assert_exists(build_exe, errors, "build-tree wallet executable")
        _assert_exists(build_python, errors, "build-tree bundled Python")
        _assert_exists(build_params, errors, "build-tree bundled params")
    elif install_exe.exists():
        _assert_exists(install_exe, errors, "wallet executable")
        _assert_exists(install_python, errors, "bundled Python")
        _assert_exists(install_params, errors, "bundled params")
    else:
        _assert_exists(appdir_exe, errors, "AppDir wallet executable")
        _assert_exists(appdir_python, errors, "AppDir bundled Python")
        _assert_exists(appdir_params, errors, "AppDir bundled params")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify wallet-qt staged bundle layout.")
    parser.add_argument("--platform", required=True, choices=["linux", "macos", "windows"])
    parser.add_argument("--path", required=True, help="Path to the staged bundle, install root, or executable")
    args = parser.parse_args()

    target = Path(args.path).expanduser().resolve()
    if args.platform == "macos":
        errors = verify_macos(target)
    elif args.platform == "windows":
        errors = verify_windows(target)
    else:
        errors = verify_linux(target)

    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1

    print(f"[OK] Verified {args.platform} bundle layout at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
