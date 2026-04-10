from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from omni_sdk.address import from_pubkey
from omni_sdk.wallet.signer import PQSigner


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _counter_paths() -> list[str]:
    return [
        "--manifest",
        "vm_py/examples/counter/manifest.json",
        "--code",
        "vm_py/examples/counter/contract.py",
    ]


def _write_wallet_file(path: Path, *, label: str, signer: PQSigner) -> None:
    sender = signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")
    store = {
        "format": "animica.wallets",
        "version": 2,
        "wallets": [
            {
                "label": label,
                "address": sender,
                "alg_id": signer.alg_id,
                "alg_name": signer.alg_name,
                "public_key_hex": signer.public_key.hex(),
                "secret_key_hex": signer.secret_key.hex(),
            }
        ],
        "default": label,
    }
    path.write_text(json.dumps(store), encoding="utf-8")


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
        *_counter_paths(),
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
        *_counter_paths(),
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
    assert '"algName": "sphincs_shake_128s"' in res.stdout


def test_deploy_counter_example_dry_run_with_wallet_label(tmp_path, monkeypatch) -> None:
    root = _repo_root()
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")

    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes(range(32)))
    wallet_file = tmp_path / "wallets.json"
    _write_wallet_file(wallet_file, label="test", signer=signer)
    expected_sender = signer.address or from_pubkey(
        signer.public_key, alg_id=signer.alg_id, hrp="anim"
    )

    cmd = [
        sys.executable,
        "sdk/python/examples/deploy_counter.py",
        *_counter_paths(),
        "--wallet-file",
        str(wallet_file),
        "--wallet-label",
        "test",
        "--dry-run",
    ]
    res = subprocess.run(
        cmd,
        cwd=root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode == 0, res.stderr
    assert '"dryRun": true' in res.stdout
    assert f'"sender": "{expected_sender}"' in res.stdout
    assert '"algName": "sphincs_shake_128s"' in res.stdout
    assert "signer source=wallet:test@" in res.stdout


def test_deploy_counter_example_rejects_invalid_seed_length() -> None:
    root = _repo_root()
    cmd = [
        sys.executable,
        "sdk/python/examples/deploy_counter.py",
        *_counter_paths(),
        "--alg",
        "sphincs_shake_128s",
        "--seed-hex",
        "11" * 64,  # 64 bytes (128 hex chars)
        "--dry-run",
    ]
    res = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode != 0
    assert "--seed-hex expects a 32-byte seed" in res.stderr
    assert "got 64 bytes" in res.stderr
    assert "pq.keygen API not recognized" not in (res.stdout + res.stderr)


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
