#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
WALLET_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = WALLET_ROOT / "scripts"


class TestWindowsCrossReleaseScripts(unittest.TestCase):
    def _run(self, script_name: str, *args: str) -> subprocess.CompletedProcess[str]:
        script_path = SCRIPTS_DIR / script_name
        return subprocess.run(
            ["bash", str(script_path), *args],
            cwd=str(WALLET_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_build_script_help_and_unknown_option(self) -> None:
        help_result = self._run("build-windows-cross.sh", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Usage:", help_result.stdout)

        invalid_result = self._run("build-windows-cross.sh", "--definitely-invalid")
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertIn("Unknown option", invalid_result.stderr)

    def test_publish_script_help_and_unknown_option(self) -> None:
        help_result = self._run("publish-wallet-downloads.sh", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Usage:", help_result.stdout)

        invalid_result = self._run("publish-wallet-downloads.sh", "--definitely-invalid")
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertIn("Unknown option", invalid_result.stderr)

    def test_release_script_help_and_unknown_option(self) -> None:
        help_result = self._run("release-windows-cross.sh", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Usage:", help_result.stdout)

        invalid_result = self._run("release-windows-cross.sh", "--definitely-invalid")
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertIn("Unknown option", invalid_result.stderr)


if __name__ == "__main__":
    unittest.main()
