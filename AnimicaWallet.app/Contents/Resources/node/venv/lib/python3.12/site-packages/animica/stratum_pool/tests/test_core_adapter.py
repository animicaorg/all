import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.core import MiningCoreAdapter, MiningJob

from core.types.header import Header, serialize_header
from core.utils.hash import sha3_256
from core.utils.pow import micro_threshold_to_target256
from mining.share_submitter import RpcError


class DummyRpc:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        return self.payload


def _full_header_template() -> dict:
    return {
        "v": 1,
        "chainId": 1,
        "height": 7,
        "parentHash": "0x" + "11" * 32,
        "timestamp": 1_800_000_000,
        "stateRoot": "0x" + "22" * 32,
        "txsRoot": "0x" + "33" * 32,
        "receiptsRoot": "0x" + "44" * 32,
        "proofsRoot": "0x" + "55" * 32,
        "daRoot": "0x" + "66" * 32,
        "mixSeed": "0x" + "77" * 32,
        "poiesPolicyRoot": "0x" + "88" * 32,
        "pqAlgPolicyRoot": "0x" + "99" * 32,
        "thetaMicro": 1_000_000,
        "workType": 0,
        "nonce": 0,
        "extra": "0x",
    }


def _header_obj(header_view: dict) -> Header:
    def _parse(value: str) -> bytes:
        return bytes.fromhex(value[2:])

    return Header(
        v=int(header_view.get("v", 1)),
        chainId=int(header_view.get("chainId", 0)),
        height=int(header_view.get("height") or header_view.get("number") or 0),
        parentHash=_parse(header_view["parentHash"]),
        timestamp=int(header_view["timestamp"]),
        stateRoot=_parse(header_view["stateRoot"]),
        txsRoot=_parse(header_view["txsRoot"]),
        receiptsRoot=_parse(header_view["receiptsRoot"]),
        proofsRoot=_parse(header_view["proofsRoot"]),
        daRoot=_parse(header_view["daRoot"]),
        mixSeed=_parse(header_view["mixSeed"]),
        poiesPolicyRoot=_parse(header_view["poiesPolicyRoot"]),
        pqAlgPolicyRoot=_parse(header_view["pqAlgPolicyRoot"]),
        thetaMicro=int(header_view["thetaMicro"]),
        workType=int(header_view.get("workType", 0)),
        nonce=int(header_view.get("nonce", 0)),
        extra=b"",
    )


def _find_nonce_for_target(header_view: dict, target_int: int, limit: int = 10_000) -> int:
    header = _header_obj(header_view)
    for nonce in range(limit):
        digest = sha3_256(serialize_header(replace(header, nonce=nonce)))
        if int.from_bytes(digest, "big", signed=False) <= target_int:
            return nonce
    raise AssertionError("unable to find nonce within search window")


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

    adapter = MiningCoreAdapter("http://example", 1, "")
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
async def test_get_new_job_retries_block_template_param_variants(monkeypatch):
    payload = {
        "templateId": "tpl-1",
        "header": _full_header_template(),
        "target": "0x" + "ff" * 32,
        "parent": {"height": 6, "hash": "0x" + "aa" * 32},
        "txs": [],
        "height": 7,
    }

    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "miner.getBlockTemplate":
                if params == {"address": "anim1pool", "include_mempool": True}:
                    raise RpcError(-32602, "invalid params")
                if params == {"payout_address": "anim1pool", "include_mempool": True}:
                    return payload
            raise AssertionError(f"unexpected RPC call: {method} {params}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    job = await adapter.get_new_job()

    assert job.job_id == "tpl-1"
    assert job.height == 7
    assert rpc.calls[0][0] == "miner.getBlockTemplate"
    assert rpc.calls[0][1] == {"address": "anim1pool", "include_mempool": True}
    assert rpc.calls[1][1] == {
        "payout_address": "anim1pool",
        "include_mempool": True,
    }


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
            if method == "miner.getBlockTemplate":
                raise RpcError(-32602, "unexpected address field")
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
    assert rpc.calls[0][0] == "miner.getWork"
    assert "address" not in rpc.calls[0][1][0]


@pytest.mark.asyncio
async def test_get_new_job_requires_block_template_for_pool_address(monkeypatch):
    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "miner.getBlockTemplate":
                raise RpcError(-32602, "unexpected address field")
            raise AssertionError(f"unexpected RPC call: {method} {params}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 7, "0xpool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    with pytest.raises(
        RuntimeError,
        match="unable to fetch block template for pool mining",
    ):
        await adapter.get_new_job()

    assert all(call[0] == "miner.getBlockTemplate" for call in rpc.calls)


@pytest.mark.asyncio
async def test_get_new_job_prefers_block_template(monkeypatch):
    payload = {
        "templateId": "template-1",
        "header": _full_header_template(),
        "target": "0x" + "ff" * 32,
        "parent": {"height": 6, "hash": "0x" + "aa" * 32},
        "txs": [],
    }

    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "miner.getBlockTemplate":
                return payload
            raise AssertionError(f"unexpected fallback call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    job = await adapter.get_new_job()

    assert job.job_id == "template-1"
    assert job.raw["templateId"] == "template-1"
    assert job.sign_bytes and job.sign_bytes.startswith("0x")
    assert rpc.calls[0][0] == "miner.getBlockTemplate"


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


@pytest.mark.asyncio
async def test_template_share_accepts_non_block_without_rpc_submit(monkeypatch):
    header = _full_header_template()
    theta_micro = int(header["thetaMicro"])
    share_target_ratio = 1.0
    share_target_int = micro_threshold_to_target256(int(theta_micro * share_target_ratio))
    nonce = _find_nonce_for_target(header, share_target_int)

    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            raise AssertionError(f"unexpected RPC call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    mining_job = MiningJob(
        job_id="template-2",
        header=header,
        theta_micro=theta_micro,
        share_target=share_target_ratio,
        height=7,
        target="0x1",
        hints={"mixSeed": header["mixSeed"]},
        raw={
            "templateId": "template-2",
            "header": header,
            "target": "0x1",
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [],
        },
    )

    accepted, reason, is_block, tx_count = await adapter.validate_and_submit_share(
        mining_job,
        {"hashshare": {"nonce": hex(nonce), "body": {}}},
    )

    assert accepted is True
    assert reason is None
    assert is_block is False
    assert tx_count == 0
    assert rpc.calls == []


@pytest.mark.asyncio
async def test_template_block_share_uses_submit_block(monkeypatch):
    rpc = DummyRpc({"accepted": True, "reason": None})

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    header = _full_header_template()
    mining_job = MiningJob(
        job_id="template-3",
        header=header,
        theta_micro=int(header["thetaMicro"]),
        share_target=1.0,
        height=7,
        target="0x" + "ff" * 32,
        hints={"mixSeed": header["mixSeed"]},
        raw={
            "templateId": "template-3",
            "header": header,
            "target": "0x" + "ff" * 32,
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [{"hash": "0xabc", "raw": "0x0102"}],
        },
    )

    accepted, reason, is_block, tx_count = await adapter.validate_and_submit_share(
        mining_job,
        {"hashshare": {"nonce": "0x01", "body": {}}},
    )

    assert accepted is True
    assert reason is None
    assert is_block is True
    assert tx_count == 1
    assert rpc.calls[0][0] == "miner.submitBlock"
    assert rpc.calls[0][1]["templateId"] == "template-3"
    assert rpc.calls[0][1]["header"]["nonce"] == 1
    assert rpc.calls[0][1]["txs"] == ["0x0102"]
