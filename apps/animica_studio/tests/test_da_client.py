from __future__ import annotations

import pytest

from animica_studio.services.da_client import DaClient
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
