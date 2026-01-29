import json

import httpx
import pytest

import asyncio

from mining.orchestrator import SubmitPipe, WorkSource
from mining.rpc_adapter import RpcTemplateProvider
from mining.share_submitter import ShareSubmitter, SubmitterConfig


@pytest.mark.asyncio
async def test_aicf_enters_solo_mode_when_getwork_disabled() -> None:
    calls = []

    header = {
        "v": 1,
        "chainId": 1,
        "height": 10,
        "parentHash": "0x" + "11" * 32,
        "timestamp": 1_700_000_000,
        "stateRoot": "0x" + "22" * 32,
        "txsRoot": "0x" + "00" * 32,
        "receiptsRoot": "0x" + "00" * 32,
        "proofsRoot": "0x" + "00" * 32,
        "daRoot": "0x" + "00" * 32,
        "mixSeed": "0x" + "33" * 32,
        "poiesPolicyRoot": "0x" + "44" * 32,
        "pqAlgPolicyRoot": "0x" + "55" * 32,
        "thetaMicro": 1_000_000,
        "workType": 0,
        "nonce": 0,
        "extra": "0x",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload["method"])
        if payload["method"] == "miner.getWork":
            result = {
                "disabled": True,
                "miningEnabled": False,
                "reason": "sync_phase:headers",
            }
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}
            )
        if payload["method"] == "miner.getBlockTemplate":
            result = {
                "enabled": True,
                "templateId": "tpl-1",
                "header": header,
                "target": "0x" + "ff" * 4,
                "txs": [],
            }
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = RpcTemplateProvider(
            rpc_url="http://test",
            proof_type="aicf",
            solo_address="anim1test",
            work_timeout_s=0.1,
            connect_timeout_s=0.1,
            read_timeout_s=0.1,
            write_timeout_s=0.1,
            pool_timeout_s=0.1,
            max_retries=1,
            http_client=client,
        )
        tpl = await provider.current_template()

    assert tpl is not None
    assert tpl["workSource"] == WorkSource.SOLO_TEMPLATE.value
    assert tpl["templateId"] == "tpl-1"
    assert tpl.get("signBytes")
    assert calls == ["miner.getWork", "miner.getBlockTemplate"]


@pytest.mark.asyncio
async def test_solo_share_submits_block_once() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload["method"])
        assert payload["method"] == "miner.submitBlock"
        result = {"accepted": True}
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        submitter = ShareSubmitter(
            SubmitterConfig(rpc_url="http://test"), http_client=client
        )
        share = {
            "workSource": WorkSource.SOLO_TEMPLATE.value,
            "templateId": "tpl-1",
            "header": {"height": 1, "parentHash": "0x" + "11" * 32, "nonce": 0},
            "nonce": 42,
            "txs": [],
        }
        res = await submitter.submit_block(
            {
                "header": dict(share["header"], nonce=42),
                "txs": [],
                "proofs": [],
                "parentHash": share["header"]["parentHash"],
                "templateId": "tpl-1",
            }
        )

    assert res["accepted"] is True
    assert submitter.stats().blocks_accepted == 1
    assert calls == ["miner.submitBlock"]


@pytest.mark.asyncio
async def test_submit_pipe_uses_block_submitter_for_solo_mode() -> None:
    submit_calls = []

    class StubSubmitter:
        async def submit(self, params):
            submit_calls.append(("share", params))
            return {"accepted": True}

        async def submit_block(self, params):
            submit_calls.append(("block", params))
            return {"accepted": True}

    submitter = StubSubmitter()
    pipe = SubmitPipe(
        submitter,
        max_concurrency=1,
        backoff_initial=0.01,
        backoff_max=0.02,
    )
    queue = asyncio.Queue()
    stop_evt = asyncio.Event()
    task = asyncio.create_task(pipe.run(queue, stop_evt))

    await queue.put(
        {
            "workSource": WorkSource.SOLO_TEMPLATE.value,
            "templateId": "tpl-1",
            "header": {"height": 1, "parentHash": "0x" + "11" * 32, "nonce": 0},
            "nonce": 7,
            "txs": [],
        }
    )
    await asyncio.sleep(0.01)
    stop_evt.set()
    await task

    assert submit_calls
    assert submit_calls[0][0] == "block"
    assert all(call[0] != "share" for call in submit_calls)
