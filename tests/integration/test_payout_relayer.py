from __future__ import annotations

import pytest

from relayer.payout_relayer import PayoutRelayer


class MockRpcClient:
    def __init__(self):
        self.calls = []
        self.logs = []
        self.head = {"result": {"height": 10}}

    def post(self, method, params):
        if method == "chain.getHead":
            return self.head
        if method == "rpc_getLogs":
            return {"result": self.logs}
        if method == "rpc_call_contract":
            self.calls.append((params["address"], params["action"], params["params"]))
            return {"result": True}
        return {}


def test_relayer_handles_payout_requested(monkeypatch):
    mock = MockRpcClient()
    # inject a PayoutRequested event
    mock.logs.append(
        {"event": "PayoutRequested", "data": [b"job1", b"workerA", 1000, b"tokenAddr"]}
    )

    # Monkeypatch RpcClient inside relayer
    monkeypatch.setattr("relayer.payout_relayer.RpcClient", lambda rpc_url: mock)

    r = PayoutRelayer("http://fake", "tokenAddr")
    r.last_block = 5
    r.poll_once()

    # after poll, ensure call was made
    assert len(mock.calls) == 1
    addr, action, params = mock.calls[0]
    assert addr == "tokenAddr"
    assert action == "role_mint"
    assert params["to"] == b"workerA"
    assert params["amount"] == 1000
