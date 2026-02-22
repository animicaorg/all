from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from animica_studio.services.ena_daemon_server import app


client = TestClient(app)


def test_health_exposes_da_capability() -> None:
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["capabilities"]["da"] is True


def test_da_put_get_and_proof_roundtrip() -> None:
    payload = base64.b64encode(b"animica-da-payload").decode("ascii")

    put = client.post("/da/put", json={"data": payload, "namespace": "ena"})
    assert put.status_code == 200
    put_body = put.json()
    assert put_body["ok"] is True
    commitment = put_body["commitment"]

    got = client.get(f"/da/get/{commitment}")
    assert got.status_code == 200
    got_body = got.json()
    assert got_body == {
        "ok": True,
        "commitment": commitment,
        "data": payload,
        "namespace": "ena",
    }

    proof = client.get(f"/da/proof/{commitment}")
    assert proof.status_code == 200
    proof_body = proof.json()
    assert proof_body["ok"] is True
    assert proof_body["commitment"] == commitment
    assert proof_body["proof"]["verified"] is True


def test_da_put_rejects_invalid_base64() -> None:
    res = client.post("/da/put", json={"data": "%%%", "namespace": "ena"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "base64" in body["error"]
