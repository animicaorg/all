from __future__ import annotations

from typer.testing import CliRunner

from animica.cli import rpc as rpc_cli

runner = CliRunner()


def _install_mock(monkeypatch):
    calls = {}

    class MockRpcClient:
        def __init__(self, url, timeout=None, headers=None):
            calls["url"] = url
            calls["timeout"] = timeout
            calls["headers"] = headers

        def request(self, method, params):
            calls["method"] = method
            calls["params"] = params
            return {"ok": True}

    monkeypatch.setattr(rpc_cli, "HAVE_RPC", True)
    monkeypatch.setattr(rpc_cli, "RpcClient", MockRpcClient, raising=False)
    monkeypatch.setattr(rpc_cli, "guard_bootstrap_rpc", lambda *args, **kwargs: None)
    return calls


def test_rpc_call_accepts_bare_param(monkeypatch) -> None:
    calls = _install_mock(monkeypatch)

    result = runner.invoke(
        rpc_cli.app,
        [
            "state.getNextNonce",
            "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
            "--rpc-url",
            "http://127.0.0.1:8545/rpc",
        ],
    )

    assert result.exit_code == 0
    assert calls["params"] == ["anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y"]


def test_rpc_call_accepts_json_list_param(monkeypatch) -> None:
    calls = _install_mock(monkeypatch)

    result = runner.invoke(
        rpc_cli.app,
        [
            "state.getNextNonce",
            '["anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y"]',
            "--rpc-url",
            "http://127.0.0.1:8545/rpc",
        ],
    )

    assert result.exit_code == 0
    assert calls["params"] == ["anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y"]


def test_rpc_call_accepts_json_object_param(monkeypatch) -> None:
    calls = _install_mock(monkeypatch)

    result = runner.invoke(
        rpc_cli.app,
        [
            "state.getNextNonce",
            '{"address":"anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y"}',
            "--rpc-url",
            "http://127.0.0.1:8545/rpc",
        ],
    )

    assert result.exit_code == 0
    assert calls["params"] == {"address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y"}


def test_rpc_call_accepts_multiple_params(monkeypatch) -> None:
    calls = _install_mock(monkeypatch)

    result = runner.invoke(
        rpc_cli.app,
        [
            "state.getNextNonce",
            "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
            "2",
            "--rpc-url",
            "http://127.0.0.1:8545/rpc",
        ],
    )

    assert result.exit_code == 0
    assert calls["params"] == [
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y",
        2,
    ]
