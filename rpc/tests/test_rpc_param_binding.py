from __future__ import annotations

import pytest

from rpc.tests import new_test_client

pytestmark = pytest.mark.anyio


ADDRESS = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqpsu8y"


def _post(client, payload):
    resp = client.post("/rpc", json=payload)
    assert resp.status_code == 200
    return resp.json()


def test_rpc_param_binding_positional() -> None:
    client, _cfg, _ = new_test_client()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "state.getNextNonce",
        "params": [ADDRESS],
    }
    data = _post(client, payload)
    assert data["jsonrpc"] == "2.0"
    assert data["result"] == 0


def test_rpc_param_binding_keyword() -> None:
    client, _cfg, _ = new_test_client()
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "state.getNextNonce",
        "params": {"addr": ADDRESS},
    }
    data = _post(client, payload)
    assert data["jsonrpc"] == "2.0"
    assert data["result"] == 0


def test_rpc_param_binding_raw_single() -> None:
    client, _cfg, _ = new_test_client()
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "state.getNextNonce",
        "params": ADDRESS,
    }
    data = _post(client, payload)
    assert data["jsonrpc"] == "2.0"
    assert data["result"] == 0


def test_rpc_param_binding_missing_params() -> None:
    client, _cfg, _ = new_test_client()
    payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "state.getNextNonce",
    }
    data = _post(client, payload)
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 4
    assert data["error"]["code"] == -32602
