#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


LINUX_NODE_REQUIRED_PATHS = (
    Path("venv/bin/python"),
    Path("assets/spec/params.yaml"),
    Path("assets/genesis/devnet.json"),
)


def _dedupe(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()

    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)

    return unique


def _existing_multiarch_candidates(lib_root: Path) -> list[Path]:
    if not lib_root.is_dir():
        return []

    matches = [
        entry / "animica-wallet" / "node"
        for entry in sorted(lib_root.iterdir(), key=lambda item: item.name)
        if entry.is_dir() and entry.name.endswith("-linux-gnu")
    ]
    return matches


def _libdir_candidates(lib_root: Path) -> list[Path]:
    candidates = [lib_root / "x86_64-linux-gnu" / "animica-wallet" / "node"]
    candidates.extend(_existing_multiarch_candidates(lib_root))
    candidates.append(lib_root / "animica-wallet" / "node")
    return _dedupe(candidates)


def linux_node_root_candidates_from_root(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    candidates: list[Path] = []

    for lib_root in (root / "usr" / "lib", root / "lib"):
        candidates.extend(_libdir_candidates(lib_root))

    return _dedupe(candidates)


def linux_node_root_candidates_from_wallet(wallet_path: Path) -> list[Path]:
    wallet_path = wallet_path.expanduser().resolve()
    wallet_dir = wallet_path if wallet_path.is_dir() else wallet_path.parent
    candidates = [wallet_dir / "node"]
    candidates.extend(_libdir_candidates((wallet_dir.parent / "lib").resolve()))
    candidates.append(wallet_dir.parent / "lib" / "node")
    return _dedupe(candidates)


def resolve_linux_node_root_from_root(root: Path) -> Path | None:
    for candidate in linux_node_root_candidates_from_root(root):
        if candidate.is_dir():
            return candidate
    return None


def resolve_linux_node_root_from_wallet(wallet_path: Path) -> Path | None:
    for candidate in linux_node_root_candidates_from_wallet(wallet_path):
        if candidate.is_dir():
            return candidate
    return None


def _print_candidates(candidates: list[Path]) -> int:
    for candidate in candidates:
        print(candidate)
    return 0


def _print_resolved(resolved: Path | None) -> int:
    if resolved is None:
        return 1
    print(resolved)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve Linux wallet install layout paths.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_root = subparsers.add_parser("resolve-root", help="Resolve the bundled node root from a staged/install root.")
    resolve_root.add_argument("--path", required=True)

    list_root = subparsers.add_parser("list-root-candidates", help="List bundled node root candidates from a staged/install root.")
    list_root.add_argument("--path", required=True)

    resolve_wallet = subparsers.add_parser("resolve-wallet", help="Resolve the bundled node root from a wallet executable path.")
    resolve_wallet.add_argument("--path", required=True)

    list_wallet = subparsers.add_parser("list-wallet-candidates", help="List bundled node root candidates from a wallet executable path.")
    list_wallet.add_argument("--path", required=True)

    args = parser.parse_args()
    target = Path(args.path)

    if args.command == "resolve-root":
        return _print_resolved(resolve_linux_node_root_from_root(target))
    if args.command == "list-root-candidates":
        return _print_candidates(linux_node_root_candidates_from_root(target))
    if args.command == "resolve-wallet":
        return _print_resolved(resolve_linux_node_root_from_wallet(target))
    if args.command == "list-wallet-candidates":
        return _print_candidates(linux_node_root_candidates_from_wallet(target))

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
