from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict

import pytest
from typer.testing import CliRunner

try:
    from omni_sdk.cli.main import app
    from omni_sdk.cli import call as call_cli
except Exception as exc:  # pragma: no cover - import-time guard
    pytest.skip(f"omni_sdk CLI unavailable: {exc}", allow_module_level=True)


runner = CliRunner()


def _write_abi(tmp_path: Path, func_name: str) -> Path:
    abi_path = tmp_path / "abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [
                    {"name": func_name, "inputs": [], "outputs": []},
                ],
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    return abi_path


def _patch_rpc_client(
    monkeypatch: pytest.MonkeyPatch,
    request_impl: Callable[[str, Any], Any],
) -> Dict[str, Any]:
    captured: Dict[str, Any] = {"urls": [], "calls": []}

    class _RpcStub:
        def __init__(self, url: str, timeout: float | None = None):
            captured["urls"].append(url)
            captured["timeout"] = timeout

        def request(self, method: str, params: Any = None) -> Any:
            captured["calls"].append((method, params))
            return request_impl(method, params)

    monkeypatch.setattr(call_cli, "RpcClient", _RpcStub)
    return captured


def test_call_read_rpc_flag_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    abi = _write_abi(tmp_path, "get")
    captured = _patch_rpc_client(monkeypatch, lambda _m, _p: "0x00")
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x00")
    monkeypatch.setattr(call_cli, "decode_return", lambda _abi, _fn, _raw: {"ok": True})

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "read",
            "--rpc",
            "https://flag.animica.org/rpc",
            "--address",
            "0x" + "11" * 32,
            "--abi",
            str(abi),
            "--func",
            "get",
        ],
        env={"OMNI_RPC_URL": "https://env.animica.org/rpc"},
    )

    assert result.exit_code == 0, result.output
    assert captured["urls"][-1] == "https://flag.animica.org/rpc"


def test_call_read_honors_omni_rpc_url_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    abi = _write_abi(tmp_path, "get")
    captured = _patch_rpc_client(monkeypatch, lambda _m, _p: "0x00")
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x00")
    monkeypatch.setattr(call_cli, "decode_return", lambda _abi, _fn, _raw: {"ok": True})

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "read",
            "--address",
            "0x" + "11" * 32,
            "--abi",
            str(abi),
            "--func",
            "get",
        ],
        env={
            "OMNI_RPC_URL": "https://rpc.animica.org/rpc",
            "OMNI_SDK_RPC_URL": "http://127.0.0.1:8545/rpc",
        },
    )

    assert result.exit_code == 0, result.output
    assert captured["urls"][-1] == "https://rpc.animica.org/rpc"


def test_call_read_reports_probed_simulation_methods(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi = _write_abi(tmp_path, "get")

    def _always_fail(method: str, _params: Any) -> Any:
        raise RuntimeError(f"{method} unavailable")

    _patch_rpc_client(monkeypatch, _always_fail)
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x00")
    monkeypatch.setattr(call_cli, "decode_return", lambda _abi, _fn, _raw: {"ok": True})

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "read",
            "--address",
            "0x" + "11" * 32,
            "--abi",
            str(abi),
            "--func",
            "get",
        ],
    )

    assert result.exit_code != 0
    assert "probed methods: state.call, execution.simulateCall, vm.simulateCall" in result.output


def test_call_write_accepts_wallet_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi = _write_abi(tmp_path, "inc")
    wallet_file = tmp_path / "wallets.json"
    wallet_file.write_text("{}", encoding="utf-8")

    captured = _patch_rpc_client(
        monkeypatch,
        lambda method, _params: {"height": 5} if method == "chain.getHead" else {},
    )
    captured_wallet: Dict[str, Any] = {}

    class _Signer:
        address = None
        public_key = bytes([1] * 32)
        alg_id = 0x1002
        alg_name = "sphincs_shake_128s"

    def _fake_make_signer_from_wallet(path: Path, label: str, _alg: str | None):
        captured_wallet["path"] = path
        captured_wallet["label"] = label
        return _Signer()

    monkeypatch.setattr(call_cli, "_make_signer_from_wallet", _fake_make_signer_from_wallet)
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x01")
    monkeypatch.setattr(
        call_cli.tx_signing,
        "sign_transaction_with_rpc_context",
        lambda *_a, **_k: SimpleNamespace(raw_tx=b"\xaa"),
    )
    monkeypatch.setattr(call_cli.tx_send, "submit_raw", lambda *_a, **_k: "0xabc")
    monkeypatch.setattr(
        call_cli.tx_send,
        "wait_for_receipt",
        lambda *_a, **_k: {"status": "SUCCESS", "gasUsed": 1, "blockNumber": 7},
    )

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "write",
            "--rpc",
            "http://127.0.0.1:8545/rpc",
            "--address",
            "0x" + "22" * 32,
            "--abi",
            str(abi),
            "--func",
            "inc",
            "--wallet-file",
            str(wallet_file),
            "--wallet-label",
            "test",
            "--max-fee",
            "1",
            "--wait",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_wallet["path"] == wallet_file
    assert captured_wallet["label"] == "test"
    assert captured["urls"][-1] == "http://127.0.0.1:8545/rpc"
    payload = json.loads(result.stdout)
    assert payload["txHash"] == "0xabc"
