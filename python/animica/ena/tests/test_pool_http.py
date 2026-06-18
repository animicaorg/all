"""HTTP-route tests for the collaborative training-pool coordinator.

Boots the stdlib ENA service on an ephemeral port in a daemon thread and drives
the full /pool/* lifecycle over real HTTP (http.client), mirroring the in-process
coverage in test_pool.py. Funding routes use a monkeypatched AnimicaRPC.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from animica.ena import ENA
from animica.ena import payments
from animica.ena.config import load_config
from animica.ena.service import _make_handler

TREASURY = "anim1zqpfpwctgp7zkfhj8qr77g3d0ucvp52n7fsv3xsjdclyzwzsjryp4gs07vma0"


def _write_dataset(path, n=20) -> str:
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


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


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    monkeypatch.setenv("ENA_TREASURY_ADDRESS", TREASURY)
    ena = ENA(cfg=load_config())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(ena))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield ena, httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _call(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        payload = None
        headers = {}
        if body is not None:
            payload = json.dumps(body)
            headers["content-type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        parsed = json.loads(data) if data else {}
        return resp.status, parsed
    finally:
        conn.close()


def test_pool_http_lifecycle(server, tmp_path, monkeypatch):
    ena, port = server
    data = _write_dataset(tmp_path / "d.jsonl", n=20)

    # --- POST /pool/create ---
    status, p = _call(port, "POST", "/pool/create", {
        "base_model": "tiny", "dataset": data, "method": "lora",
        "name": "http-demo", "num_shards": 2, "auto_promote": False})
    assert status == 200
    pid = p["pool_id"]
    assert pid.startswith("enapool-")
    assert p["status"] == "open" and p["round"] == 1

    # --- GET /pool/list ---
    status, listing = _call(port, "GET", "/pool/list")
    assert status == 200
    assert any(pl["pool_id"] == pid for pl in listing["pools"])

    # list with status filter still returns the wrapper shape.
    status, open_listing = _call(port, "GET", "/pool/list?status=open")
    assert status == 200 and isinstance(open_listing["pools"], list)

    # --- GET /pool/status ---
    status, st = _call(port, "GET", f"/pool/status?pool_id={pid}")
    assert status == 200
    assert st["pool_id"] == pid

    # missing pool_id -> 400.
    status, err = _call(port, "GET", "/pool/status")
    assert status == 400 and err["error"] == "pool_id required"

    # --- GET /pool/leaderboard ---
    status, lb = _call(port, "GET", f"/pool/leaderboard?pool_id={pid}")
    assert status == 200 and isinstance(lb["leaderboard"], list)

    status, err = _call(port, "GET", "/pool/leaderboard")
    assert status == 400 and err["error"] == "pool_id required"

    # --- POST /pool/fund/quote ---
    status, q = _call(port, "POST", "/pool/fund/quote", {
        "pool_id": pid, "reward_anm": 10.0, "requester": "anim1alice"})
    assert status == 200
    assert q["memo"] == p["pool_hash"]

    # --- POST /pool/fund/confirm (monkeypatched RPC) ---
    digest = payments.address_to_digest_hex(TREASURY)
    rpc = _FakeRPC({
        "a1": _tx(digest, p["pool_hash"], 6_000_000_000, frm="anim1alice"),
        "b2": _tx(digest, p["pool_hash"], 4_000_000_000, frm="anim1bob"),
    })
    monkeypatch.setattr(payments, "AnimicaRPC", lambda *a, **k: rpc)
    status, r1 = _call(port, "POST", "/pool/fund/confirm", {
        "pool_id": pid, "txid": "a1", "requester": "anim1alice"})
    assert status == 200 and r1["funded"] is True
    status, r2 = _call(port, "POST", "/pool/fund/confirm", {
        "pool_id": pid, "txid": "b2", "requester": "anim1bob"})
    assert status == 200 and r2["funded"] is True

    # --- POST /pool/claim + /pool/submit (two trainers) ---
    status, r0 = _call(port, "POST", "/pool/claim", {
        "pool_id": pid, "worker_id": "trainer-A"})
    s0 = r0["claimed"]
    assert status == 200 and "manifest" in s0
    status, r1 = _call(port, "POST", "/pool/claim", {
        "pool_id": pid, "worker_id": "trainer-B"})
    s1 = r1["claimed"]
    assert status == 200 and s1["shard_id"] != s0["shard_id"]

    status, sub0 = _call(port, "POST", "/pool/submit", {
        "pool_id": pid, "shard_id": s0["shard_id"], "worker_id": "trainer-A",
        "miner_address": "anim1trainerA",
        "metrics": {"samples": 30, "train_loss": 0.5}})
    assert status == 200 and len(sub0["receipt_hash"]) == 64
    status, sub1 = _call(port, "POST", "/pool/submit", {
        "pool_id": pid, "shard_id": s1["shard_id"], "worker_id": "trainer-B",
        "miner_address": "anim1trainerB",
        "metrics": {"samples": 10, "train_loss": 0.7}})
    assert status == 200 and len(sub1["checkpoint_hash"]) == 64

    # --- POST /pool/aggregate ---
    status, agg = _call(port, "POST", "/pool/aggregate", {"pool_id": pid})
    assert status == 200
    assert agg["promoted"] is True and agg["next_round"] == 2

    # --- POST /pool/payout ---
    status, out = _call(port, "POST", "/pool/payout", {"pool_id": pid, "round": 1})
    assert status == 200
    assert out["paid_nano"] == 8_000_000_000
    roles = {ent["role"] for ent in out["entries"]}
    assert "funder" in roles and "trainer" in roles
