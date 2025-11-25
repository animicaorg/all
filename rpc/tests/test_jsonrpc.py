from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from rpc.tests import new_test_client, rpc_call


def test_single_call_ok_chain_id():
    client, cfg, _ = new_test_client()
    res = rpc_call(client, "chain.getChainId")
    assert res["jsonrpc"] == "2.0"
    assert res["id"] == 1
    assert res["result"] == cfg.chain_id


def test_method_not_found_error():
    client, _, _ = new_test_client()
    payload = {"jsonrpc": "2.0", "method": "nope.method", "id": 42}
    r = client.post("/rpc", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 42
    assert "error" in data
    # JSON-RPC -32601: Method not found
    assert data["error"]["code"] == -32601


def test_invalid_params_type_rejected():
    """
    Pass a params value of the wrong type (string) to a no-param method.
    Dispatcher should return -32602 Invalid params.
    """
    client, _, _ = new_test_client()
    payload = {"jsonrpc": "2.0", "method": "chain.getHead", "params": "oops", "id": "abc"}
    r = client.post("/rpc", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == "abc"
    assert "error" in data
    # JSON-RPC -32602: Invalid params
    assert data["error"]["code"] == -32602


def test_parse_error_on_malformed_json():
    """
    Send invalid JSON with application/json content-type: expect -32700 Parse error.
    """
    client, _, _ = new_test_client()
    r = client.post(
        "/rpc",
        data=b"{ bad json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["jsonrpc"] == "2.0"
    # On parse error, id must be null
    assert data.get("id", None) is None
    # JSON-RPC -32700: Parse error
    assert data["error"]["code"] == -32700


def test_batch_mixed_valid_invalid_and_garbage():
    """
    Batch with:
      - valid call (chain.getChainId, id="a")
      - unknown method (id="b") -> -32601
      - invalid element (a bare string) -> -32600 with id=null
    Response MUST be an array with three entries (order not guaranteed by spec).
    """
    client, cfg, _ = new_test_client()
    batch = [
        {"jsonrpc": "2.0", "method": "chain.getChainId", "id": "a"},
        {"jsonrpc": "2.0", "method": "no.such.method", "id": "b"},
        "not a request",
    ]
    r = client.post("/rpc", data=json.dumps(batch), headers={"content-type": "application/json"})
    assert r.status_code == 200
    resp = r.json()
    assert isinstance(resp, list), f"Expected list response, got: {type(resp)} {resp}"

    # Build helpers to find items by id (including None)
    def find_by_id(arr: List[Dict[str, Any]], idval: Any) -> Optional[Dict[str, Any]]:
        for item in arr:
            if item.get("id", None) == idval:
                return item
        return None

    ok = find_by_id(resp, "a")
    assert ok and ok["jsonrpc"] == "2.0" and ok["result"] == cfg.chain_id

    m_nf = find_by_id(resp, "b")
    assert m_nf and m_nf["error"]["code"] == -32601  # Method not found

    invalid_items = [it for it in resp if it.get("id", None) is None]
    assert len(invalid_items) == 1
    assert invalid_items[0]["error"]["code"] == -32600  # Invalid Request


def test_batch_empty_array_is_invalid_request():
    """
    Spec: An empty batch MUST return an error (-32600), not an empty array.
    """
    client, _, _ = new_test_client()
    r = client.post("/rpc", data="[]", headers={"content-type": "application/json"})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict), "Empty batch must return a single error object"
    assert data["error"]["code"] == -32600  # Invalid Request
    assert data.get("id", None) is None


def test_id_echoing_and_types_preserved_in_batch():
    """
    Ensure server echoes id with same type for each element in a batch.
    """
    client, cfg, _ = new_test_client()
    batch = [
        {"jsonrpc": "2.0", "method": "chain.getChainId", "id": 7},
        {"jsonrpc": "2.0", "method": "chain.getChainId", "id": "07"},
    ]
    r = client.post("/rpc", data=json.dumps(batch), headers={"content-type": "application/json"})
    assert r.status_code == 200
    resp = r.json()
    assert isinstance(resp, list) and len(resp) == 2

    # Map id -> item
    by_id = {item["id"]: item for item in resp}
    assert 7 in by_id
    assert "07" in by_id
    assert isinstance(by_id[7]["id"], int)
    assert isinstance(by_id["07"]["id"], str)
    assert by_id[7]["result"] == cfg.chain_id
    assert by_id["07"]["result"] == cfg.chain_id


def test_notification_is_ignored_no_result():
    """
    Spec: Notifications (no 'id') must not be responded to.
    Some servers return 200 with empty body; others return 204.
    We accept either, but if body exists, it must be empty.
    """
    client, _, _ = new_test_client()
    payload = {"jsonrpc": "2.0", "method": "chain.getHead"}  # no id => notification
    r = client.post("/rpc", json=payload)
    assert r.status_code in (200, 204)
    # If body exists, it should be empty or whitespace
    body = r.text or ""
    assert body.strip() == "" or body.strip() == "null", f"Unexpected notification response body: {body!r}"
