#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
WALLET_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = WALLET_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

from linux_layout import resolve_linux_node_root_from_root, resolve_linux_node_root_from_wallet


def _load_verify_bundle_layout():
    module_path = SCRIPTS_DIR / "verify-bundle-layout.py"
    spec = importlib.util.spec_from_file_location("verify_bundle_layout", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_bundle_layout = _load_verify_bundle_layout()


class TestLinuxLayoutResolver(unittest.TestCase):
    def _touch(self, path: Path, executable: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test\n", encoding="utf-8")
        if executable:
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _create_node_tree(self, node_root: Path) -> None:
        self._touch(node_root / "venv" / "bin" / "python", executable=True)
        self._touch(node_root / "assets" / "spec" / "params.yaml")
        self._touch(node_root / "assets" / "genesis" / "devnet.json")

    def _create_appdir(self, root: Path, node_root: Path) -> None:
        self._touch(root / "usr" / "bin" / "animica-wallet", executable=True)
        self._create_node_tree(node_root)

    def test_resolve_root_prefers_multiarch_over_legacy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="animica-layout-") as tmp:
            root = Path(tmp)
            legacy = root / "usr" / "lib" / "animica-wallet" / "node"
            multiarch = root / "usr" / "lib" / "x86_64-linux-gnu" / "animica-wallet" / "node"
            self._create_appdir(root, legacy)
            self._create_node_tree(multiarch)

            resolved = resolve_linux_node_root_from_root(root)

            self.assertEqual(resolved, multiarch)

    def test_resolve_root_falls_back_to_legacy_libdir(self) -> None:
        with tempfile.TemporaryDirectory(prefix="animica-layout-") as tmp:
            root = Path(tmp)
            legacy = root / "usr" / "lib" / "animica-wallet" / "node"
            self._create_appdir(root, legacy)

            resolved = resolve_linux_node_root_from_root(root)

            self.assertEqual(resolved, legacy)

    def test_resolve_wallet_prefers_multiarch_relative_libdir(self) -> None:
        with tempfile.TemporaryDirectory(prefix="animica-layout-") as tmp:
            root = Path(tmp)
            wallet = root / "usr" / "bin" / "animica-wallet"
            legacy = root / "usr" / "lib" / "animica-wallet" / "node"
            multiarch = root / "usr" / "lib" / "x86_64-linux-gnu" / "animica-wallet" / "node"
            self._touch(wallet, executable=True)
            self._create_node_tree(legacy)
            self._create_node_tree(multiarch)

            resolved = resolve_linux_node_root_from_wallet(wallet)

            self.assertEqual(resolved, multiarch)

    def test_verify_linux_accepts_multiarch_appdir_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="animica-layout-") as tmp:
            root = Path(tmp)
            self._create_appdir(root, root / "usr" / "lib" / "x86_64-linux-gnu" / "animica-wallet" / "node")

            errors = verify_bundle_layout.verify_linux(root)

            self.assertEqual(errors, [])

    def test_verify_linux_accepts_legacy_appdir_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="animica-layout-") as tmp:
            root = Path(tmp)
            self._create_appdir(root, root / "usr" / "lib" / "animica-wallet" / "node")

            errors = verify_bundle_layout.verify_linux(root)

            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
