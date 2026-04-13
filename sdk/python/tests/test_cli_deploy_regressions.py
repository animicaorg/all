from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omni_sdk.address import from_pubkey
from omni_sdk.cli import deploy as deploy_cli
from omni_sdk.errors import RpcError
from omni_sdk.wallet.signer import PQSigner


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _write_manifest(path: Path) -> Path:
    manifest = {
        "name": "Counter",
        "version": "1.0.0",
        "abi": {
            "functions": [
                {"name": "get", "inputs": [], "outputs": [{"name": "value", "type": "u64"}]}
            ],
            "events": [],
        },
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _write_ir(path: Path) -> Path:
    path.write_bytes(b"dummy-ir")
    return path


def _write_wallet_file(path: Path, *, label: str, signer: PQSigner) -> None:
    sender = signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")
    store = {
        "format": "animica.wallets",
        "version": 2,
        "default": label,
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
    }
    path.write_text(json.dumps(store), encoding="utf-8")


def test_deploy_module_entrypoint_help_not_silent() -> None:
    root = _repo_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "sdk" / "python")

    res = subprocess.run(
        [sys.executable, "-m", "omni_sdk.cli.deploy", "--help"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode == 0, res.stderr
    assert "Usage" in res.stdout


def test_deploy_bad_manifest_path_fails_loudly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ir_path = _write_ir(tmp_path / "counter.ir")

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--manifest",
            str(tmp_path / "missing-manifest.json"),
            "--ir",
            str(ir_path),
            "--seed-hex",
            "00" * 32,
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "manifest not found" in combined


def test_deploy_bad_ir_path_fails_loudly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json")

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--manifest",
            str(manifest_path),
            "--ir",
            str(tmp_path / "missing-counter.ir"),
            "--seed-hex",
            "00" * 32,
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "ir/code file not found" in combined


def test_deploy_missing_keystore_fails_loudly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--keystore",
            str(tmp_path / "missing-wallets.json"),
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "wallet/keystore file not found" in combined


def test_deploy_rpc_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")

    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")

    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes(range(32)))
    wallet_path = tmp_path / "wallets.json"
    _write_wallet_file(wallet_path, label="test", signer=signer)

    class _RpcStub:
        def __init__(self, _url: str, timeout: float | None = None):
            self.timeout = timeout

        def request(self, method: str, _params: Any = None) -> Any:
            if method == "state.getNonce":
                return 7
            raise AssertionError(f"unexpected RPC method: {method}")

    def _raise_rpc_error(**_kwargs: Any) -> Any:
        raise RpcError(
            code=-32011,
            message="transaction rejected",
            method="tx.sendRawTransaction",
            data={"reason": "admission_failed"},
        )

    monkeypatch.setattr(deploy_cli, "RpcClient", _RpcStub)
    monkeypatch.setattr(deploy_cli, "deploy_package", _raise_rpc_error)

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--keystore",
            str(wallet_path),
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "RpcError" in combined
    assert "tx.sendRawTransaction" in combined
    assert "transaction rejected" in combined


def test_deploy_success_prints_required_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")

    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")

    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([7] * 32))
    wallet_path = tmp_path / "wallets.json"
    _write_wallet_file(wallet_path, label="test", signer=signer)

    class _RpcStub:
        def __init__(self, _url: str, timeout: float | None = None):
            self.timeout = timeout

        def request(self, method: str, _params: Any = None) -> Any:
            if method == "state.getNonce":
                return 11
            raise AssertionError(f"unexpected RPC method: {method}")

    expected_tx_hash = "0x" + "ab" * 32
    expected_contract_address = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j"
    expected_contract_id = "contract:counter:1"

    def _fake_deploy_package(**kwargs: Any) -> Any:
        assert kwargs["chain_id"] == 1
        assert kwargs["nonce"] == 11
        return expected_contract_address, {
            "txHash": expected_tx_hash,
            "status": "SUCCESS",
            "gasUsed": 123456,
            "blockNumber": 42,
            "createdContractId": expected_contract_id,
        }

    monkeypatch.setattr(deploy_cli, "RpcClient", _RpcStub)
    monkeypatch.setattr(deploy_cli, "deploy_package", _fake_deploy_package)

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--keystore",
            str(wallet_path),
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0, captured.err

    payload = json.loads(captured.out)
    assert payload["sender"].startswith("anim1")
    assert payload["rpcUrl"] == "http://127.0.0.1:8545"
    assert payload["chainId"] == 1
    assert payload["txHash"] == expected_tx_hash
    assert payload["contractAddress"] == expected_contract_address
    assert payload["createdContractId"] == expected_contract_id
    assert payload["manifestPath"] == str(manifest_path.resolve())
    assert payload["irPath"] == str(ir_path.resolve())
