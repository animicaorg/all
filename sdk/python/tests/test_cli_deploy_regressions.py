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


def _wallet_entry(label: str, signer: PQSigner) -> dict[str, Any]:
    sender = signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")
    return {
        "label": label,
        "address": sender,
        "alg_id": signer.alg_id,
        "alg_name": signer.alg_name,
        "public_key_hex": signer.public_key.hex(),
        "secret_key_hex": signer.secret_key.hex(),
    }


def _write_animica_wallet_store(
    path: Path,
    *,
    entries: list[dict[str, Any]],
    default_label: str | None = None,
    default_address: str | None = None,
) -> None:
    store: dict[str, Any] = {
        "format": "animica.wallets",
        "version": 2,
        "wallets": entries,
    }
    if default_label is not None:
        store["default"] = default_label
    if default_address is not None:
        store["default_address"] = default_address
    path.write_text(json.dumps(store), encoding="utf-8")


def _write_sdk_keystore_entries(path: Path, *, label: str, signer: PQSigner) -> None:
    entry = _wallet_entry(label, signer)
    payload = {label: entry}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _install_success_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_sender: str,
    expected_nonce: int = 11,
) -> tuple[str, str, str]:
    class _RpcStub:
        def __init__(self, _url: str, timeout: float | None = None):
            self.timeout = timeout

        def request(self, method: str, params: Any = None) -> Any:
            if method == "state.getNonce":
                assert isinstance(params, list)
                assert params
                assert str(params[0]).lower() == expected_sender.lower()
                return expected_nonce
            raise AssertionError(f"unexpected RPC method: {method}")

    expected_tx_hash = "0x" + "ab" * 32
    expected_contract_address = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j"
    expected_contract_id = "contract:counter:1"

    def _fake_deploy_package(**kwargs: Any) -> Any:
        assert kwargs["chain_id"] == 1
        assert kwargs["nonce"] == expected_nonce
        signer = kwargs["signer"]
        sender = signer.address or from_pubkey(
            signer.public_key, alg_id=signer.alg_id, hrp="anim"
        )
        assert sender.lower() == expected_sender.lower()
        return expected_contract_address, {
            "txHash": expected_tx_hash,
            "status": "SUCCESS",
            "gasUsed": 123456,
            "blockNumber": 42,
            "createdContractId": expected_contract_id,
        }

    monkeypatch.setattr(deploy_cli, "RpcClient", _RpcStub)
    monkeypatch.setattr(deploy_cli, "deploy_package", _fake_deploy_package)
    return expected_tx_hash, expected_contract_address, expected_contract_id


def _assert_success_payload(
    payload: dict[str, Any],
    *,
    sender: str,
    tx_hash: str,
    contract_address: str,
    contract_id: str,
    manifest_path: Path,
    ir_path: Path,
) -> None:
    assert payload["sender"] == sender
    assert payload["rpc_url"] == "http://127.0.0.1:8545"
    assert payload["chain_id"] == 1
    assert payload["tx_hash"] == tx_hash
    assert payload["contract_address"] == contract_address
    assert payload["createdContractId"] == contract_id
    assert payload["manifest_path"] == str(manifest_path.resolve())
    assert payload["ir_path"] == str(ir_path.resolve())
    # Keep asserting the historical camelCase keys for backward compatibility.
    assert payload["rpcUrl"] == payload["rpc_url"]
    assert payload["chainId"] == payload["chain_id"]
    assert payload["txHash"] == payload["tx_hash"]
    assert payload["contractAddress"] == payload["contract_address"]
    assert payload["manifestPath"] == payload["manifest_path"]
    assert payload["irPath"] == payload["ir_path"]


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


def test_deploy_bad_manifest_path_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_deploy_bad_ir_path_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_deploy_missing_keystore_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert "wallet file not found" in combined


