from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_sdk.errors import RpcError


def _load_deploy_counter_module():
    path = Path(__file__).resolve().parents[1] / "examples" / "deploy_counter.py"
    spec = importlib.util.spec_from_file_location("deploy_counter_test_module", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("failed to load deploy_counter.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_last_json_blob(stdout: str) -> dict:
    idx = stdout.find("{")
    assert idx >= 0, f"no JSON payload in output: {stdout!r}"
    return json.loads(stdout[idx:])


def test_deploy_counter_replay_outputs_actionable_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _load_deploy_counter_module()
    tx_hash = "0x" + ("ab" * 32)
    sender = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j"

    class _RpcStub:
        def __init__(self, url: str, timeout: float = 30.0):
            self.url = url
            self.timeout = timeout

        def request(self, method: str, params=None):
            if method == "state.getNonce":
                if params == [sender, "pending"]:
                    return 0
                return 0
            if method == "tx.getTransactionReceipt":
                return None
            if method == "tx.getStatus":
                return {"hash": tx_hash, "status": "pending"}
            raise RuntimeError(f"unexpected RPC method: {method}")

    def _fake_deploy_package(**_kwargs):
        raise RpcError(
            code=-32010,
            message="mempool admission failed: replay",
            method="tx.sendRawTransaction",
            data={
                "mempoolError": {
                    "reason_code": "replay",
                    "reason": "replay",
                    "context": {"tx_hash": tx_hash},
                }
            },
        )

    fake_signer = SimpleNamespace(
        address=sender,
        alg_name="sphincs_shake_128s",
        alg_id=0x1002,
        public_key=bytes([1] * 32),
    )

    monkeypatch.setattr(mod, "RpcClient", _RpcStub)
    monkeypatch.setattr(mod, "deploy_package", _fake_deploy_package)
    monkeypatch.setattr(mod, "_make_signer_from_wallet", lambda _p, _l: fake_signer)
    monkeypatch.setattr(mod, "_load_json", lambda _p: {"abi": {"functions": [], "events": []}})
    monkeypatch.setattr(mod, "_load_code_bytes", lambda _p: b"contract")

    argv = [
        "deploy_counter.py",
        "--rpc",
        "https://rpc.animica.org/rpc",
        "--chain-id",
        "1",
        "--wallet-file",
        "/tmp/wallets.json",
        "--wallet-label",
        "test",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exit_info:
        mod.main()
    assert exit_info.value.code == 2

    captured = capsys.readouterr()
    payload = _extract_last_json_blob(captured.out)
    assert payload["error"] == "replay"
    assert payload["txHash"] == tx_hash
    assert payload["diagnostics"]["nonceLatest"] == 0
    assert payload["diagnostics"]["noncePending"] == 0
    assert payload["nextSteps"]
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
