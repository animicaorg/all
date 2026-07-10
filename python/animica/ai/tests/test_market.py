"""Unit tests for animica.ai.market — the aicf.* RPC client + local job tracking.

The RPC transport is mocked (no node needed); these assert each helper calls the
correct method with the correct params, and that local job tracking round-trips.
"""

from __future__ import annotations

from animica.ai import market


def test_helpers_call_correct_rpc(monkeypatch):
    seen = []

    def fake_call(method, params=None, **kw):
        seen.append((method, params))
        return {"ok": method}

    monkeypatch.setattr(market, "call", fake_call)

    market.estimate(100, 256, "fast")
    market.job_status("0xabc")
    market.settle_job("0xabc")
    market.register_worker("anim1x", tiers=["fast"], hardware={"cpu_cores": 4})
    market.worker_status("anim1x")
    market.worker_earnings("anim1x")

    methods = [m for m, _ in seen]
    assert methods == [
        "aicf.estimateJobCost", "aicf.jobStatus", "aicf.settleJob",
        "aicf.workerRegister", "aicf.workerStatus", "aicf.workerEarnings",
    ]
    # estimate param mapping
    assert seen[0][1] == {"prompt_tokens": 100, "max_output_tokens": 256, "tier_preferred": "fast"}
    # workerRegister carries tiers + hardware
    assert seen[3][1]["address"] == "anim1x"
    assert seen[3][1]["tiers"] == ["fast"]
    assert seen[3][1]["hardware"] == {"cpu_cores": 4}


def test_treasury_address_unwraps(monkeypatch):
    monkeypatch.setattr(market, "call", lambda *a, **k: {"address": "anim1treasury"})
    assert market.treasury_address() == "anim1treasury"
    monkeypatch.setattr(market, "call", lambda *a, **k: "anim1plain")
    assert market.treasury_address() == "anim1plain"


def test_submit_job_passes_envelope(monkeypatch):
    captured = {}

    def fake_call(method, params=None, **kw):
        captured["method"] = method
        captured["params"] = params
        return {"job_id": "0xjob"}

    monkeypatch.setattr(market, "call", fake_call)
    out = market.submit_job({"prompt": "hi"}, {"txn_hex": "deadbeef"})
    assert out["job_id"] == "0xjob"
    assert captured["method"] == "aicf.submitInferenceJob"
    assert captured["params"]["payment"] == {"txn_hex": "deadbeef"}
    assert captured["params"]["spec"] == {"prompt": "hi"}


def test_local_job_tracking_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_HOME", str(tmp_path))
    assert market.local_jobs() == []
    market.record_job({"job_id": "0x1", "cost_animica": 0.5})
    market.record_job({"job_id": "0x2", "cost_animica": 1.5})
    jobs = market.local_jobs()
    assert [j["job_id"] for j in jobs] == ["0x1", "0x2"]
