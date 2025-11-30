import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.core import MiningCoreAdapter, MiningJob

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
        "target": "0x1234",
        "signBytes": "0x99",
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
    assert rpc.calls[0][1][0]["chainId"] == 1
    assert job.target == "0x1234"
    assert job.sign_bytes == "0x99"


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
    assert rpc.calls[1][1] == []  # final fallback drops params entirely


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
    # First attempt includes pool address, then retries without it
    assert rpc.calls[0][1][0]["address"] == "0xpool"
    assert rpc.calls[1][1] == []


@pytest.mark.asyncio
async def test_submit_share_uses_submit_work(monkeypatch):
    rpc = DummyRpc({"accepted": True, "reason": None})

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "0xpool")
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    class DummyValidator:
        async def validate(self, job, params):  # noqa: D401
            return True, None, False, 0

    adapter._validator = DummyValidator()  # type: ignore[assignment]

    mining_job = MiningJob(
        job_id="job-1",
        header={"number": 1},
        theta_micro=1,
        share_target=0.1,
        height=1,
        hints={"mixSeed": "0x0"},
    )

    accepted, reason, _is_block, _tx_count = await adapter.validate_and_submit_share(
        mining_job,
        {"hashshare": {"nonce": "0x01", "body": {}, "mixSeed": "0x0"}},
    )

    assert accepted
    assert reason is None
    assert rpc.calls[0][0] == "miner.submitWork"
    assert rpc.calls[0][1]["jobId"] == "job-1"
    assert rpc.calls[0][1]["nonce"] == "0x01"
