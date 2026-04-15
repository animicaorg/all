#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from linux_layout import (
    GENESIS_REQUIRED_FILES,
    LINUX_NODE_REQUIRED_PATHS,
    resolve_linux_node_root_from_root,
)


def _assert_exists(path: Path, errors: list[str], label: str) -> None:
    if not path.exists():
        errors.append(f"missing {label}: {path}")


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _assert_genesis_assets(root: Path, errors: list[str], label_prefix: str) -> None:
    for genesis_file in GENESIS_REQUIRED_FILES:
        _assert_exists(
            root / "assets" / "genesis" / genesis_file,
            errors,
            f"{label_prefix} ({genesis_file})",
        )


def verify_macos(path: Path) -> list[str]:
    errors: list[str] = []
    _assert_exists(path, errors, "app bundle")
    _assert_exists(path / "Contents" / "MacOS" / "AnimicaWallet", errors, "wallet executable")
    _assert_exists(path / "Contents" / "Info.plist", errors, "Info.plist")
    _assert_exists(path / "Contents" / "Resources" / "animica.icns", errors, "bundle icon")
    _assert_exists(path / "Contents" / "PlugIns" / "platforms" / "libqcocoa.dylib", errors, "Qt cocoa platform plugin")
    _assert_exists(path / "Contents" / "Resources" / "node" / "venv" / "bin" / "python", errors, "bundled Python runtime")
    _assert_exists(path / "Contents" / "Resources" / "node" / "assets" / "spec" / "params.yaml", errors, "bundled spec asset")
    _assert_genesis_assets(path / "Contents" / "Resources" / "node", errors, "bundled genesis asset")
    return errors


def verify_windows(path: Path) -> list[str]:
    root = path if path.is_dir() else path.parent
    errors: list[str] = []
    _assert_exists(root / "animica-wallet.exe", errors, "wallet executable")
    _assert_exists(root / "Qt6Core.dll", errors, "Qt6Core runtime")
    platform_plugin = _first_existing([
        root / "plugins" / "platforms" / "qwindows.dll",
        root / "platforms" / "qwindows.dll",
    ])
    if platform_plugin is None:
        errors.append(
            "missing Qt windows platform plugin: "
            f"{root / 'plugins' / 'platforms' / 'qwindows.dll'} "
            f"or {root / 'platforms' / 'qwindows.dll'}"
        )
    elif platform_plugin == root / "plugins" / "platforms" / "qwindows.dll":
        _assert_exists(root / "qt.conf", errors, "qt.conf")
    _assert_exists(root / "node" / "venv" / "Scripts" / "python.exe", errors, "bundled Python runtime")
    _assert_exists(root / "node" / "assets" / "spec" / "params.yaml", errors, "bundled spec asset")
    _assert_genesis_assets(root / "node", errors, "bundled genesis asset")
    return errors


def verify_linux(path: Path) -> list[str]:
    root = path if path.is_dir() else path.parent
    errors: list[str] = []

    candidates = [
        root / "bin" / "animica-wallet",
        root / "usr" / "bin" / "animica-wallet",
    ]
    executable = _first_existing(candidates)
    if executable is None:
        errors.append(
            "missing Linux wallet executable: "
            + ", ".join(str(candidate) for candidate in candidates)
        )

    node_root = resolve_linux_node_root_from_root(root)
    if node_root is None and root.name == "usr":
        node_root = resolve_linux_node_root_from_root(root.parent)
    if node_root is None:
        errors.append(f"missing bundled node root under {root}")
        return errors

    for rel in LINUX_NODE_REQUIRED_PATHS:
        _assert_exists(node_root / rel, errors, f"bundled node runtime file ({rel})")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify wallet-qt staged bundle layout.")
    parser.add_argument("--platform", required=True, choices=["linux", "macos", "windows"])
    parser.add_argument("--path", required=True, help="Path to the staged bundle, install root, or executable")
    parser.add_argument(
        "--remote-rpc-only",
        action="store_true",
        help="Accepted for backward compatibility; the wallet is always remote-RPC-only now.",
    )
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
