import json

import httpx
import pytest

from mining.share_submitter import ShareSubmitter, SubmitterConfig


@pytest.mark.asyncio
async def test_submit_share_rejects_missing_job_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("submitShare should not be called without jobId")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        submitter = ShareSubmitter(
            SubmitterConfig(rpc_url="http://test"), http_client=client
        )
        with pytest.raises(ValueError, match="jobId"):
            await submitter.submit(
                {"header": {"height": 1}, "nonce": 1, "proof": {"type": "hashshare"}}
            )


@pytest.mark.asyncio
async def test_submit_share_sends_payload_with_job_id() -> None:
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.update(payload["params"][0])
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"accepted": True},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        submitter = ShareSubmitter(
            SubmitterConfig(rpc_url="http://test"), http_client=client
        )
        res = await submitter.submit(
            {
                "jobId": "job-1",
                "header": {"height": 1},
                "nonce": 1,
                "proof": {"type": "hashshare"},
            }
        )

    assert res["accepted"] is True
    assert seen["jobId"] == "job-1"
