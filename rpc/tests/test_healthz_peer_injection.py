from __future__ import annotations

from fastapi.testclient import TestClient

from rpc import deps
from rpc.access_policy import AccessMode
from rpc.server import create_app
from rpc.tests import make_test_config


def test_healthz_and_peer_injection_from_localhost() -> None:
    cfg, _tmp = make_test_config()
    cfg.access_mode = AccessMode.PUBLIC_BOOTSTRAP.value

    app = create_app(cfg)
    deps.ensure_started(cfg)

    client = TestClient(app)
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json().get("ok") is True

    payload = {"jsonrpc": "2.0", "id": 1, "method": "p2p.importPeers", "params": [["seed.example:30333"]]}
    resp = client.post("/rpc", json=payload, headers={"x-forwarded-for": "127.0.0.1"})
    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    assert "error" not in body
