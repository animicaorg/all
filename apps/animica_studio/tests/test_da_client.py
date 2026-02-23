from __future__ import annotations

import pytest

from animica_studio.services.da_client import DaClient, DaUploadError
from animica_studio.services.rpc_client import RpcError, RpcResponseError


def test_da_configure_falls_back_to_alias_when_primary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def call(self, method: str, params):
            calls.append(method)
            if method == "da.configure":
                raise RpcResponseError(RpcError(code=-32601, message="Method not found"))
            if method == "da_configure":
                return {"ok": True, "enabled": True}
            raise AssertionError(f"unexpected method {method}")

        def close(self) -> None:
            return None

    monkeypatch.setattr("animica_studio.services.da_client.RpcClient", FakeRpcClient)

    client = DaClient("http://127.0.0.1:8545")
    out = client.configure({"enabled": True})

    assert out["ok"] is True
    assert calls == ["da.configure", "da_configure"]


def test_da_configure_does_not_swallow_non_availability_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def call(self, _method: str, _params):
            raise RpcResponseError(RpcError(code=-32602, message="Invalid params"))

        def close(self) -> None:
            return None

    monkeypatch.setattr("animica_studio.services.da_client.RpcClient", FakeRpcClient)

    client = DaClient("http://127.0.0.1:8545")
    with pytest.raises(RpcResponseError) as exc:
        client.configure({"enabled": True, "max_bytes": -1})
    assert exc.value.rpc_error.code == -32602


def test_upload_json_uses_resolved_method_and_positional_namespace_plus_data(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def resolve_method(self, _requested: str, _candidates):
            return "da.putBlob"

        def get_param_spec(self, _method: str):
            return [{"name": "namespace", "required": True}, {"name": "data", "required": True}]

        def call(self, method: str, params):
            called["method"] = method
            called["params"] = params
            return "0xblob"

        def close(self) -> None:
            return None

    monkeypatch.setattr("animica_studio.services.da_client.RpcClient", FakeRpcClient)

    client = DaClient("http://127.0.0.1:8545")
    out = client.upload_json({"hello": "world"}, namespace=0)

    assert out["blob_id"] == "0xblob"
    assert called["method"] == "da.putBlob"
    params = called["params"]
    assert isinstance(params, list)
    assert params[0] == 0
    assert isinstance(params[1], str) and str(params[1]).startswith("0x")


def test_upload_bytes_raises_upload_error_with_diagnostics_on_invalid_params(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRegistry:
        server_info = {"version": "v-test"}

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def resolve_method(self, _requested: str, _candidates):
            return "da_putBlob"

        def get_param_spec(self, _method: str):
            return [{"name": "namespace", "required": True}, {"name": "data", "required": True}]

        def call(self, _method: str, _params):
            raise RpcResponseError(RpcError(code=-32602, message="missing required param namespace"))

        def registry(self):
            return FakeRegistry()

        def close(self) -> None:
            return None

    monkeypatch.setattr("animica_studio.services.da_client.RpcClient", FakeRpcClient)

    client = DaClient("http://127.0.0.1:8545")
    with pytest.raises(DaUploadError) as exc:
        client.upload_bytes(b"abc", namespace=0)
    assert exc.value.diagnostics["resolved_method"] == "da_putBlob"
    assert exc.value.diagnostics["params_len"] == 2
    assert exc.value.diagnostics["server_version"] == "v-test"
