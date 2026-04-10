from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_deploy_counter_example_help_runs() -> None:
    root = _repo_root()
    cmd = [sys.executable, "sdk/python/examples/deploy_counter.py", "--help"]
    res = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "Deploy vm_py/examples/counter" in res.stdout


def test_omni_sdk_deploy_cli_help_runs() -> None:
    root = _repo_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "sdk" / "python")
    cmd = [sys.executable, "-m", "omni_sdk.cli.main", "deploy", "package", "--help"]
    res = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "Deploy a manifest+code contract package" in res.stdout


def test_deploy_counter_example_dry_run() -> None:
    root = _repo_root()
    cmd = [
        sys.executable,
        "sdk/python/examples/deploy_counter.py",
        "--manifest",
        "vm_py/examples/counter/manifest.json",
        "--code",
        "vm_py/examples/counter/contract.py",
        "--sender",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j",
        "--dry-run",
    ]
    res = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert '"dryRun": true' in res.stdout


def test_deploy_counter_example_dry_run_with_seed_creates_signer() -> None:
    root = _repo_root()
    env = os.environ.copy()
    env["ANIMICA_UNSAFE_PQ_FAKE"] = "1"
    env["ANIMICA_ALLOW_PQ_PURE_FALLBACK"] = "1"
    cmd = [
        sys.executable,
        "sdk/python/examples/deploy_counter.py",
        "--manifest",
        "vm_py/examples/counter/manifest.json",
        "--code",
        "vm_py/examples/counter/contract.py",
        "--alg",
        "sphincs_shake_128s",
        "--seed-hex",
        "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
        "--dry-run",
    ]
    res = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert '"dryRun": true' in res.stdout
    assert '"sender": "anim1' in res.stdout


def test_omni_sdk_deploy_cli_dry_run() -> None:
    root = _repo_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "sdk" / "python")
    cmd = [
        sys.executable,
        "-m",
        "omni_sdk.cli.main",
        "--chain-id",
        "1",
        "deploy",
        "package",
        "--manifest",
        "vm_py/examples/counter/manifest.json",
        "--code",
        "vm_py/examples/counter/contract.py",
        "--sender",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j",
        "--dry-run",
    ]
    res = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert '"dryRun": true' in res.stdout
