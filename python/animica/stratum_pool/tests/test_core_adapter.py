import asyncio
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.core import MiningCoreAdapter
from mining.share_submitter import RpcError


class DummyRpc:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        return self.payload


@pytest.mark.asyncio
async def test_get_new_job_prefers_first_success(monkeypatch):
    payload = {
        "jobId": "abc",
        "header": {"number": 7},
        "thetaMicro": 123,
        "shareTarget": 0.5,
        "height": 7,
    }
    rpc = DummyRpc(payload)

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "0xpool")
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    job = await adapter.get_new_job()

    assert job.job_id == "abc"
    assert job.height == 7
    assert rpc.calls[0][0] == "miner.getWork"


@pytest.mark.asyncio
async def test_get_new_job_retries_without_params(monkeypatch):
    payload = {
        "jobId": "abc",
        "header": {"number": 7},
        "thetaMicro": 123,
        "shareTarget": 0.5,
        "height": 7,
    }

    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if params:
                raise RpcError(-32602, "invalid params")
            return payload

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "0xpool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    job = await adapter.get_new_job()

    assert job.job_id == "abc"
    assert job.height == 7
    assert rpc.calls[0][1]  # first attempt uses params with address
    assert rpc.calls[1][1]  # retries with metadata but without address
    assert rpc.calls[2][1] == []  # final fallback drops params entirely


@pytest.mark.asyncio
async def test_get_new_job_omits_empty_pool_address(monkeypatch):
    payload = {
        "jobId": "abc",
        "header": {"number": 7},
        "thetaMicro": 123,
        "shareTarget": 0.5,
        "height": 7,
    }

    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if isinstance(params, list) and params and "address" in params[0]:
                raise RpcError(-32602, "unexpected address field")
            return payload

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    job = await adapter.get_new_job()

    assert job.job_id == "abc"
    assert job.height == 7
    assert "address" not in rpc.calls[0][1][0]


@pytest.mark.asyncio
async def test_get_new_job_fallbacks_without_pool_address(monkeypatch):
    payload = {
        "jobId": "abc",
        "header": {"number": 7},
        "thetaMicro": 123,
        "shareTarget": 0.5,
        "height": 7,
    }

    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if params and isinstance(params[0], dict) and "address" in params[0]:
                raise RpcError(-32602, "unexpected address field")
            return payload

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 7, "0xpool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    job = await adapter.get_new_job()

    assert job.job_id == "abc"
    assert job.height == 7
    # First attempt includes pool address, then retries without it but keeps chainId
    assert rpc.calls[0][1][0]["address"] == "0xpool"
    assert rpc.calls[1][1][0] == {"chainId": 7}
