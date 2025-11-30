import hashlib

import pytest

from rpc.methods import miner as miner_methods
from rpc.tests import new_test_client, rpc_call


def _find_nonce(sign_bytes_hex: str, target_hex: str) -> str:
    sign_bytes = bytes.fromhex(sign_bytes_hex[2:] if sign_bytes_hex.startswith("0x") else sign_bytes_hex)
    target = int(target_hex, 16)
    for i in range(10000):
        candidate = i.to_bytes(8, "big")
        digest = hashlib.sha3_256(sign_bytes + candidate).digest()
        if int.from_bytes(digest, "big") <= target:
            return "0x" + candidate.hex()
    pytest.skip("could not find a satisfying nonce within search space")


def test_get_work_returns_template():
    client, cfg, _ = new_test_client()
    res = rpc_call(client, "miner.getWork")
    job = res["result"]
    assert job["height"] >= 1
    assert "header" in job and isinstance(job["header"], dict)
    assert job["header"].get("number") == job["height"]
    assert "thetaMicro" in job
    assert "shareTarget" in job
    assert job["algo"] == "asic_sha256"
    assert "jobId" in job and job["jobId"] in miner_methods._JOB_CACHE


def test_get_work_accepts_explicit_empty_params():
    client, _, _ = new_test_client()

    res = rpc_call(client, "miner.getWork", [])
    job = res["result"]

    assert job["jobId"] in miner_methods._JOB_CACHE


def test_jsonrpc_endpoint_accepts_empty_params_via_post_body():
    """Mimic the curl call with params: [] hitting the /rpc endpoint directly."""

    client, _, _ = new_test_client()

    payload = {"jsonrpc": "2.0", "id": 3, "method": "miner.getWork", "params": []}
    res = client.post("/rpc", json=payload)

    assert res.status_code == 200
    data = res.json()
    assert data.get("error") is None
    assert data.get("result") is not None
    assert data["result"].get("jobId") in miner_methods._JOB_CACHE


def test_submit_work_accepts_valid_solution_and_updates_head():
    client, _, _ = new_test_client()
    job = rpc_call(client, "miner.getWork")["result"]

    nonce_hex = _find_nonce(job["signBytes"], job["target"])
    res = rpc_call(client, "miner.submitWork", {"jobId": job["jobId"], "nonce": nonce_hex})

    result = res["result"]
    assert result["accepted"] is True
    assert result["reason"] is None
    assert result["height"] == job["height"]
    assert miner_methods._LOCAL_HEAD.get("height") == job["height"]
    assert miner_methods._LOCAL_HEAD.get("hash") == result["hash"]


def test_submit_work_accepts_positional_params():
    client, _, _ = new_test_client()
    job = rpc_call(client, "miner.getWork", ["asic_sha256"])["result"]

    nonce_hex = _find_nonce(job["signBytes"], job["target"])
    res = rpc_call(client, "miner.submitWork", [job["jobId"], nonce_hex])

    result = res["result"]
    assert result["accepted"] is True


def test_submit_work_rejects_invalid_or_stale_jobs():
    client, _, _ = new_test_client()
    job = rpc_call(client, "miner.getWork")["result"]

    # Missing nonce → invalid params
    bad = rpc_call(client, "miner.submitWork", {"jobId": job["jobId"]}, expect_error=True)
    assert bad["error"]["code"] == -32602

    # Mark head as advanced past the template height to force stale rejection
    miner_methods._LOCAL_HEAD.update({"height": job["height"], "hash": "0x01", "header": None})
    stale = rpc_call(client, "miner.submitWork", {"jobId": job["jobId"], "nonce": "0x00"}, expect_error=True)
    assert stale["error"]["code"] == -32602


def test_get_work_rejects_wrong_param_type():
    client, _, _ = new_test_client()

    res = rpc_call(client, "miner.getWork", "bad-type", expect_error=True)

    assert res["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_dispatch_accepts_empty_param_array():
    from rpc import jsonrpc

    payload = {"jsonrpc": "2.0", "id": 9, "method": "miner.getWork", "params": []}
    ctx = jsonrpc._default_ctx()

    res = await jsonrpc.dispatch(payload, ctx)

    assert res["result"]["jobId"] in miner_methods._JOB_CACHE


def test_get_sha256_job_shape():
    client, _, _ = new_test_client()
    miner_methods._LOCAL_HEAD.clear()
    res = rpc_call(client, "miner.get_sha256_job")
    job = res["result"]
    assert "prevhash" in job and len(job["prevhash"]) == 64
    assert "coinb1" in job and "coinb2" in job
    assert job["version"].startswith("2")
    assert job["nbits"]
    assert job["ntime"]
    assert job["clean_jobs"] is True


def test_submit_sha256_block_stub_accepts_payload():
    client, _, _ = new_test_client()
    payload = {"header": "deadbeef", "nonce": "01"}
    res = rpc_call(client, "miner.submit_sha256_block", payload)
    assert res["result"]["accepted"] is True
    assert res["result"]["payload"] == payload


def test_miner_mine_advances_head():
    client, cfg, _ = new_test_client()
    start = rpc_call(client, "chain.getHead")["result"].get("height") or 0

    mined = rpc_call(client, "miner.mine", [2])["result"]
    assert mined["mined"] == 2
    assert mined["height"] >= start + 2

    after = rpc_call(client, "chain.getHead")["result"].get("height") or 0
    assert after >= start + 2
