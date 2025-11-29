from rpc.tests import new_test_client, rpc_call


def test_get_work_returns_template():
    client, cfg, _ = new_test_client()
    res = rpc_call(client, "miner.getWork")
    job = res["result"]
    assert job["height"] >= 1
    assert "header" in job and isinstance(job["header"], dict)
    assert job["header"].get("number") == job["height"]
    assert "thetaMicro" in job
    assert "shareTarget" in job


def test_submit_share_accepts_dummy_share():
    client, _, _ = new_test_client()
    payload = {
        "header": {"number": 1},
        "nonce": "0x01",
    }
    res = rpc_call(client, "miner.submitShare", payload)
    assert res["result"]["accepted"] is True
    assert res["result"]["share"]["nonce"] == "0x01"
