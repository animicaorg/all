from rpc import methods
from rpc.tests import new_test_client, rpc_call


def test_invalid_params_returns_structured_error():
    client, _, _ = new_test_client()
    payload = {
        "jsonrpc": "2.0",
        "method": "chain.getHead",
        "params": "oops",
        "id": 99,
    }

    resp = client.post("/rpc", json=payload)
    data = resp.json()
    assert data["error"]["code"] == -32602
    assert data["error"]["message"] == "Invalid params"


def test_method_not_found_error_message_is_consistent():
    client, _, _ = new_test_client()
    payload = {"jsonrpc": "2.0", "method": "nope.method", "id": 7}

    resp = client.post("/rpc", json=payload)
    data = resp.json()
    assert data["error"]["code"] == -32601
    assert data["error"]["message"] == "Method not found"


def test_internal_error_returns_standard_shape():
    @methods.method("test.raiseInternal", replace=True)
    def raise_internal():  # pragma: no cover - registered for test
        raise RuntimeError("boom")

    # Register with the JSON-RPC dispatcher registry as well
    from rpc import jsonrpc

    jsonrpc.registry.register("test.raiseInternal", raise_internal)

    client, _, _ = new_test_client()
    res = rpc_call(client, "test.raiseInternal", expect_error=True)

    assert res["error"]["code"] == -32603
    assert res["error"]["message"] == "Internal error"
