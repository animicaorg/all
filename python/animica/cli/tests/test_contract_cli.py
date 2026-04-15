from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from animica.cli import contract as contract_cli
from animica.cli.main import app
from animica.contracts import artifacts as artifact_store
from animica.contracts import deployments as deployment_store
from animica.contracts import wallet_utils

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[4]


class _FakeSigner:
    alg_id = 0x1001
    public_key = b"\x11" * 32
    address = "anim1fake9rj4ddu9rj4ddu9rj4ddu9rj4ddu9rj4ddu9rj4ddu9rj4ddu9s8c2qf"

    def sign_tx(self, message: bytes, chain_id: int, fork_id: int | None = None) -> bytes:
        _ = (message, chain_id, fork_id)
        return b"\x22" * 64


class _FakeSignerResolution:
    def __init__(self, sender: str):
        self.signer = _FakeSigner()
        self.sender = sender
        self.source = "test-wallet"
        self.source_kind = "wallet-store"


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANIMICA_HOME", str(home / ".animica"))
    monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1337")


def _write_wallet_store(path: Path) -> None:
    data = {
        "version": 1,
        "wallets": [
            {
                "label": "main",
                "address": "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2",
                "alg_id": 4097,
                "alg_name": "dilithium3",
                "public_key_hex": "11" * 32,
                "secret_key_hex": "22" * 32,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _patch_deploy_submission_primitives(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tx_hash: str = "0x" + "aa" * 32,
) -> None:
    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution(
            "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2"
        ),
    )
    monkeypatch.setattr("omni_sdk.contracts.deployer.make_package_bytes", lambda **_kwargs: b"\xA1\x01")
    monkeypatch.setattr(
        "omni_sdk.contracts.deployer.build_deploy_tx",
        lambda **_kwargs: {"kind": "deploy", "payload": {"t": 1, "v": {"code": b"\x01", "manifest": b"{}"}}},
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.resolve_signing_context",
        lambda _rpc, chain_id: {"chain_id": chain_id},
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.sign_transaction_for_submission",
        lambda _tx, _signer, context: type("Signed", (), {"raw_tx": b"\x99"})(),
    )
    monkeypatch.setattr("omni_sdk.tx.send.submit_raw", lambda _rpc, _raw: tx_hash)


def test_contract_group_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["contract", "--help"])
    assert result.exit_code == 0, result.output
    for cmd in (
        "compile",
        "deploy",
        "call",
        "send",
        "inspect",
        "address",
        "estimate-gas",
        "encode-calldata",
        "decode-result",
        "list-artifacts",
    ):
        assert cmd in result.output


def test_wallet_label_and_address_resolution(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    _write_wallet_store(wallet_file)

    resolved = wallet_utils.resolve_wallet_address("main", wallet_file=wallet_file)
    assert resolved.startswith("anim1")

    raw = wallet_utils.resolve_wallet_address(resolved, wallet_file=wallet_file)
    assert raw == resolved


def test_contract_compile_writes_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "counter.py"
    source.write_text("def get():\n    return 1\n", encoding="utf-8")

    def _fake_compile(path: Path, *, entrypoint: str | None = None, optimize: bool = True) -> tuple[bytes, dict[str, Any]]:
        _ = (path, entrypoint, optimize)
        return b"\x01\x02\x03", {"pipeline": "test"}

    monkeypatch.setattr(artifact_store, "compile_source_to_ir_bytes", _fake_compile)
    monkeypatch.setattr(
        artifact_store,
        "generate_abi_from_source",
        lambda _src: {
            "functions": [{"name": "get", "inputs": [], "outputs": [{"type": "int"}]}],
            "events": [],
        },
    )

    out = tmp_path / "build" / "counter.avm"
    abi_out = tmp_path / "build" / "counter.abi.json"
    manifest_out = tmp_path / "build" / "counter.manifest.json"
    result = runner.invoke(
        app,
        [
            "contract",
            "compile",
            str(source),
            "--out",
            str(out),
            "--abi-out",
            str(abi_out),
            "--manifest-out",
            str(manifest_out),
            "--overwrite",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert Path(payload["artifact_path"]).exists()
    assert Path(payload["abi_path"]).exists()
    assert Path(payload["manifest_path"]).exists()


@pytest.mark.parametrize(
    "relative_source",
    [
        "contracts/packages/counter/contract.py",
        "contracts/templates/counter/contract.py",
        "vm_py/examples/counter/contract.py",
        "vm_py/examples/min_counter/contract.py",
    ],
)
def test_contract_compile_accepts_repo_counter_examples(relative_source: str) -> None:
    source = REPO_ROOT / relative_source
    assert source.exists(), f"missing source fixture: {source}"
    result = runner.invoke(app, ["contract", "compile", str(source), "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["artifact_path"]
    assert payload["compiler_meta"]["pipeline"]


def test_contract_inspect_source_reports_manifest_abi_and_functions() -> None:
    source = REPO_ROOT / "contracts/packages/counter/contract.py"
    result = runner.invoke(app, ["contract", "inspect", str(source), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["kind"] == "source"
    assert payload["manifest_path"] and payload["manifest_path"].endswith("manifest.json")
    assert "get" in payload["functions"]
    assert "inc" in payload["functions"]
    assert payload["compile_ready"] is True
    assert payload["deployable"] is True


def test_deploy_success_path_and_save(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "counter.avm"
    artifact.write_bytes(b"\xCA\xFE\xBA\xBE")
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [
                    {"name": "constructor", "inputs": [{"name": "start", "type": "int"}], "outputs": []},
                    {"name": "get", "inputs": [], "outputs": [{"type": "int"}]},
                ],
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution(
            "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2"
        ),
    )
    monkeypatch.setattr(
        "omni_sdk.contracts.deployer.make_package_bytes",
        lambda **_kwargs: b"\xA1\x01",
    )
    monkeypatch.setattr(
        "omni_sdk.contracts.deployer.build_deploy_tx",
        lambda **_kwargs: {"kind": "deploy", "payload": {"t": 1, "v": {"code": b"\x01", "manifest": b"{}"}}},
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.resolve_signing_context",
        lambda _rpc, chain_id: {"chain_id": chain_id},
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.sign_transaction_for_submission",
        lambda _tx, _signer, context: type("Signed", (), {"raw_tx": b"\x99"})(),
    )
    monkeypatch.setattr("omni_sdk.tx.send.submit_raw", lambda _rpc, _raw: "0x" + "aa" * 32)
    monkeypatch.setattr(
        contract_cli,
        "_poll_transaction_confirmation",
        lambda _rpc, _tx_hash, **_kwargs: {
            "receipt": {
                "txHash": "0x" + "aa" * 32,
                "contractAddress": "0x" + "11" * 32,
                "blockNumber": 7,
                "gasUsed": 51234,
                "status": "SUCCESS",
            },
            "tx_status": "SUCCESS",
            "block_height": 7,
            "confirmed": True,
            "success": True,
            "timed_out": False,
        },
    )

    result = runner.invoke(
        app,
        [
            "contract",
            "deploy",
            str(artifact),
            "--from",
            "main",
            "--abi",
            str(abi_path),
            "--constructor-args",
            "[5]",
            "--save",
            "--name",
            "counter",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["submitted"] is True
    assert payload["confirmed"] is True
    assert payload["success"] is True
    assert payload["tx_hash"].startswith("0x")
    assert payload["contract_address"] == "0x" + "11" * 32
    assert payload["deployment_name"] == "counter"
    assert payload["saved"] is True
    assert payload["wait_timeout_secs"] == 60
    assert "confirmed successfully" in payload["message"].lower()

    saved = deployment_store.resolve_deployment("counter", key=None)
    assert isinstance(saved, dict)
    assert saved["address"] == "0x" + "11" * 32
    assert saved["tx_hash"] == "0x" + "aa" * 32


def test_deploy_save_fails_when_address_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "counter.avm"
    artifact.write_bytes(b"\xCA\xFE\xBA\xBE")
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [
                    {"name": "constructor", "inputs": [], "outputs": []},
                    {"name": "get", "inputs": [], "outputs": [{"type": "int"}]},
                ],
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution(
            "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2"
        ),
    )
    monkeypatch.setattr("omni_sdk.contracts.deployer.make_package_bytes", lambda **_kwargs: b"\xA1\x01")
    monkeypatch.setattr(
        "omni_sdk.contracts.deployer.build_deploy_tx",
        lambda **_kwargs: {"kind": "deploy", "payload": {"t": 1, "v": {"code": b"\x01", "manifest": b"{}"}}},
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.resolve_signing_context",
        lambda _rpc, chain_id: {"chain_id": chain_id},
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.sign_transaction_for_submission",
        lambda _tx, _signer, context: type("Signed", (), {"raw_tx": b"\x99"})(),
    )
    monkeypatch.setattr("omni_sdk.tx.send.submit_raw", lambda _rpc, _raw: "0x" + "aa" * 32)
    monkeypatch.setattr(
        contract_cli,
        "_poll_transaction_confirmation",
        lambda _rpc, _tx_hash, **_kwargs: {
            "receipt": {
                "txHash": "0x" + "aa" * 32,
                "blockNumber": 7,
                "gasUsed": 51234,
                "status": "SUCCESS",
            },
            "tx_status": "SUCCESS",
            "block_height": 7,
            "confirmed": True,
            "success": True,
            "timed_out": False,
        },
    )
    monkeypatch.setattr(contract_cli, "_derive_contract_address", lambda *a, **k: None)

    result = runner.invoke(
        app,
        [
            "contract",
            "deploy",
            str(artifact),
            "--from",
            "main",
            "--abi",
            str(abi_path),
            "--save",
            "--name",
            "counter",
        ],
    )
    assert result.exit_code != 0
    assert "save failed" in result.output.lower()
    assert "address could not be resolved" in result.output.lower()


def test_deploy_wait_save_timeout_returns_partial_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "counter.avm"
    artifact.write_bytes(b"\xCA\xFE\xBA\xBE")
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps({"functions": [{"name": "constructor", "inputs": [], "outputs": []}], "events": []}),
        encoding="utf-8",
    )

    _patch_deploy_submission_primitives(monkeypatch)
    observed: dict[str, Any] = {}

    def _fake_poll(_rpc: Any, _tx_hash: str, **kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {
            "receipt": None,
            "tx_status": "PENDING",
            "block_height": None,
            "confirmed": False,
            "success": None,
            "timed_out": True,
        }

    monkeypatch.setattr(contract_cli, "_poll_transaction_confirmation", _fake_poll)
    monkeypatch.setattr(contract_cli, "_derive_contract_address", lambda *a, **k: None)

    result = runner.invoke(
        app,
        [
            "contract",
            "deploy",
            str(artifact),
            "--from",
            "main",
            "--abi",
            str(abi_path),
            "--wait",
            "--wait-timeout-secs",
            "2",
            "--poll-interval-secs",
            "0.1",
            "--save",
            "--name",
            "counter",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["submitted"] is True
    assert payload["confirmed"] is False
    assert payload["success"] is None
    assert payload["saved"] is False
    assert payload["wait_timeout_secs"] == 2
    assert "not confirmed before timeout" in payload["message"].lower()
    assert "save was skipped pending confirmation" in payload["message"].lower()
    assert deployment_store.resolve_deployment("counter", key=None) is None
    assert observed["wait_timeout_secs"] == 2.0
    assert observed["poll_interval_secs"] == 0.1


def test_deploy_no_wait_returns_tx_hash_without_waiting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "counter.avm"
    artifact.write_bytes(b"\xAA")
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps({"functions": [{"name": "constructor", "inputs": [], "outputs": []}], "events": []}),
        encoding="utf-8",
    )

    _patch_deploy_submission_primitives(monkeypatch, tx_hash="0x" + "ab" * 32)
    monkeypatch.setattr(
        contract_cli,
        "_poll_transaction_confirmation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("wait poll should not run for --no-wait")),
    )

    result = runner.invoke(
        app,
        [
            "contract",
            "deploy",
            str(artifact),
            "--from",
            "main",
            "--abi",
            str(abi_path),
            "--no-wait",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["submitted"] is True
    assert payload["confirmed"] is False
    assert payload["success"] is None
    assert payload["tx_hash"] == "0x" + "ab" * 32
    assert payload["saved"] is False
    assert "without waiting" in payload["message"].lower()


def test_deploy_no_wait_with_save_returns_actionable_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = tmp_path / "counter.avm"
    artifact.write_bytes(b"\xAB")
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps({"functions": [{"name": "constructor", "inputs": [], "outputs": []}], "events": []}),
        encoding="utf-8",
    )

    _patch_deploy_submission_primitives(monkeypatch, tx_hash="0x" + "ac" * 32)

    result = runner.invoke(
        app,
        [
            "contract",
            "deploy",
            str(artifact),
            "--from",
            "main",
            "--abi",
            str(abi_path),
            "--no-wait",
            "--save",
            "--name",
            "counter",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["submitted"] is True
    assert payload["confirmed"] is False
    assert payload["saved"] is False
    assert payload["save_requested"] is True
    assert "save-deployment" in payload["message"]
    assert "contract receipt" in payload["message"]
    assert deployment_store.resolve_deployment("counter", key=None) is None


def test_deploy_missing_wallet_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "counter.avm"
    artifact.write_bytes(b"\xCA\xFE")

    def _raise_wallet(**_kwargs: Any) -> Any:
        raise typer.BadParameter("wallet label not found")

    import typer

    monkeypatch.setattr(wallet_utils, "resolve_signer", _raise_wallet)

    result = runner.invoke(app, ["contract", "deploy", str(artifact), "--from", "missing"])
    assert result.exit_code != 0
    assert "wallet label not found" in result.output.lower()


def test_deploy_invalid_abi_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "counter.avm"
    artifact.write_bytes(b"\xAA")
    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution(
            "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2"
        ),
    )
    result = runner.invoke(
        app,
        [
            "contract",
            "deploy",
            str(artifact),
            "--from",
            "main",
            "--abi",
            str(tmp_path / "missing.json"),
        ],
    )
    assert result.exit_code != 0
    assert "abi file not found" in result.output.lower()


def test_deploy_bad_sender_address_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "counter.avm"
    artifact.write_bytes(b"\xAA")
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps({"functions": [{"name": "constructor", "inputs": [], "outputs": []}], "events": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution("not-a-valid-address"),
    )
    monkeypatch.setattr("omni_sdk.contracts.deployer.make_package_bytes", lambda **_kwargs: b"\x01")
    monkeypatch.setattr(
        "omni_sdk.contracts.deployer.build_deploy_tx",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("invalid from_addr 'not-a-valid-address'")),
    )

    result = runner.invoke(
        app,
        [
            "contract",
            "deploy",
            str(artifact),
            "--from",
            "main",
            "--abi",
            str(abi_path),
        ],
    )
    assert result.exit_code != 0
    assert "invalid from_addr" in result.output.lower()


def test_save_deployment_stores_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://127.0.0.1:18545/rpc")
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [{"name": "get", "inputs": [], "outputs": [{"type": "int"}]}],
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "contract",
            "save-deployment",
            "--name",
            "counter",
            "--address",
            "0x" + "12" * 32,
            "--abi",
            str(abi_path),
            "--tx-hash",
            "0x" + "34" * 32,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["saved"] is True
    assert payload["deployment_name"] == "counter"
    assert payload["address"] == "0x" + "12" * 32

    saved = deployment_store.resolve_deployment("counter", key=None)
    assert isinstance(saved, dict)
    assert saved["address"] == "0x" + "12" * 32
    assert saved["tx_hash"] == "0x" + "34" * 32
    assert saved["abi_path"] == str(abi_path.resolve())


def test_save_deployment_alias_can_be_used_by_call_and_send(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://127.0.0.1:18545/rpc")
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [
                    {"name": "get", "inputs": [], "outputs": [{"type": "int"}]},
                    {"name": "increment", "inputs": [], "outputs": []},
                ],
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    save_result = runner.invoke(
        app,
        [
            "contract",
            "save-deployment",
            "--name",
            "counter",
            "--address",
            "0x" + "56" * 32,
            "--abi",
            str(abi_path),
            "--tx-hash",
            "0x" + "78" * 32,
            "--json",
        ],
    )
    assert save_result.exit_code == 0, save_result.output

    monkeypatch.setattr(
        contract_cli,
        "_simulate_call_with_fallback",
        lambda **_kwargs: ("state.call", "0x00", b"\x00", 11, None),
    )
    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution(
            "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2"
        ),
    )
    monkeypatch.setattr(contract_cli, "_resolve_nonce", lambda _rpc, _sender, _override: 4)
    monkeypatch.setattr(
        "omni_sdk.tx.signing.sign_transaction_with_rpc_context",
        lambda _tx, _signer, chain_id, rpc: type("Signed", (), {"raw_tx": b"\x01"})(),
    )
    monkeypatch.setattr("omni_sdk.tx.send.submit_raw", lambda _rpc, _raw: "0x" + "89" * 32)
    monkeypatch.setattr(
        contract_cli,
        "wait_for_receipt",
        lambda _rpc, _tx_hash, **_kwargs: {
            "txHash": "0x" + "89" * 32,
            "status": "SUCCESS",
            "blockNumber": 23,
            "gasUsed": 9000,
        },
    )

    call_result = runner.invoke(app, ["contract", "call", "counter", "get", "--json"])
    assert call_result.exit_code == 0, call_result.output
    call_payload = json.loads(call_result.output)
    assert call_payload["decoded_result"] == 11
    assert call_payload["address"] == "0x" + "56" * 32

    send_result = runner.invoke(
        app,
        [
            "contract",
            "send",
            "counter",
            "increment",
            "--from",
            "main",
            "--wait",
            "--json",
        ],
    )
    assert send_result.exit_code == 0, send_result.output
    send_payload = json.loads(send_result.output)
    assert send_payload["tx_hash"] == "0x" + "89" * 32
    assert send_payload["tx_status"] == "SUCCESS"


def test_contract_receipt_reports_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://127.0.0.1:18545/rpc")
    monkeypatch.setattr(
        contract_cli,
        "_poll_transaction_confirmation",
        lambda _rpc, _tx_hash, **_kwargs: {
            "receipt": {
                "txHash": "0x" + "de" * 32,
                "contractAddress": "0x" + "fe" * 32,
                "status": "SUCCESS",
                "blockNumber": 15,
                "gasUsed": 1010,
            },
            "tx_status": "SUCCESS",
            "block_height": 15,
            "confirmed": True,
            "success": True,
            "timed_out": False,
        },
    )

    result = runner.invoke(
        app,
        [
            "contract",
            "receipt",
            "0x" + "de" * 32,
            "--wait",
            "--wait-timeout-secs",
            "3",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["confirmed"] is True
    assert payload["success"] is True
    assert payload["contract_address"] == "0x" + "fe" * 32
    assert payload["tx_hash"] == "0x" + "de" * 32


def test_call_decode_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [
                    {"name": "get", "inputs": [], "outputs": [{"type": "int"}]},
                ],
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    deployment_store.save_deployment_record(
        {
            "name": "counter",
            "address": "0x" + "22" * 32,
            "abi_path": str(abi_path),
            "tx_hash": "0x" + "33" * 32,
        },
        key=deployment_store.network_key(chain_id=1337, network="devnet"),
    )

    monkeypatch.setattr(
        contract_cli,
        "_simulate_call_with_fallback",
        lambda **_kwargs: ("state.call", "0x00", b"\x00", 42, None),
    )

    result = runner.invoke(app, ["contract", "call", "counter", "get", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["decoded_result"] == 42
    assert payload["address"] == "0x" + "22" * 32


def test_send_wait_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [
                    {"name": "increment", "inputs": [], "outputs": []},
                ],
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    deployment_store.save_deployment_record(
        {
            "name": "counter",
            "address": "0x" + "44" * 32,
            "abi_path": str(abi_path),
            "tx_hash": "0x" + "55" * 32,
        },
        key=deployment_store.network_key(chain_id=1337, network="devnet"),
    )

    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution(
            "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2"
        ),
    )
    monkeypatch.setattr(contract_cli, "_resolve_nonce", lambda _rpc, _sender, _override: 9)
    monkeypatch.setattr(
        "omni_sdk.tx.signing.sign_transaction_with_rpc_context",
        lambda _tx, _signer, chain_id, rpc: type("Signed", (), {"raw_tx": b"\x01"})(),
    )
    monkeypatch.setattr("omni_sdk.tx.send.submit_raw", lambda _rpc, _raw: "0x" + "66" * 32)
    monkeypatch.setattr(
        contract_cli,
        "wait_for_receipt",
        lambda _rpc, _tx_hash, **_kwargs: {
            "txHash": "0x" + "66" * 32,
            "status": "SUCCESS",
            "blockNumber": 11,
            "gasUsed": 12000,
        },
    )

    result = runner.invoke(
        app,
        [
            "contract",
            "send",
            "counter",
            "increment",
            "--from",
            "main",
            "--wait",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["tx_hash"] == "0x" + "66" * 32
    assert payload["tx_status"] == "SUCCESS"
    assert payload["block_height"] == 11
    assert payload["gas_used"] == 12000


def test_send_accepts_increment_alias_for_inc_method(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi_path = tmp_path / "counter.abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [
                    {
                        "name": "inc",
                        "inputs": [{"name": "delta", "type": "int"}],
                        "outputs": [{"name": "value", "type": "int"}],
                    },
                    {"name": "get", "inputs": [], "outputs": [{"name": "value", "type": "int"}]},
                ],
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    deployment_store.save_deployment_record(
        {
            "name": "counter",
            "address": "0x" + "99" * 32,
            "abi_path": str(abi_path),
            "tx_hash": "0x" + "11" * 32,
        },
        key=deployment_store.network_key(chain_id=1337, network="devnet"),
    )

    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution(
            "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2"
        ),
    )
    monkeypatch.setattr(contract_cli, "_resolve_nonce", lambda _rpc, _sender, _override: 2)
    monkeypatch.setattr(
        "omni_sdk.tx.signing.sign_transaction_with_rpc_context",
        lambda _tx, _signer, chain_id, rpc: type("Signed", (), {"raw_tx": b"\x01"})(),
    )
    monkeypatch.setattr("omni_sdk.tx.send.submit_raw", lambda _rpc, _raw: "0x" + "22" * 32)
    monkeypatch.setattr(
        contract_cli,
        "wait_for_receipt",
        lambda _rpc, _tx_hash, **_kwargs: {
            "txHash": "0x" + "22" * 32,
            "status": "SUCCESS",
            "blockNumber": 19,
            "gasUsed": 5000,
        },
    )

    result = runner.invoke(
        app,
        [
            "contract",
            "send",
            "counter",
            "increment",
            "--args",
            "[1]",
            "--from",
            "main",
            "--wait",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["tx_status"] == "SUCCESS"


def test_address_alias_resolution(tmp_path: Path) -> None:
    deployment_store.save_deployment_record(
        {
            "name": "counter",
            "address": "0x" + "77" * 32,
            "tx_hash": "0x" + "88" * 32,
        },
        key=deployment_store.network_key(chain_id=1337, network="devnet"),
    )
    result = runner.invoke(app, ["contract", "address", "counter", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["address"] == "0x" + "77" * 32


def test_list_artifacts_json_mode(tmp_path: Path) -> None:
    art_path = tmp_path / "a.avm"
    art_path.write_bytes(b"\x01\x02")
    artifact_store.save_artifact_record(
        artifact_store.infer_artifact_metadata(
            artifact_path=art_path,
            code_hash=artifact_store.sha3_256_hex(b"\x01\x02"),
            contract_name="Counter",
        )
    )
    deployment_store.save_deployment_record(
        {
            "name": "counter",
            "address": "0x" + "99" * 32,
            "tx_hash": "0x" + "aa" * 32,
        },
        key=deployment_store.network_key(chain_id=1337, network="devnet"),
    )

    result = runner.invoke(app, ["contract", "list-artifacts", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["artifact_count"] >= 1
    assert payload["deployment_count"] >= 1


def test_counter_happy_path_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "counter.py"
    source.write_text("def get():\n    return 0\n", encoding="utf-8")

    monkeypatch.setattr(
        artifact_store,
        "compile_source_to_ir_bytes",
        lambda _src, **_kwargs: (b"\xDE\xAD", {"pipeline": "test"}),
    )
    monkeypatch.setattr(
        artifact_store,
        "generate_abi_from_source",
        lambda _src: {
            "functions": [
                {"name": "get", "inputs": [], "outputs": [{"type": "int"}]},
                {"name": "increment", "inputs": [], "outputs": []},
            ],
            "events": [],
        },
    )
    monkeypatch.setattr(
        wallet_utils,
        "resolve_signer",
        lambda **_kwargs: _FakeSignerResolution(
            "anim1q0j0u8j7s6g5f4d3c2b1a0mnpqrstuvwxzy1234567890abcdefghjkqf8z2"
        ),
    )
    monkeypatch.setattr("omni_sdk.contracts.deployer.make_package_bytes", lambda **_kwargs: b"\x01")
    monkeypatch.setattr(
        "omni_sdk.contracts.deployer.build_deploy_tx",
        lambda **_kwargs: {"kind": "deploy", "payload": {"t": 1, "v": {"code": b"\x01", "manifest": b"{}"}}},
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.resolve_signing_context",
        lambda _rpc, chain_id: {"chain_id": chain_id},
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.sign_transaction_for_submission",
        lambda _tx, _signer, context: type("Signed", (), {"raw_tx": b"\x77"})(),
    )
    monkeypatch.setattr(
        "omni_sdk.tx.signing.sign_transaction_with_rpc_context",
        lambda _tx, _signer, chain_id, rpc: type("Signed", (), {"raw_tx": b"\x55"})(),
    )
    monkeypatch.setattr(
        "omni_sdk.tx.send.submit_raw",
        lambda _rpc, _raw: "0x" + "ab" * 32,
    )
    monkeypatch.setattr(
        contract_cli,
        "_poll_transaction_confirmation",
        lambda _rpc, _tx_hash, **_kwargs: {
            "receipt": {
                "txHash": "0x" + "ab" * 32,
                "contractAddress": "0x" + "cd" * 32,
                "status": "SUCCESS",
                "blockNumber": 21,
                "gasUsed": 10001,
            },
            "tx_status": "SUCCESS",
            "block_height": 21,
            "confirmed": True,
            "success": True,
            "timed_out": False,
        },
    )
    monkeypatch.setattr(
        contract_cli,
        "wait_for_receipt",
        lambda _rpc, _tx_hash, **_kwargs: {
            "txHash": "0x" + "ab" * 32,
            "status": "SUCCESS",
            "blockNumber": 21,
            "gasUsed": 10001,
        },
    )
    monkeypatch.setattr(contract_cli, "_resolve_nonce", lambda _rpc, _sender, _override: 3)

    call_values = [0, 1]

    def _fake_simulate(**_kwargs: Any) -> tuple[str, Any, bytes | None, Any, dict[str, Any] | None]:
        value = call_values.pop(0)
        return ("state.call", "0x00", b"\x00", value, {"source": "test"})

    monkeypatch.setattr(contract_cli, "_simulate_call_with_fallback", _fake_simulate)

    compile_res = runner.invoke(
        app,
        [
            "contract",
            "compile",
            str(source),
            "--out",
            str(tmp_path / "counter.avm"),
            "--abi-out",
            str(tmp_path / "counter.abi.json"),
            "--overwrite",
        ],
    )
    assert compile_res.exit_code == 0, compile_res.output

    deploy_res = runner.invoke(
        app,
        [
            "contract",
            "deploy",
            str(tmp_path / "counter.avm"),
            "--from",
            "main",
            "--abi",
            str(tmp_path / "counter.abi.json"),
            "--save",
            "--name",
            "counter",
            "--json",
        ],
    )
    assert deploy_res.exit_code == 0, deploy_res.output
    deploy_payload = json.loads(deploy_res.output)
    assert deploy_payload["contract_address"] == "0x" + "cd" * 32

    call_before = runner.invoke(app, ["contract", "call", "counter", "get", "--json"])
    assert call_before.exit_code == 0, call_before.output
    assert json.loads(call_before.output)["decoded_result"] == 0

    send_res = runner.invoke(
        app,
        ["contract", "send", "counter", "increment", "--from", "main", "--wait", "--json"],
    )
    assert send_res.exit_code == 0, send_res.output
    assert json.loads(send_res.output)["tx_status"] == "SUCCESS"

    call_after = runner.invoke(app, ["contract", "call", "counter", "get", "--json"])
    assert call_after.exit_code == 0, call_after.output
    assert json.loads(call_after.output)["decoded_result"] == 1
