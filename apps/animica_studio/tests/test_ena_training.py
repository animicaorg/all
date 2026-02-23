from __future__ import annotations

import json
from pathlib import Path

from animica_studio.services.ena_client import EnaError, EnaMode, EnaProfile, safe_text
from animica_studio.services.ena_tools import ToolContext, ToolPolicy, check_tool_allowed
from animica_studio.services.training_push import TrainingPushService


def test_safe_text_never_object_object() -> None:
    value = {"a": 1}
    out = safe_text(value)
    assert out.startswith("{")
    assert "[object Object]" not in out


def test_tool_policy_gating() -> None:
    ctx = ToolContext(workspace=Path.cwd(), policy=ToolPolicy.ALLOW_READONLY)
    assert check_tool_allowed("read_file", ctx)[0]
    assert not check_tool_allowed("write_file", ctx)[0]


def test_merkle_and_deterministic_tar(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("hello", encoding="utf-8")
    b.write_text("world", encoding="utf-8")
    svc = TrainingPushService("http://127.0.0.1:1/rpc")
    bundle1 = svc.build_bundle([a, b], bundle_type="dataset", metadata={"name": "x"})
    bundle2 = svc.build_bundle([a, b], bundle_type="dataset", metadata={"name": "x"})
    assert bundle1["bundle_root"] == bundle2["bundle_root"]
    assert Path(bundle1["bundle_path"]).read_bytes() == Path(bundle2["bundle_path"]).read_bytes()


def test_tx_send_param_shape(monkeypatch, tmp_path: Path) -> None:
    called = {}

    class FakeRpc:
        def __init__(self, *_args, **_kwargs):
            pass

        def _known_methods(self):
            return set()

        def call(self, method, params=None):
            called[method] = params
            if method == "tx_sendRawTransaction":
                return "0xabc"
            return "0xraw"

        def close(self):
            return None

    monkeypatch.setattr("animica_studio.services.training_push.RpcClient", FakeRpc)
    svc = TrainingPushService("http://fake")
    out = svc.submit_bundle_tx({"uri": "da://x"}, {"bundle_root": "r", "metadata": {}})
    assert out["tx_hash"] == "0xabc"
    assert called["tx_sendRawTransaction"] == ["0x"]


def test_ena_error_stringified() -> None:
    err = EnaError(code="x", message="y", details={"k": 1}, retryable=True)
    text = str(err)
    payload = json.loads(text)
    assert payload["message"] == "y"


def test_network_rpc_training_submission_disabled(monkeypatch) -> None:
    from animica_studio.services.ena_client import EnaClient

    profile = EnaProfile(mode=EnaMode.NETWORK_RPC, rpc_url="http://127.0.0.1:8545/rpc")
    client = EnaClient(profile)

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("RpcClient should not be used for ENA training submission in network RPC mode")

    monkeypatch.setattr("animica_studio.services.ena_client.RpcClient", _unexpected)
    assert client.submit_training_job({"bundle": "x"}) is None
