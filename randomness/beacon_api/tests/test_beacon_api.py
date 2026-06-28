"""End-to-end tests for the public beacon HTTP API + the served page/verifier."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from randomness.beacon_api.server import create_app


def _client():
    app = create_app(start=False)            # don't start the background thread in tests
    app.state.driver._produce()              # ensure rounds 0 and 1 exist (chain)
    return TestClient(app), app.state.driver


def test_healthz_and_info():
    c, _ = _client()
    assert c.get("/healthz").json()["ok"] is True
    info = c.get("/beacon/info").json()
    assert info["hash"].startswith("sha3-256")
    assert info["domains"]["beacon"] == "animica/qrng/beacon/v1"
    assert info["mode"] in ("pseudo", "hardware")


def test_latest_and_round_and_chain():
    c, _ = _client()
    latest = c.get("/beacon/latest").json()
    assert latest["round"] >= 1 and len(bytes.fromhex(latest["value"])) == 32
    r0 = c.get("/beacon/round/0").json()
    assert r0["round"] == 0 and r0["prev"] == ""        # genesis has empty prev
    # hash chain: latest.prev == previous round's value
    prev = c.get("/beacon/round/" + str(latest["round"] - 1)).json()
    assert latest["prev"] == prev["value"]
    assert c.get("/beacon/round/99999").status_code == 404
    assert len(c.get("/beacon/chain?limit=10").json()["rounds"]) >= 2


def test_beacon_value_is_verifiable():
    c, _ = _client()
    b = c.get("/beacon/latest").json()
    recomputed = hashlib.sha3_256(
        b"animica/qrng/beacon/v1"
        + int(b["round"]).to_bytes(8, "big")
        + bytes.fromhex(b["prev"])
        + bytes.fromhex(b["aggregate_commitment"])
    ).hexdigest()
    assert recomputed == b["value"]


def test_draw_is_verifiable():
    c, _ = _client()
    d = c.get("/draw?kind=dice&sides=6&count=3&request_id=demo-1").json()
    assert len(d["output"]) == 3 and all(1 <= x <= 6 for x in d["output"])
    assert d["verified"] is True
    # determinism: same request -> same output
    d2 = c.get("/draw?kind=dice&sides=6&count=3&request_id=demo-1").json()
    assert d2["output"] == d["output"]
    # server /verify recomputes and agrees
    assert c.post("/verify", json=d).json()["verified"] is True
    # tamper -> fails
    bad = dict(d); bad["output"] = [9, 9, 9]
    assert c.post("/verify", json=bad).json()["verified"] is False


def test_lottery_distinct_winners():
    c, _ = _client()
    d = c.get("/draw?kind=lottery&entries=a,b,c,d,e,f&k=3&request_id=raffle").json()
    assert len(d["output"]) == 3 and len(set(d["output"])) == 3
    assert set(d["output"]).issubset({"a", "b", "c", "d", "e", "f"})
    assert d["verified"] is True


def test_page_and_verifier_served():
    c, _ = _client()
    page = c.get("/")
    assert page.status_code == 200 and "Animica Quantum Randomness Beacon" in page.text
    js = c.get("/verify.js")
    assert js.status_code == 200 and "sha3_256" in js.text
