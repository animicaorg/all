#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from linux_layout import (
    LINUX_NODE_REQUIRED_PATHS,
    linux_node_root_candidates_from_root,
    resolve_linux_node_root_from_root,
)


def _assert_exists(path: Path, errors: list[str], label: str) -> None:
    if not path.exists():
        errors.append(f"missing {label}: {path}")


def verify_macos(path: Path, require_embedded_node: bool = True) -> list[str]:
    errors: list[str] = []
    _assert_exists(path, errors, "app bundle")
    _assert_exists(path / "Contents" / "MacOS" / "AnimicaWallet", errors, "wallet executable")
    _assert_exists(path / "Contents" / "Info.plist", errors, "Info.plist")
    _assert_exists(path / "Contents" / "Resources" / "animica.icns", errors, "bundle icon")
    if require_embedded_node:
        _assert_exists(path / "Contents" / "Resources" / "node" / "venv" / "bin" / "python", errors, "bundled Python")
        _assert_exists(path / "Contents" / "Resources" / "node" / "assets" / "spec" / "params.yaml", errors, "bundled params")
        _assert_exists(path / "Contents" / "Resources" / "node" / "assets" / "genesis" / "devnet.json", errors, "bundled devnet genesis")
    _assert_exists(path / "Contents" / "PlugIns" / "platforms" / "libqcocoa.dylib", errors, "Qt cocoa platform plugin")
    return errors


def verify_windows(path: Path, require_embedded_node: bool = True) -> list[str]:
    root = path if path.is_dir() else path.parent
    errors: list[str] = []
    _assert_exists(root / "animica-wallet.exe", errors, "wallet executable")
    if require_embedded_node:
        _assert_exists(root / "node" / "venv" / "Scripts" / "python.exe", errors, "bundled Python")
        _assert_exists(root / "node" / "assets" / "spec" / "params.yaml", errors, "bundled params")
        _assert_exists(root / "node" / "assets" / "genesis" / "devnet.json", errors, "bundled devnet genesis")
    _assert_exists(root / "Qt6Core.dll", errors, "Qt6Core runtime")
    _assert_exists(root / "platforms" / "qwindows.dll", errors, "Qt windows platform plugin")
    return errors


def verify_linux(path: Path, require_embedded_node: bool = True) -> list[str]:
    root = path if path.is_dir() else path.parent
    errors: list[str] = []

    build_node_root = root / "bin" / "node"
    build_exe = root / "bin" / "animica-wallet"
    install_exe = root / "bin" / "animica-wallet"
    appdir_exe = root / "usr" / "bin" / "animica-wallet"

    if require_embedded_node and build_node_root.is_dir():
        _assert_exists(build_exe, errors, "build-tree wallet executable")
        node_root = build_node_root
        label = "build-tree"
    else:
        executable = install_exe if install_exe.exists() else appdir_exe
        if install_exe.exists():
            _assert_exists(install_exe, errors, "wallet executable")
            label = "install tree"
        else:
            _assert_exists(appdir_exe, errors, "AppDir wallet executable")
            label = "AppDir"

        if not require_embedded_node:
            return errors

        node_root = resolve_linux_node_root_from_root(root)
        if node_root is None:
            checked_candidates = ", ".join(str(candidate) for candidate in linux_node_root_candidates_from_root(root))
            errors.append(
                f"missing {label} bundled node root under {root}; checked: {checked_candidates}"
            )
            return errors

    if require_embedded_node:
        for relative_path in LINUX_NODE_REQUIRED_PATHS:
            label_suffix = relative_path.as_posix()
            _assert_exists(node_root / relative_path, errors, f"{label} {label_suffix}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify wallet-qt staged bundle layout.")
    parser.add_argument("--platform", required=True, choices=["linux", "macos", "windows"])
    parser.add_argument("--path", required=True, help="Path to the staged bundle, install root, or executable")
    parser.add_argument(
        "--remote-rpc-only",
        action="store_true",
        help="Skip embedded node checks for remote-RPC-only builds",
    )
    args = parser.parse_args()

    target = Path(args.path).expanduser().resolve()
    require_embedded_node = not args.remote_rpc_only
    if args.platform == "macos":
        errors = verify_macos(target, require_embedded_node=require_embedded_node)
    elif args.platform == "windows":
        errors = verify_windows(target, require_embedded_node=require_embedded_node)
    else:
        errors = verify_linux(target, require_embedded_node=require_embedded_node)

    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1

    print(f"[OK] Verified {args.platform} bundle layout at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
