import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.core import (MiningCoreAdapter, MiningJob,
                                       TemplateUnavailable, freeze_mining_job)

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


def _find_nonce_between_targets(
    header_view: dict,
    *,
    upper_target: int,
    lower_exclusive: int,
    limit: int = 20_000,
) -> int:
    header = _header_obj(header_view)
    for nonce in range(limit):
        digest = sha3_256(serialize_header(replace(header, nonce=nonce)))
        digest_int = int.from_bytes(digest, "big", signed=False)
        if digest_int <= upper_target and digest_int > lower_exclusive:
            return nonce
    raise AssertionError("unable to find nonce between targets")


def _find_nonce_above_target(
    header_view: dict,
    *,
    target_int: int,
    limit: int = 20_000,
) -> int:
    header = _header_obj(header_view)
    for nonce in range(limit):
        digest = sha3_256(serialize_header(replace(header, nonce=nonce)))
        if int.from_bytes(digest, "big", signed=False) > target_int:
            return nonce
    raise AssertionError("unable to find nonce above target")


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
async def test_get_new_job_extracts_share_target_from_target_hint(monkeypatch):
    payload = {
        "templateId": "template-target-hint",
        "header": _full_header_template(),
        "target": "0x" + "ff" * 32,
        "parent": {"height": 6, "hash": "0x" + "aa" * 32},
        "targetHint": {"shareRatio": 0.025},
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

    assert job.share_target == pytest.approx(0.025)
    assert job.raw["_shareTargetProvided"] is True
    assert float(job.raw["_requestedShareTarget"]) == pytest.approx(0.025)


@pytest.mark.asyncio
async def test_get_new_job_surfaces_min_block_spacing(monkeypatch):
    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "miner.getBlockTemplate":
                return {
                    "enabled": False,
                    "reason": "min_block_spacing",
                    "waitSeconds": 1.25,
                    "head": {"height": 9, "hash": "0x" + "ab" * 32},
                }
            raise AssertionError(f"unexpected call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    with pytest.raises(TemplateUnavailable) as excinfo:
        await adapter.get_new_job()

    assert excinfo.value.reason == "min_block_spacing"
    assert excinfo.value.wait_seconds == pytest.approx(1.25)
    assert excinfo.value.head.get("height") == 9


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
    assert rpc.calls[0][0] == "chain.getHead"
    assert rpc.calls[1][0] == "miner.submitBlock"
    assert rpc.calls[1][1]["templateId"] == "template-3"
    assert rpc.calls[1][1]["header"]["nonce"] == 1
    assert rpc.calls[1][1]["txs"] == ["0x0102"]


@pytest.mark.asyncio
async def test_template_block_share_rejects_stale_head_locally(monkeypatch):
    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "chain.getHead":
                return {"height": 42, "hash": "0x" + "ff" * 32}
            raise AssertionError(f"unexpected RPC call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    header = _full_header_template()
    mining_job = MiningJob(
        job_id="template-stale-head",
        header=header,
        theta_micro=int(header["thetaMicro"]),
        share_target=1.0,
        height=7,
        target="0x" + "ff" * 32,
        hints={"mixSeed": header["mixSeed"]},
        template_id="template-stale-head",
        parent_hash=header["parentHash"],
        raw={
            "templateId": "template-stale-head",
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

    assert accepted is False
    assert is_block is True
    assert tx_count == 1
    assert isinstance(reason, str)
    assert "stale template" in reason
    assert [call[0] for call in rpc.calls] == ["chain.getHead"]


@pytest.mark.asyncio
async def test_template_block_share_rejects_expired_template_locally(monkeypatch):
    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "chain.getHead":
                return {"height": 6, "hash": "0x" + "11" * 32}
            raise AssertionError(f"unexpected RPC call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    header = _full_header_template()
    mining_job = MiningJob(
        job_id="template-expired",
        header=header,
        theta_micro=int(header["thetaMicro"]),
        share_target=1.0,
        height=7,
        target="0x" + "ff" * 32,
        hints={"mixSeed": header["mixSeed"]},
        template_id="template-expired",
        parent_hash=header["parentHash"],
        expires_at=0.0,
        raw={
            "templateId": "template-expired",
            "header": header,
            "target": "0x" + "ff" * 32,
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [{"hash": "0xabc", "raw": "0x0102"}],
            "expiresAt": 1,
        },
    )

    accepted, reason, is_block, tx_count = await adapter.validate_and_submit_share(
        mining_job,
        {"hashshare": {"nonce": "0x01", "body": {}}},
    )

    assert accepted is False
    assert is_block is True
    assert tx_count == 1
    assert isinstance(reason, str)
    assert "template_expired" in reason
    assert [call[0] for call in rpc.calls] == ["chain.getHead"]


@pytest.mark.asyncio
async def test_template_submit_payload_header_matches_direct_mine_blocks_path(monkeypatch):
    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "chain.getHead":
                return {"height": 6, "hash": "0x" + "11" * 32}
            if method == "miner.submitBlock":
                return {"accepted": True, "reason": None}
            raise AssertionError(f"unexpected RPC call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    header = _full_header_template()
    nonce = 7
    target_hex = "0x" + "ff" * 32
    mining_job = MiningJob(
        job_id="template-header-match",
        header=header,
        theta_micro=int(header["thetaMicro"]),
        share_target=1.0,
        height=7,
        target=target_hex,
        hints={"mixSeed": header["mixSeed"]},
        template_id="template-header-match",
        parent_hash=header["parentHash"],
        raw={
            "templateId": "template-header-match",
            "header": header,
            "target": target_hex,
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [{"hash": "0xabc", "raw": "0x0102"}],
        },
    )

    accepted, reason, is_block, tx_count = await adapter.validate_and_submit_share(
        mining_job,
        {"hashshare": {"nonce": hex(nonce), "body": {}}},
    )

    assert accepted is True
    assert reason is None
    assert is_block is True
    assert tx_count == 1

    submit_payload = rpc.calls[1][1]
    submitted_header = _header_obj(submit_payload["header"])
    direct_header = replace(_header_obj(header), nonce=nonce)
    assert serialize_header(submitted_header) == serialize_header(direct_header)


@pytest.mark.asyncio
async def test_template_share_target_and_block_target_boundaries(monkeypatch):
    header = _full_header_template()
    theta_micro = int(header["thetaMicro"])
    share_ratio = 0.6
    share_target_int = micro_threshold_to_target256(int(theta_micro * share_ratio))
    block_target_int = share_target_int // 2

    nonce_share_only = _find_nonce_between_targets(
        header,
        upper_target=share_target_int,
        lower_exclusive=block_target_int,
    )
    nonce_block = _find_nonce_for_target(header, block_target_int)

    class RejectingRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "chain.getHead":
                return {"height": 6, "hash": header["parentHash"]}
            if method == "miner.submitBlock":
                return {"accepted": True, "reason": None}
            raise AssertionError(f"unexpected RPC call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = RejectingRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    mining_job = MiningJob(
        job_id="template-target-bounds",
        header=header,
        theta_micro=theta_micro,
        share_target=share_ratio,
        height=7,
        target=hex(block_target_int),
        hints={"mixSeed": header["mixSeed"]},
        template_id="template-target-bounds",
        parent_hash=header["parentHash"],
        raw={
            "templateId": "template-target-bounds",
            "header": header,
            "target": hex(block_target_int),
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [],
        },
    )

    accepted_share, reason_share, is_block_share, tx_count_share = (
        await adapter.validate_and_submit_share(
            mining_job,
            {"hashshare": {"nonce": hex(nonce_share_only), "body": {}}},
        )
    )
    assert accepted_share is True
    assert reason_share is None
    assert is_block_share is False
    assert tx_count_share == 0
    assert rpc.calls == []

    accepted_block, reason_block, is_block_block, tx_count_block = (
        await adapter.validate_and_submit_share(
            mining_job,
            {"hashshare": {"nonce": hex(nonce_block), "body": {}}},
        )
    )
    assert accepted_block is True
    assert reason_block is None
    assert is_block_block is True
    assert tx_count_block == 0
    assert [call[0] for call in rpc.calls] == ["chain.getHead", "miner.submitBlock"]

    # Deterministically pick a nonce that misses the share target.
    nonce_low_diff = nonce_share_only + 1
    low_diff_ok, low_diff_reason, low_diff_block, low_diff_tx_count = (
        await adapter.validate_and_submit_share(
            mining_job,
            {"hashshare": {"nonce": hex(nonce_low_diff), "body": {}}},
        )
    )
    if low_diff_ok:
        # If the immediate successor happened to pass, find one that fails.
        probe = nonce_low_diff + 1
        while True:
            low_diff_ok, low_diff_reason, low_diff_block, low_diff_tx_count = (
                await adapter.validate_and_submit_share(
                    mining_job,
                    {"hashshare": {"nonce": hex(probe), "body": {}}},
                )
            )
            if not low_diff_ok:
                break
            probe += 1

    assert low_diff_ok is False
    assert low_diff_reason == "low difficulty share"
    assert low_diff_block is False
    assert low_diff_tx_count == 0


@pytest.mark.asyncio
async def test_template_share_validation_is_deterministic_for_same_nonce(monkeypatch):
    header = _full_header_template()
    theta_micro = int(header["thetaMicro"])
    share_ratio = 0.01
    share_target_int = micro_threshold_to_target256(int(theta_micro * share_ratio))
    nonce_ok = _find_nonce_for_target(header, share_target_int, limit=200_000)
    nonce_low = _find_nonce_above_target(header, target_int=share_target_int)

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
        job_id="template-deterministic",
        source_job_id="template-deterministic",
        header=header,
        theta_micro=theta_micro,
        share_target=share_ratio,
        height=7,
        target="0x1",
        hints={"mixSeed": header["mixSeed"]},
        template_id="template-deterministic",
        parent_hash=header["parentHash"],
        raw={
            "templateId": "template-deterministic",
            "header": header,
            "target": "0x1",
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [],
        },
    )

    accepted_runs = []
    for _ in range(3):
        accepted_runs.append(
            await adapter.validate_and_submit_share(
                mining_job,
                {"hashshare": {"nonce": hex(nonce_ok), "body": {}}},
            )
        )
    assert all(result[0] is True and result[1] is None for result in accepted_runs)

    low_runs = []
    for _ in range(3):
        low_runs.append(
            await adapter.validate_and_submit_share(
                mining_job,
                {"hashshare": {"nonce": hex(nonce_low), "body": {}}},
            )
        )
    assert all(
        result[0] is False and result[1] == "low difficulty share"
        for result in low_runs
    )
    # Non-block validation should not consult live head state.
    assert rpc.calls == []


@pytest.mark.asyncio
async def test_low_diff_reject_logging_includes_frozen_validation_fields(
    monkeypatch, caplog
):
    header = _full_header_template()
    theta_micro = int(header["thetaMicro"])
    share_ratio = 0.01
    share_target_int = micro_threshold_to_target256(int(theta_micro * share_ratio))
    nonce_low = _find_nonce_above_target(header, target_int=share_target_int)

    class DummyRpc:
        def call(self, method, params):
            raise AssertionError(f"unexpected RPC call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    monkeypatch.setattr(adapter, "_rpc", DummyRpc())
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    mining_job = MiningJob(
        job_id="template-logging",
        source_job_id="template-logging",
        header=header,
        theta_micro=theta_micro,
        share_target=share_ratio,
        height=7,
        target="0x1",
        hints={"mixSeed": header["mixSeed"]},
        template_id="template-logging",
        parent_hash=header["parentHash"],
        raw={
            "templateId": "template-logging",
            "header": header,
            "target": "0x1",
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [],
        },
    )

    caplog.set_level(logging.INFO, logger="animica.stratum_pool.core")
    ok, reason, _is_block, _tx_count = await adapter.validate_and_submit_share(
        mining_job,
        {
            "_worker": "animica-cpu",
            "_session_id": "sess-1",
            "_address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            "hashshare": {"nonce": hex(nonce_low), "body": {}},
        },
    )

    assert ok is False
    assert reason == "low difficulty share"

    reject_record = next(r for r in caplog.records if r.msg == "stratum_share_rejected")
    assert reject_record.nonce == nonce_low
    assert reject_record.share_hash_int.startswith("0x")
    assert reject_record.frozen_theta_micro == theta_micro
    assert reject_record.share_threshold_micro == int(theta_micro * share_ratio)
    assert reject_record.reason == "low difficulty share"


def test_freeze_job_integer_target_derivation_for_diff_point_zero_zero_one():
    header = _full_header_template()
    header["thetaMicro"] = 12_076_750
    job = MiningJob(
        job_id="job-001",
        source_job_id="job-001",
        header=header,
        theta_micro=12_076_750,
        share_target=0.01,
        height=141,
        target="0x" + "ff" * 32,
        raw={
            "templateId": "job-001",
            "header": header,
            "target": "0x" + "ff" * 32,
            "parent": {"height": 140, "hash": header["parentHash"]},
        },
    )

    frozen = freeze_mining_job(job, fallback_chain_id=1)
    assert frozen.share_threshold_micro == 120_767
    assert frozen.share_target_int == micro_threshold_to_target256(120_767)


@pytest.mark.asyncio
async def test_template_share_sequence_matches_frozen_target_compare(monkeypatch):
    header = _full_header_template()
    theta_micro = int(header["thetaMicro"])
    share_ratio = 0.6
    share_target_int = micro_threshold_to_target256(int(theta_micro * share_ratio))

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
        job_id="template-sequence",
        source_job_id="template-sequence",
        header=header,
        theta_micro=theta_micro,
        share_target=share_ratio,
        height=7,
        target="0x1",
        hints={"mixSeed": header["mixSeed"]},
        template_id="template-sequence",
        parent_hash=header["parentHash"],
        raw={
            "templateId": "template-sequence",
            "header": header,
            "target": "0x1",
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [],
        },
    )

    header_obj = _header_obj(header)
    nonces = list(range(0, 64))
    expected = {}
    for nonce in nonces:
        digest = sha3_256(serialize_header(replace(header_obj, nonce=nonce)))
        expected[nonce] = int.from_bytes(digest, "big", signed=False) <= share_target_int

    assert any(expected.values())
    assert any(not ok for ok in expected.values())

    observed = []
    for _round in (1, 2):
        round_results = {}
        for nonce in nonces:
            ok, reason, is_block, _tx_count = await adapter.validate_and_submit_share(
                mining_job,
                {"hashshare": {"nonce": hex(nonce), "body": {}}},
            )
            round_results[nonce] = (ok, reason, is_block)
        observed.append(round_results)

    for nonce in nonces:
        expected_ok = expected[nonce]
        first = observed[0][nonce]
        second = observed[1][nonce]
        assert first == second
        assert first[0] is expected_ok
        if expected_ok:
            assert first[1] is None
            assert first[2] is False
        else:
            assert first[1] == "low difficulty share"
            assert first[2] is False

    assert rpc.calls == []


@pytest.mark.asyncio
async def test_template_block_share_rpc_stale_template_regression(monkeypatch):
    class DummyRpc:
        def __init__(self):
            self.calls = []

        def call(self, method, params):
            self.calls.append((method, params))
            if method == "chain.getHead":
                return {"height": 6, "hash": "0x" + "11" * 32}
            if method == "miner.submitBlock":
                raise RpcError(
                    -32063,
                    "stale template",
                    {"reason": "stale_template", "detail": "template_expired"},
                )
            raise AssertionError(f"unexpected RPC call: {method}")

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    adapter = MiningCoreAdapter("http://example", 1, "anim1pool")
    rpc = DummyRpc()
    monkeypatch.setattr(adapter, "_rpc", rpc)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    header = _full_header_template()
    mining_job = MiningJob(
        job_id="template-rpc-stale",
        header=header,
        theta_micro=int(header["thetaMicro"]),
        share_target=1.0,
        height=7,
        target="0x" + "ff" * 32,
        hints={"mixSeed": header["mixSeed"]},
        template_id="template-rpc-stale",
        parent_hash=header["parentHash"],
        raw={
            "templateId": "template-rpc-stale",
            "header": header,
            "target": "0x" + "ff" * 32,
            "parent": {"height": 6, "hash": header["parentHash"]},
            "txs": [],
        },
    )

    accepted, reason, is_block, tx_count = await adapter.validate_and_submit_share(
        mining_job,
        {"hashshare": {"nonce": "0x01", "body": {}}},
    )

    assert accepted is False
    assert is_block is True
    assert tx_count == 0
    assert isinstance(reason, str)
    assert "rpc:-32063" in reason
    assert "stale template" in reason.lower()
