from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _write_abi(tmp_path: Path, *, func_name: str = "get", output_type: str = "u64") -> Path:
    abi_path = tmp_path / "abi.json"
    abi_path.write_text(
        json.dumps(
            {
                "functions": [
                    {
                        "name": func_name,
                        "inputs": [],
                        "outputs": [{"name": "value", "type": output_type}],
                    }
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


def _patch_write_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Signer:
        address = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j"
        public_key = bytes([1] * 32)
        alg_id = 0x1002
        alg_name = "sphincs_shake_128s"

    monkeypatch.setattr(
        call_cli,
        "_resolve_signer",
        lambda **_kwargs: _Signer(),
    )
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x01")
    monkeypatch.setattr(call_cli.tx_build, "call", lambda **kwargs: {"tx": kwargs})
    monkeypatch.setattr(
        call_cli.tx_signing,
        "sign_transaction_with_rpc_context",
        lambda *_a, **_k: SimpleNamespace(raw_tx=b"\xaa"),
    )


def test_call_read_emits_structured_json_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi = _write_abi(tmp_path, func_name="get")
    _patch_rpc_client(monkeypatch, lambda _method, _params: "0x0102")
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x00")
    monkeypatch.setattr(call_cli, "decode_return", lambda _abi, _fn, _raw: {"value": 2})

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "read",
            "--rpc",
            "http://127.0.0.1:8545/rpc",
            "--address",
            "0x" + "11" * 32,
            "--abi",
            str(abi),
            "--func",
            "get",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["rpc_url"] == "http://127.0.0.1:8545/rpc"
    assert payload["chain_id"] == 1
    assert payload["address"] == "0x" + "11" * 32
    assert payload["func"] == "get"
    assert payload["args"] == []
    assert payload["result"] == "0x0102"
    assert payload["decoded_result"] == {"value": 2}


def test_call_module_main_read_emits_json_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    abi = _write_abi(tmp_path, func_name="get")
    _patch_rpc_client(monkeypatch, lambda _method, _params: "0x0003")
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x00")
    monkeypatch.setattr(call_cli, "decode_return", lambda _abi, _fn, _raw: 3)

    rc = call_cli.main(
        [
            "read",
            "--rpc",
            "http://127.0.0.1:8545/rpc",
            "--chain-id",
            "1",
            "--address",
            "0x" + "12" * 32,
            "--abi",
            str(abi),
            "--func",
            "get",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["decoded_result"] == 3


def test_call_write_emits_structured_json_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi = _write_abi(tmp_path, func_name="set")
    _patch_write_flow(monkeypatch)
    _patch_rpc_client(
        monkeypatch,
        lambda method, _params: 9 if method == "state.getNonce" else {},
    )
    monkeypatch.setattr(call_cli.tx_send, "submit_raw", lambda *_a, **_k: "0xabc123")
    monkeypatch.setattr(
        call_cli.tx_send,
        "wait_for_receipt",
        lambda *_a, **_k: {"status": "SUCCESS", "blockNumber": 12, "gasUsed": 99},
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
            "set",
            "--args-json",
            "[3]",
            "--seed-hex",
            "11" * 32,
            "--wait",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["rpc_url"] == "http://127.0.0.1:8545/rpc"
    assert payload["chain_id"] == 1
    assert payload["address"] == "0x" + "22" * 32
    assert payload["func"] == "set"
    assert payload["args"] == [3]
    assert payload["sender"]
    assert payload["tx_hash"] == "0xabc123"
    assert payload["tx_status"] == "SUCCESS"
    assert payload["block_number"] == 12
    assert payload["receipt"]["gasUsed"] == 99
    assert payload["wait"] is True


def test_call_module_main_write_emits_json_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    abi = _write_abi(tmp_path, func_name="set")
    _patch_write_flow(monkeypatch)
    _patch_rpc_client(
        monkeypatch,
        lambda method, _params: 9 if method == "state.getNonce" else {},
    )
    monkeypatch.setattr(call_cli.tx_send, "submit_raw", lambda *_a, **_k: "0xabc999")
    monkeypatch.setattr(
        call_cli.tx_send,
        "wait_for_receipt",
        lambda *_a, **_k: {"status": "SUCCESS", "blockNumber": 18, "gasUsed": 5},
    )

    rc = call_cli.main(
        [
            "write",
            "--rpc",
            "http://127.0.0.1:8545/rpc",
            "--chain-id",
            "1",
            "--address",
            "0x" + "23" * 32,
            "--abi",
            str(abi),
            "--func",
            "set",
            "--args-json",
            "[9]",
            "--seed-hex",
            "12" * 32,
            "--wait",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["tx_hash"] == "0xabc999"
    assert payload["block_number"] == 18


def test_call_read_with_primitive_return_is_wrapped_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi = _write_abi(tmp_path, func_name="get")
    _patch_rpc_client(monkeypatch, lambda _method, _params: "0x01")
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x00")
    monkeypatch.setattr(call_cli, "decode_return", lambda _abi, _fn, _raw: 7)

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "read",
            "--address",
            "0x" + "33" * 32,
            "--abi",
            str(abi),
            "--func",
            "get",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["decoded_result"] == 7
    assert payload["status"] == "ok"


def test_call_write_tx_hash_with_wait_timeout_still_emits_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi = _write_abi(tmp_path, func_name="set")
    _patch_write_flow(monkeypatch)
    _patch_rpc_client(
        monkeypatch,
        lambda method, _params: 3 if method == "state.getNonce" else {},
    )
    monkeypatch.setattr(call_cli.tx_send, "submit_raw", lambda *_a, **_k: "0xdeadbeef")

    def _raise_timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("receipt not available yet")

    monkeypatch.setattr(call_cli.tx_send, "wait_for_receipt", _raise_timeout)

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "write",
            "--address",
            "0x" + "44" * 32,
            "--abi",
            str(abi),
            "--func",
            "set",
            "--args-json",
            "[5]",
            "--seed-hex",
            "22" * 32,
            "--wait",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["tx_hash"] == "0xdeadbeef"
    assert payload["receipt"] is None
    assert payload["wait"] is True
    assert "wait_error" in payload


def test_call_read_client_error_exits_nonzero_and_writes_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    abi = _write_abi(tmp_path, func_name="get")
    _patch_rpc_client(
        monkeypatch,
        lambda method, _params: (_ for _ in ()).throw(RuntimeError(f"{method} failed")),
    )
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x00")

    rc = call_cli.main(
        [
            "read",
            "--rpc",
            "http://127.0.0.1:8545/rpc",
            "--chain-id",
            "1",
            "--address",
            "0x" + "55" * 32,
            "--abi",
            str(abi),
            "--func",
            "get",
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc != 0
    assert "probed methods" in combined or "failed" in combined


def test_call_module_entrypoint_help_not_silent() -> None:
    root = _repo_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "sdk" / "python")

    res = subprocess.run(
        [sys.executable, "-m", "omni_sdk.cli.call", "--help"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode == 0, res.stderr
    assert "Usage" in res.stdout
    assert "read" in res.stdout
    assert "write" in res.stdout


def test_call_read_decoded_none_still_emits_success_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi = _write_abi(tmp_path, func_name="get")
    _patch_rpc_client(monkeypatch, lambda _method, _params: "0x00")
    monkeypatch.setattr(call_cli, "encode_call", lambda _abi, _fn, _args: b"\x00")
    monkeypatch.setattr(call_cli, "decode_return", lambda _abi, _fn, _raw: None)

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "read",
            "--address",
            "0x" + "66" * 32,
            "--abi",
            str(abi),
            "--func",
            "get",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert "decoded_result" in payload
    assert payload["decoded_result"] is None


def test_call_write_null_receipt_fields_still_emits_success_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    abi = _write_abi(tmp_path, func_name="set")
    _patch_write_flow(monkeypatch)
    _patch_rpc_client(
        monkeypatch,
        lambda method, _params: 4 if method == "state.getNonce" else {},
    )
    monkeypatch.setattr(call_cli.tx_send, "submit_raw", lambda *_a, **_k: "0xfacefeed")
    monkeypatch.setattr(
        call_cli.tx_send,
        "wait_for_receipt",
        lambda *_a, **_k: {"status": None, "blockNumber": None, "gasUsed": None},
    )

    result = runner.invoke(
        app,
        [
            "--chain-id",
            "1",
            "call",
            "write",
            "--address",
            "0x" + "77" * 32,
            "--abi",
            str(abi),
            "--func",
            "set",
            "--args-json",
            "[8]",
            "--seed-hex",
            "33" * 32,
            "--wait",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["tx_hash"] == "0xfacefeed"
    assert payload["tx_status"] is None
    assert payload["block_number"] is None
    assert payload["receipt"] == {"status": None, "blockNumber": None, "gasUsed": None}
