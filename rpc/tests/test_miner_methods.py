import hashlib

import pytest

from core.types.header import Header
from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
from rpc.methods import miner as miner_methods
from rpc.tests import new_test_client, rpc_call


def _find_nonce(sign_bytes_hex: str, target_hex: str) -> str:
    sign_bytes = bytes.fromhex(
        sign_bytes_hex[2:] if sign_bytes_hex.startswith("0x") else sign_bytes_hex
    )
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


def test_get_block_template_accepts_address_param(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(client, "miner.getBlockTemplate", {"address": payout_address})

    assert res["result"]["coinbase"]["address"] == payout_address


def test_get_block_template_accepts_payout_address_alias(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(client, "miner.getBlockTemplate", {"payout_address": payout_address})

    assert res["result"]["coinbase"]["address"] == payout_address


def test_get_block_template_accepts_positional_address(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(client, "miner.getBlockTemplate", [payout_address])

    assert res["result"]["coinbase"]["address"] == payout_address


def test_get_block_template_requires_address():
    client, _, _ = new_test_client()

    res = rpc_call(client, "miner.getBlockTemplate", {}, expect_error=True)

    assert res["error"]["code"] == -32602
    assert res["error"]["data"]["detail"] == "address is required"


def test_get_block_template_rejects_invalid_address():
    client, _, _ = new_test_client()

    res = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": "not-a-valid-address"},
        expect_error=True,
    )

    assert res["error"]["code"] == -32602
    assert (
        "address must be a 32-byte 0x-prefixed hex or anim bech32 address"
        in res["error"]["data"]["detail"]
    )


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


def test_get_work_handles_callable_header_hash():
    """Ensure headers exposing hash() methods don't break parent hash resolution."""

    client, _, _ = new_test_client()

    prev_head = dict(miner_methods._LOCAL_HEAD)
    try:
        header = Header(
            v=1,
            chainId=1,
            height=1,
            parentHash=miner_methods.ZERO32,
            timestamp=0,
            stateRoot=miner_methods.ZERO32,
            txsRoot=miner_methods.ZERO32,
            receiptsRoot=miner_methods.ZERO32,
            proofsRoot=miner_methods.ZERO32,
            daRoot=miner_methods.ZERO32,
            mixSeed=miner_methods.ZERO32,
            poiesPolicyRoot=miner_methods.ZERO32,
            pqAlgPolicyRoot=miner_methods.ZERO32,
            thetaMicro=miner_methods._resolve_theta(),
            nonce=0,
            extra=b"",
        )
        miner_methods._LOCAL_HEAD.update({"height": 5, "hash": None, "header": header})

        res = rpc_call(client, "miner.getWork")
        assert res["result"]["jobId"] in miner_methods._JOB_CACHE
    finally:
        miner_methods._LOCAL_HEAD.clear()
        miner_methods._LOCAL_HEAD.update(prev_head)


def test_submit_work_accepts_valid_solution_and_updates_head():
    client, _, _ = new_test_client()
    job = rpc_call(client, "miner.getWork")["result"]

    nonce_hex = _find_nonce(job["signBytes"], job["target"])
    res = rpc_call(
        client, "miner.submitWork", {"jobId": job["jobId"], "nonce": nonce_hex}
    )

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
    bad = rpc_call(
        client, "miner.submitWork", {"jobId": job["jobId"]}, expect_error=True
    )
    assert bad["error"]["code"] == -32602

    # Mark head as advanced past the template height to force stale rejection
    miner_methods._record_local_block(job["height"], "0x01", None)
    stale = rpc_call(
        client,
        "miner.submitWork",
        {"jobId": job["jobId"], "nonce": "0x00"},
    )
    result = stale["result"]
    assert result["accepted"] is False
    assert result["stale"] is True


def test_submit_work_rejects_stale_parent():
    client, _, _ = new_test_client()
    job = rpc_call(client, "miner.getWork")["result"]

    miner_methods._JOB_CACHE[job["jobId"]]["parent_hash"] = b"\x01" * 32

    res = rpc_call(
        client,
        "miner.submitWork",
        {"jobId": job["jobId"], "nonce": "0x00"},
    )
    result = res["result"]
    assert result["accepted"] is False
    assert result["stale"] is True
    assert result["reason"] == "stale-parent"


def test_get_work_rejects_wrong_param_type():
    client, _, _ = new_test_client()

    res = rpc_call(client, "miner.getWork", "bad-type", expect_error=True)

    assert res["error"]["code"] == -32602


def test_get_work_disabled_when_stalled(monkeypatch: pytest.MonkeyPatch):
    class _Snap:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return dict(self._data)

    class _Svc:
        def status_snapshot(self):
            return _Snap({"peers_total": 3})

        def sync_status_snapshot(self):
            return _Snap(
                {"phase": "STALLED", "head_height": 0, "best_header_height": 10}
            )

    import p2p

    monkeypatch.setenv("ANIMICA_MINING_MIN_PEERS", "1")
    monkeypatch.setattr(p2p, "get_service", lambda: _Svc())

    client, _, _ = new_test_client()
    res = rpc_call(client, "miner.getWork")["result"]
    assert res["disabled"] is True
    assert res["reason"] == "sync_phase:stalled"


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


def test_miner_mine_with_zero_transactions():
    """
    Test that mining a payout-only block (no pending transactions) succeeds.
    
    This test ensures no UnboundLocalError is thrown when mining with zero txs.
    Regression test for PR #426 fix.
    """
    client, cfg, _ = new_test_client()
    
    # Ensure pending pool is empty (no transactions to include)
    try:
        from rpc.methods import tx as tx_methods
        # Clear any pending transactions
        if hasattr(tx_methods, "_FALLBACK_PENDING"):
            tx_methods._FALLBACK_PENDING.clear()
        if hasattr(tx_methods, "_FALLBACK_PENDING_TS"):
            tx_methods._FALLBACK_PENDING_TS.clear()
    except (ImportError, AttributeError):
        # If modules/attributes not available, continue anyway
        pass
    
    # Mine a single block with no pending transactions
    start_height = rpc_call(client, "chain.getHead")["result"].get("height") or 0
    result = rpc_call(client, "miner.mine", [1])["result"]
    
    # Verify mining succeeded
    assert result["mined"] == 1
    assert result["height"] >= start_height + 1
    assert "totalReward" in result
    assert "rewards" in result
    assert len(result["rewards"]) == 1
