from __future__ import annotations

from typing import Any

from .rpc_client import RpcClient


class AicfClient:
    def __init__(self, rpc_url: str) -> None:
        self.rpc = RpcClient(rpc_url)
        self._claimable_positional_fallback = False

    def close(self) -> None:
        self.rpc.close()

    def get_claimable(self, address: str) -> dict[str, Any]:
        method = self.rpc.resolve_method("aicf.getClaimable", ["aicf.getClaimable", "aicf_getClaimable"])
        if self._claimable_positional_fallback:
            return self.rpc.call(method, [address])
        try:
            return self.rpc.call_with_schema(method, {"address": address})
        except Exception as exc:
            if "missing required params" not in str(exc).lower():
                raise
            self._claimable_positional_fallback = True
            return self.rpc.call(method, [address])

    def credits_by_address(self, address: str) -> dict[str, Any]:
        method = self.rpc.resolve_method("aicf.creditsByAddress", ["aicf.creditsByAddress", "aicf_creditsByAddress"])
        return self.rpc.call_with_schema(method, {"address": address})

    def claim(self, address: str, amount: str | None = None) -> dict[str, Any]:
        method = self.rpc.resolve_method("aicf.claim", ["aicf.claim", "aicf_claim"])
        payload: dict[str, Any] = {"address": address}
        if amount:
            payload["amount"] = amount
        return self.rpc.call_with_schema(method, payload)
