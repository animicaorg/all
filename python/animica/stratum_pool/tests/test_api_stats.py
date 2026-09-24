from __future__ import annotations

import time

import pytest

from animica.stratum_pool import api
from mining import share_submitter


class FakeRpcClient:
    calls: list[tuple[str, object]] = []

    def __init__(self, _url: str) -> None:
        pass

    async def call(self, method: str, params: object, timeout_s: float):
        self.calls.append((method, params))
        if method == "aicf.workerCount":
            return {
                "total_registered": 229,
                "online": 3,
                "phones_online": 1,
                "engines_online": {"node": 2, "webllm": 1},
                "regions_online": {"eu": 2, "unknown": 1},
                "jobs_completed_total": 31400,
            }
        wallet = str((params or {}).get("address"))
        return {
            "registered": True,
            "last_seen": time.time(),
            "tiers": ["standard"],
            "address": wallet,
        }

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_inference_stats_use_full_fleet_and_keep_audited_wallets(monkeypatch):
    FakeRpcClient.calls = []
    monkeypatch.setattr(share_submitter, "AsyncJsonRpcClient", FakeRpcClient)
    monkeypatch.setattr(api, "_INFERENCE_WALLETS", ["anim1one", "anim1two"])

    result = await api._count_serving_inference_workers("http://rpc.invalid")

    assert result["serving"] == 3
    assert result["registered_total"] == 229
    assert result["engines_online"] == {"node": 2, "webllm": 1}
    assert result["phones_online"] == 1
    assert result["audited_serving"] == 2
    assert result["serving_wallets"] == ["anim1one", "anim1two"]
    assert result["source"] == "aicf.workerCount"
    assert FakeRpcClient.calls[0] == (
        "aicf.workerCount",
        {"online_window_s": float(api._SERVING_FRESH_S)},
    )


class BrokenRpcClient(FakeRpcClient):
    async def call(self, method: str, params: object, timeout_s: float):
        if method == "aicf.workerCount":
            raise RuntimeError("older node")
        return await super().call(method, params, timeout_s)


@pytest.mark.asyncio
async def test_inference_stats_fall_back_to_audited_wallets(monkeypatch):
    monkeypatch.setattr(share_submitter, "AsyncJsonRpcClient", BrokenRpcClient)
    monkeypatch.setattr(api, "_INFERENCE_WALLETS", ["anim1one"])

    result = await api._count_serving_inference_workers("http://rpc.invalid")

    assert result["serving"] == 1
    assert result["source"] == "configured_wallet_status"

