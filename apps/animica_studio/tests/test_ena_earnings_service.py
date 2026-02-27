from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from animica_studio.services.ena_earnings_service import EnaEarningsService


class _FakeRegistry:
    def resolve_any(self, _candidates):
        return "aicf.getClaimable"


class _FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def registry(self):
        return _FakeRegistry()

    def call_with_schema(self, method, payload):
        self.calls.append(("call_with_schema", method, payload))
        raise RuntimeError("RpcError(-32602): missing required params: params")

    def call(self, method, params):
        self.calls.append(("call", method, params))
        return {"claimable": 7}


def test_get_claimable_falls_back_to_positional_params() -> None:
    svc = EnaEarningsService("http://127.0.0.1:8545/rpc")
    client = _FakeClient()

    out = svc._get_claimable(client, "0xabc")

    assert out == 7.0
    assert client.calls[0] == ("call_with_schema", "aicf.getClaimable", {"address": "0xabc"})
    assert client.calls[1] == ("call", "aicf.getClaimable", ["0xabc"])
