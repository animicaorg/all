#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
WALLET_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = WALLET_ROOT / "scripts"


def _load_manifest_module():
    module_path = SCRIPTS_DIR / "generate-wallet-manifest.py"
    spec = importlib.util.spec_from_file_location("generate_wallet_manifest", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest_module = _load_manifest_module()


class TestWalletManifestGenerator(unittest.TestCase):
    def _write_file(self, path: Path, content: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    def test_build_manifest_accepts_windows_only_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wallet-manifest-") as tmp:
            website_dir = Path(tmp)
            installer_hash = self._write_file(website_dir / "animica-wallet-windows-x64.exe", b"setup\n")
            zip_hash = self._write_file(website_dir / "animica-wallet-windows-x64.zip", b"zip\n")
            (website_dir / "animica-wallet-windows.sha256").write_text(
                f"{installer_hash}  animica-wallet-windows-x64.exe\n"
                f"{zip_hash}  animica-wallet-windows-x64.zip\n",
                encoding="utf-8",
            )

            manifest = manifest_module.build_manifest(
                website_dir,
                version="v1.2.3-test",
                generated_at="2026-04-08T00:00:00Z",
                architecture="x86_64",
            )

            self.assertEqual(manifest["version"], "v1.2.3-test")
            self.assertIn("windows", manifest)
            self.assertNotIn("linux", manifest)
            self.assertEqual(manifest["windows"]["installer_sha256"], installer_hash)
            self.assertEqual(manifest["windows"]["zip_sha256"], zip_hash)

    def test_build_manifest_handles_absent_platforms(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wallet-manifest-") as tmp:
            website_dir = Path(tmp)

            with self.assertRaises(manifest_module.ManifestError):
                manifest_module.build_manifest(
                    website_dir,
                    version="v1.2.3-test",
                    generated_at="2026-04-08T00:00:00Z",
                    architecture="x86_64",
                )

    def test_build_manifest_rejects_checksum_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wallet-manifest-") as tmp:
            website_dir = Path(tmp)
            self._write_file(website_dir / "animica-wallet-linux.deb", b"deb\n")
            (website_dir / "animica-wallet-linux.sha256").write_text(
                "deadbeef  animica-wallet-linux.deb\n",
                encoding="utf-8",
            )

            with self.assertRaises(manifest_module.ManifestError):
                manifest_module.build_manifest(
                    website_dir,
                    version="v1.2.3-test",
                    generated_at="2026-04-08T00:00:00Z",
                    architecture="x86_64",
                )


if __name__ == "__main__":
    unittest.main()
