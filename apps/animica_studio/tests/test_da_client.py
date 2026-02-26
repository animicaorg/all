from __future__ import annotations

import pytest

from animica_studio.services.da_client import DaClient
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


def test_da_put_blob_builder_returns_exactly_two_positional_args() -> None:
    params = DaClient._build_da_put_blob_params(7, "0x616263")
    assert isinstance(params, list)
    assert params == [7, "0x616263"]
    assert len(params) == 2


def test_da_dot_put_blob_builder_returns_object_with_bytes() -> None:
    params = DaClient._build_da_dot_put_blob_params(b"abc", "studio/checkpoint", {"content_type": "text/plain"})
    assert isinstance(params, dict)
    assert "bytes" in params
    assert params["bytes"] == "YWJj"
    assert params["namespace"] == "studio/checkpoint"


def test_upload_blob_uses_da_put_blob_positional_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple[str, object]] = []

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def registry(self):
            return _make_registry({"da.putBlob": {}, "da_putBlob": {}, "da.status": {}})

        def call(self, method: str, params):
            called.append((method, params))
            if method == "da.status":
                return {"enabled": True, "allow_remote_put": False}
            return "0xblob"

        def close(self) -> None:
            return None

    monkeypatch.setattr("animica_studio.services.da_client.RpcClient", FakeRpcClient)

    client = DaClient("http://127.0.0.1:8545")
    out = client.upload_blob(namespace=0, raw_bytes=b"abc", content_type=None, tags=None)

    assert out["blob_id"] == "0xblob"
    assert called[1][0] == "da_putBlob"
    params = called[1][1]
    assert isinstance(params, list)
    assert params[0] == 0
    assert params[1] == "0x616263"


def test_upload_blob_uses_ingest_local_when_remote_put_disallowed(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    calls: list[tuple[str, object]] = []

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def registry(self):
            return _make_registry({"da.putBlob": {}, "da.getStatus": {}, "da.ingestLocal": {}, "da.getIngestDir": {}})

        def call(self, method: str, params):
            calls.append((method, params))
            if method == "da.getStatus":
                return {"enabled": True, "allow_remote_put": False}
            if method == "da.getIngestDir":
                return {"dir": str(tmp_path)}
            return "0xblob"

        def close(self) -> None:
            return None

    monkeypatch.setattr("animica_studio.services.da_client.RpcClient", FakeRpcClient)

    client = DaClient("http://127.0.0.1:8545")
    out = client.upload_blob(namespace=7, raw_bytes=b"abc", content_type=None, tags=None)
    assert out["blob_id"] == "0xblob"
    assert [c[0] for c in calls] == ["da.getStatus", "da.getIngestDir", "da.ingestLocal"]
    assert isinstance(calls[-1][1], dict)
    assert calls[-1][1]["namespace"] == 7
    assert str(calls[-1][1]["path"]).startswith(str(tmp_path))
