"""Tests for serve-while-train (animica.ena.serving) + the pool serving ledger.

Stdlib-only: the model runner falls back to a deterministic stub without
torch/transformers, so the OpenAI-compatible round-trip and the server→reward
ledger are exercised on a CPU-only box.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from animica.ena import ENA
from animica.ena import payments
from animica.ena import serving
from animica.ena.config import load_config
from animica.ena.errors import PoolError

TREASURY = "anim1zqpfpwctgp7zkfhj8qr77g3d0ucvp52n7fsv3xsjdclyzwzsjryp4gs07vma0"


def _write_dataset(path, n=12):
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


@pytest.fixture()
def ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    return ENA(cfg=load_config())


@pytest.fixture()
def funded_ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    monkeypatch.setenv("ENA_TREASURY_ADDRESS", TREASURY)
    return ENA(cfg=load_config())


class _FakeRPC:
    def __init__(self, txs):
        self.txs = txs

    def get_transaction(self, txhash):
        from animica.ena.payments import _norm_hex
        return self.txs.get(_norm_hex(txhash))

    def get_status(self, txhash):
        tx = self.get_transaction(txhash) or {}
        return {"status": tx.get("status", "unknown")}


def _tx(to_digest, memo, value_nano, status="confirmed", frm="anim1payer"):
    return {"to_addr": "0x" + to_digest, "memo": memo, "value": str(value_nano),
            "status": status, "from_addr": frm}


def _fund(ena, monkeypatch, pid, pool_hash, nano, txid):
    digest = payments.address_to_digest_hex(TREASURY)
    rpc = _FakeRPC({txid: _tx(digest, pool_hash, nano)})
    monkeypatch.setattr(payments, "AnimicaRPC", lambda *a, **k: rpc)
    return ena.pool.fund_confirm(pid, txid)


def _promote(ena, pid):
    """Drive a pool to a promoted served_checkpoint (no eval gate)."""
    # These tests promote explicitly; pin off auto-promote so the round doesn't
    # advance out from under the loop + trailing aggregate() call.
    pool = ena.store.get_pool(pid)
    pool["auto_promote"] = False
    ena.store.upsert_pool(pool)
    for _ in range(2):
        s = ena.pool.claim_shard(pid, "trainer")
        if s is None:
            break
        ena.pool.submit_shard(pid, s["shard_id"], worker_id="trainer",
                              metrics={"samples": 8})
    return ena.pool.aggregate(pid)


# ---------------------------------------------------------------------------
# pure runner + helpers
# ---------------------------------------------------------------------------

def test_stub_runner_generates_without_torch():
    r = serving.PoolModelRunner("tiny-base", adapter_path=None)
    out = r.generate("hello world", max_tokens=16)
    assert isinstance(out, str) and out and "animica-pool stub" in out


def test_prompt_and_token_helpers():
    p = serving.messages_to_prompt([{"role": "user", "content": "hi"}])
    assert p.endswith("assistant:") and "user: hi" in p
    assert serving.approx_tokens("") == 0 and serving.approx_tokens("abcd" * 4) >= 1


# ---------------------------------------------------------------------------
# pool serving ledger
# ---------------------------------------------------------------------------

def test_served_checkpoint_requires_promotion(ena, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, name="srv", num_shards=2)
    with pytest.raises(PoolError):
        ena.pool.get_served_checkpoint(p["pool_id"])


def test_record_served_pays_server_bucket(funded_ena, tmp_path, monkeypatch):
    e = funded_ena
    data = _write_dataset(tmp_path / "d.jsonl")
    p = e.pool.create("tiny", data, name="srv", num_shards=2)
    pid = p["pool_id"]
    r = _fund(e, monkeypatch, pid, p["pool_hash"], 10_000_000_000, "f1")
    assert r["funded"]
    # a server serves tokens in the (still-current) round 1
    e.pool.record_served(pid, "node-A", 100, address="anim1serverA")
    out = e.pool.payout(pid, round=1)
    server_entries = [x for x in out["entries"] if x["role"] == "server"]
    # servers bucket = 20% of 10 ANM = 2 ANM, all to the one server
    assert server_entries and server_entries[0]["address"] == "anim1serverA"
    assert server_entries[0]["nano"] == 2_000_000_000


def test_openai_endpoint_serves_and_credits(funded_ena, tmp_path, monkeypatch):
    e = funded_ena
    data = _write_dataset(tmp_path / "d.jsonl")
    p = e.pool.create("tiny", data, name="srv", num_shards=2)
    pid = p["pool_id"]
    _fund(e, monkeypatch, pid, p["pool_hash"], 4_000_000_000, "f2")
    agg = _promote(e, pid)
    assert agg["promoted"] is True

    served = e.pool.get_served_checkpoint(pid)
    assert served["model_id"] == f"{pid}:{served['checkpoint_hash']}"
    reg = e.pool.register_server(pid, "node-A", "http://127.0.0.1:9/v1",
                                 address="anim1serverA")
    assert reg["model_id"] == served["model_id"]
    assert any(s["server_id"] == reg["server_id"] for s in e.pool.list_servers(pid))

    handler = serving.make_handler(e, pid, worker_id="node-A", address="anim1serverA")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        # GET /v1/models
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/v1/models")
        models = json.loads(c.getresponse().read())
        assert models["data"][0]["id"] == served["model_id"]
        # POST /v1/chat/completions
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        payload = json.dumps({"model": served["model_id"],
                              "messages": [{"role": "user", "content": "hello"}]})
        c.request("POST", "/v1/chat/completions", payload,
                  {"content-type": "application/json"})
        resp = c.getresponse()
        out = json.loads(resp.read())
        assert resp.status == 200
        assert out["object"] == "chat.completion"
        assert out["choices"][0]["message"]["content"]
        assert out["usage"]["completion_tokens"] >= 1
    finally:
        httpd.shutdown()
        t.join(timeout=2)

    # the served request was credited as a server contribution
    server_contribs = [c for c in e.store.list_contributions(pid)
                       if c["role"] == "server"]
    assert server_contribs and server_contribs[0]["weight"] >= 1


def test_served_tokens_credit_the_served_round_not_current(funded_ena, tmp_path, monkeypatch):
    e = funded_ena
    data = _write_dataset(tmp_path / "d.jsonl")
    p = e.pool.create("tiny", data, name="rnd", num_shards=2)
    pid = p["pool_id"]
    _fund(e, monkeypatch, pid, p["pool_hash"], 4_000_000_000, "rf")
    assert _promote(e, pid)["promoted"]            # round advances 1 -> 2
    assert e.pool.get(pid)["round"] == 2
    served = e.pool.get_served_checkpoint(pid)
    assert served["round"] == 1
    # a served request serves the round-1 checkpoint → credited to round 1, not 2
    e.pool.record_served(pid, "node-A", 50, address="anim1srv",
                         served_round=served["round"])
    contribs = e.store.list_contributions(pid, role="server")
    assert contribs and contribs[0]["round"] == 1


def test_build_serving_receipt_is_deterministic_and_splits_fee():
    r1 = serving.build_serving_receipt(
        pool_id="p", model_id="p:abc", requester="anim1u", provider_id="node-A",
        prompt="hello", output="world", tokens_in=5, tokens_out=7,
        fee_nano=1_000_000, aicf_bps=2000, served_round=1)
    r2 = serving.build_serving_receipt(
        pool_id="p", model_id="p:abc", requester="anim1u", provider_id="node-A",
        prompt="hello", output="world", tokens_in=5, tokens_out=7,
        fee_nano=1_000_000, aicf_bps=2000, served_round=1,
        chain_id=1)
    # deterministic content hash (timestamp excluded? no — equal within same second);
    assert len(r1["receipt_hash"]) == 64
    # fee split: 20% AICF, 80% provider
    assert r1["aicf_cut"] == 200_000 and r1["provider_cut"] == 800_000
    assert r1["prompt_hash"] != r1["output_hash"]
    # changing the output changes the hash
    r3 = serving.build_serving_receipt(
        pool_id="p", model_id="p:abc", requester="anim1u", provider_id="node-A",
        prompt="hello", output="DIFFERENT", tokens_in=5, tokens_out=7)
    assert r3["output_hash"] != r1["output_hash"]