def test_deploy_sdk_keystore_path_still_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")

    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([1] * 32))
    sender = signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")

    keystore_path = tmp_path / "sdk-keystore.json"
    _write_sdk_keystore_entries(keystore_path, label="main", signer=signer)

    tx_hash, contract_address, contract_id = _install_success_stubs(
        monkeypatch, expected_sender=sender
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--keystore",
            str(keystore_path),
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    payload = json.loads(captured.out)
    _assert_success_payload(
        payload,
        sender=sender,
        tx_hash=tx_hash,
        contract_address=contract_address,
        contract_id=contract_id,
        manifest_path=manifest_path,
        ir_path=ir_path,
    )


def test_deploy_wallet_store_with_label_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")

    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([2] * 32))
    sender = signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")

    wallet_store = tmp_path / "wallets.json"
    _write_animica_wallet_store(
        wallet_store,
        entries=[_wallet_entry("main", signer)],
        default_label="main",
    )

    tx_hash, contract_address, contract_id = _install_success_stubs(
        monkeypatch, expected_sender=sender
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--label",
            "main",
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    payload = json.loads(captured.out)
    _assert_success_payload(
        payload,
        sender=sender,
        tx_hash=tx_hash,
        contract_address=contract_address,
        contract_id=contract_id,
        manifest_path=manifest_path,
        ir_path=ir_path,
    )


def test_deploy_wallet_store_with_address_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")

    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([3] * 32))
    sender = signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")

    wallet_store = tmp_path / "wallets.json"
    _write_animica_wallet_store(
        wallet_store,
        entries=[_wallet_entry("main", signer)],
        default_label="main",
    )

    tx_hash, contract_address, contract_id = _install_success_stubs(
        monkeypatch, expected_sender=sender
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--address",
            sender,
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    payload = json.loads(captured.out)
    _assert_success_payload(
        payload,
        sender=sender,
        tx_hash=tx_hash,
        contract_address=contract_address,
        contract_id=contract_id,
        manifest_path=manifest_path,
        ir_path=ir_path,
    )


def test_deploy_wallet_store_default_wallet_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")

    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    main_signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([4] * 32))
    backup_signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([5] * 32))
    main_sender = main_signer.address or from_pubkey(
        main_signer.public_key, alg_id=main_signer.alg_id, hrp="anim"
    )

    wallet_store = tmp_path / "wallets.json"
    _write_animica_wallet_store(
        wallet_store,
        entries=[_wallet_entry("backup", backup_signer), _wallet_entry("main", main_signer)],
        default_label="main",
        default_address=main_sender,
    )

    tx_hash, contract_address, contract_id = _install_success_stubs(
        monkeypatch, expected_sender=main_sender
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    payload = json.loads(captured.out)
    _assert_success_payload(
        payload,
        sender=main_sender,
        tx_hash=tx_hash,
        contract_address=contract_address,
        contract_id=contract_id,
        manifest_path=manifest_path,
        ir_path=ir_path,
    )


def test_deploy_wallet_store_missing_label_errors_clearly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([6] * 32))
    wallet_store = tmp_path / "wallets.json"
    _write_animica_wallet_store(
        wallet_store,
        entries=[_wallet_entry("main", signer)],
        default_label="main",
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--label",
            "missing",
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "wallet label 'missing' not found" in combined


def test_deploy_wallet_store_missing_address_errors_clearly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([7] * 32))
    wallet_store = tmp_path / "wallets.json"
    _write_animica_wallet_store(
        wallet_store,
        entries=[_wallet_entry("main", signer)],
        default_label="main",
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--address",
            "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqzzzzzz",
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "wallet address" in combined
    assert "not found" in combined


def test_deploy_wallet_entry_missing_address_errors_clearly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([9] * 32))
    entry = _wallet_entry("main", signer)
    entry.pop("address", None)

    wallet_store = tmp_path / "wallets.json"
    _write_animica_wallet_store(
        wallet_store,
        entries=[entry],
        default_label="main",
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--label",
            "main",
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "missing address" in combined


def test_deploy_wallet_store_without_default_errors_clearly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes([8] * 32))
    wallet_store = tmp_path / "wallets.json"
    _write_animica_wallet_store(
        wallet_store,
        entries=[_wallet_entry("main", signer)],
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "does not define a default wallet" in combined


def test_deploy_malformed_wallet_file_errors_clearly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    wallet_store = tmp_path / "wallets.json"
    wallet_store.write_text("{not-json", encoding="utf-8")

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--label",
            "main",
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "invalid JSON in wallet file" in combined


def test_deploy_ambiguous_wallet_format_errors_clearly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path / "manifest.json")
    ir_path = _write_ir(tmp_path / "counter.ir")
    wallet_store = tmp_path / "wallets.json"
    wallet_store.write_text(
        json.dumps(
            {
                "format": "animica.wallets",
                "version": 2,
                "wallets": [],
                "ciphertext": "deadbeef",
                "kdf": "PBKDF2-SHA3-256",
                "aead": "AES-256-GCM",
                "salt": "00",
                "nonce": "00",
            }
        ),
        encoding="utf-8",
    )

    rc = deploy_cli.main(
        [
            "--rpc",
            "http://127.0.0.1:8545",
            "--chain-id",
            "1",
            "--wallet-store",
            str(wallet_store),
            "--label",
            "main",
            "--manifest",
            str(manifest_path),
            "--ir",
            str(ir_path),
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "ambiguous wallet file format" in combined


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
    sender = signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")
    wallet_path = tmp_path / "wallets.json"
    _write_animica_wallet_store(
        wallet_path,
        entries=[_wallet_entry("main", signer)],
        default_label="main",
    )

    class _RpcStub:
        def __init__(self, _url: str, timeout: float | None = None):
            self.timeout = timeout

        def request(self, method: str, params: Any = None) -> Any:
            if method == "state.getNonce":
                assert isinstance(params, list)
                assert params and str(params[0]).lower() == sender.lower()
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
            "--wallet-store",
            str(wallet_path),
            "--label",
            "main",
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
