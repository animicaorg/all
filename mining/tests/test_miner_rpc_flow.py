import asyncio
import json
from typing import Any, Dict

import httpx
import pytest

from mining.orchestrator import MinerOrchestrator, OrchestratorConfig
from mining.rpc_adapter import RpcTemplateProvider
from mining.share_submitter import ShareSubmitter, SubmitterConfig


@pytest.mark.asyncio
async def test_rpc_template_provider_retries_on_timeout() -> None:
    calls = {"get": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "miner.getWork":
            calls["get"] += 1
            if calls["get"] == 1:
                raise httpx.ReadTimeout("timed out", request=request)
            result = {
                "jobId": "job-1",
                "header": {"number": 1},
                "shareTarget": 0.1,
                "signBytes": "0x00",
            }
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": result},
            )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = RpcTemplateProvider(
            rpc_url="http://test",
            proof_type="aicf",
            work_timeout_s=0.1,
            connect_timeout_s=0.1,
            read_timeout_s=0.1,
            write_timeout_s=0.1,
            pool_timeout_s=0.1,
            max_retries=2,
            initial_backoff_s=0.01,
            max_backoff_s=0.02,
            http_client=client,
        )
        tpl = await provider.current_template()

    assert calls["get"] == 2
    assert tpl is not None
    assert tpl["jobId"] == "job-1"


@pytest.mark.asyncio
async def test_orchestrator_submits_share_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submit_calls = {"count": 0}
    template = {
        "jobId": "job-2",
        "header": {"number": 2},
        "shareTarget": 0.1,
        "signBytes": "0x01",
        "hints": {"mixSeed": "0x" + "11" * 32},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "miner.getWork":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": template},
            )
        if method == "miner.submitShare":
            submit_calls["count"] += 1
            if submit_calls["count"] == 1:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"accepted": True, "reason": None},
                },
            )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {}},
        )

    class FakeScanner:
        def __init__(self, device: str = "cpu", threads: int = 0) -> None:
            self.device = device
            self.threads = threads

        async def run(
            self,
            template_iter: Any,
            out_queue: "asyncio.Queue[Dict[str, Any]]",
            stop_evt: asyncio.Event,
        ) -> None:
            tpl = await template_iter.__anext__()
            share = {
                "jobId": tpl["jobId"],
                "header": tpl["header"],
                "nonce": 1,
                "mixSeed": tpl["hints"]["mixSeed"],
                "shareTarget": tpl["shareTarget"],
                "d_ratio": 1.0,
                "proof": {
                    "type": "hashshare",
                    "body": {
                        "headerHash": "0x" + "00" * 32,
                        "nonce": 1,
                        "u": "0x" + "00" * 32,
                        "mixSeed": "0x" + "00" * 32,
                        "targetMu": 1,
                        "algo": "sha3-256",
                    },
                },
            }
            await out_queue.put(share)
            while not stop_evt.is_set():
                await asyncio.sleep(0.01)

    monkeypatch.setattr(
        "mining.orchestrator._try_import_hash_scanner", lambda: (FakeScanner, None)
    )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = RpcTemplateProvider(
            rpc_url="http://test",
            proof_type="aicf",
            work_timeout_s=0.1,
            connect_timeout_s=0.1,
            read_timeout_s=0.1,
            write_timeout_s=0.1,
            pool_timeout_s=0.1,
            max_retries=2,
            initial_backoff_s=0.01,
            max_backoff_s=0.02,
            http_client=client,
        )
        submitter = ShareSubmitter(
            SubmitterConfig(
                rpc_url="http://test",
                submit_timeout_s=0.1,
                connect_timeout_s=0.1,
                read_timeout_s=0.1,
                write_timeout_s=0.1,
                pool_timeout_s=0.1,
                max_retries=2,
                initial_backoff_s=0.01,
                max_backoff_s=0.02,
            ),
            http_client=client,
        )
        cfg = OrchestratorConfig(template_interval_sec=0.05)
        orch = MinerOrchestrator(
            template_provider=provider, submitter=submitter, config=cfg
        )
        await orch.start()
        try:
            await asyncio.wait_for(_wait_for_submit(submitter), timeout=2.0)
        finally:
            await orch.stop()

    assert submit_calls["count"] >= 2
    assert submitter.stats().shares_accepted >= 1


async def _wait_for_submit(submitter: ShareSubmitter) -> None:
    while submitter.stats().shares_accepted < 1:
        await asyncio.sleep(0.05)
