from __future__ import annotations

import pytest

from animica_studio.services.da_client import DaClient, DaUploadError
from animica_studio.services.rpc_client import RpcError, RpcResponseError


def test_da_configure_falls_back_to_alias_when_primary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    payloads: list[object] = []

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def call(self, method: str, params):
            calls.append(method)
            payloads.append(params)
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
    assert payloads == [{"enabled": True}, {"enabled": True}]


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


def _make_registry(meta_by_method: dict[str, dict], info: dict | None = None):
    class FakeRegistry:
        server_info = info or {}

        def has_method(self, method: str) -> bool:
            return method in meta_by_method

        def get_method_meta(self, method: str) -> dict:
            return meta_by_method.get(method, {})

    return FakeRegistry()


def test_upload_json_uses_discover_schema_and_positional_params(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def registry(self):
            return _make_registry(
                {
                    "da.putBlob": {
                        "param_structure": "positional",
                        "params": [{"name": "namespace"}, {"name": "data"}],
                        "raw": {
                            "name": "da.putBlob",
                            "params": [
                                {"name": "namespace", "required": True, "schema": {"type": "integer"}},
                                {"name": "data", "required": True, "schema": {"type": "string"}},
                            ],
                        },
                    }
                }
            )

        def resolve_method(self, _requested: str, _candidates):
            return "da.putBlob"

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


def test_upload_bytes_retries_with_object_encoding_on_too_many_positional(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def registry(self):
            return _make_registry(
                {
                    "da.putBlob": {
                        "param_structure": "positional",
                        "params": [{"name": "namespace"}, {"name": "data"}],
                        "raw": {
                            "name": "da.putBlob",
                            "params": [
                                {"name": "namespace", "required": True, "schema": {"type": "integer"}},
                                {"name": "data", "required": True, "schema": {"type": "string"}},
                            ],
                        },
                    }
                }
            )

        def resolve_method(self, _requested: str, _candidates):
            return "da.putBlob"

        def call(self, method: str, params):
            calls.append((method, params))
            if len(calls) == 1:
                raise RpcResponseError(RpcError(code=-32602, message="too many positional arguments"))
            return "0xblob"

        def close(self) -> None:
            return None

    monkeypatch.setattr("animica_studio.services.da_client.RpcClient", FakeRpcClient)

    client = DaClient("http://127.0.0.1:8545")
    out = client.upload_bytes(b"abc", namespace=7)
    assert out["blob_id"] == "0xblob"
    assert len(calls) == 2
    assert isinstance(calls[0][1], list)
    assert isinstance(calls[1][1], dict)
    assert calls[1][1] == {"namespace": 7, "data": "0x616263"}


def test_upload_bytes_raises_upload_error_with_diagnostics_on_invalid_params(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def registry(self):
            return _make_registry(
                {
                    "da_putBlob": {
                        "param_structure": "positional",
                        "params": [{"name": "namespace"}, {"name": "data"}],
                        "raw": {
                            "name": "da_putBlob",
                            "params": [
                                {"name": "namespace", "required": True, "schema": {"type": "integer"}},
                                {"name": "data", "required": True, "schema": {"type": "string"}},
                            ],
                        },
                    }
                },
                info={"version": "v-test"},
            )

        def resolve_method(self, _requested: str, _candidates):
            return "da_putBlob"

        def call(self, _method: str, _params):
            raise RpcResponseError(RpcError(code=-32602, message="invalid params"))

        def close(self) -> None:
            return None

    monkeypatch.setattr("animica_studio.services.da_client.RpcClient", FakeRpcClient)

    client = DaClient("http://127.0.0.1:8545")
    with pytest.raises(DaUploadError) as exc:
        client.upload_bytes(b"abc", namespace=0)
    assert exc.value.diagnostics["resolved_method"] == "da_putBlob"
    assert exc.value.diagnostics["param_spec"] == ["namespace", "data"]
    assert exc.value.diagnostics["chosen_encoding"] == "positional"
    assert exc.value.diagnostics["server_version"] == "v-test"
