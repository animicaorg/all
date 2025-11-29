"""
Test utilities for Animica RPC.

Usage in tests:
    from rpc.tests import new_test_client, rpc_call

    def test_health():
        client, cfg, tmpdir = new_test_client()
        r = client.get("/healthz")
        assert r.json()["ok"] is True

    def test_rpc_example():
        client, cfg, _ = new_test_client()
        res = rpc_call(client, "chain.getChainId")
        assert res["result"] == cfg.chain_id
"""
from __future__ import annotations

import json
import os
import tempfile
import typing as t
from contextlib import contextmanager

from fastapi.testclient import TestClient

from rpc import config as rpc_config
from rpc import server as rpc_server


def _temp_db_uri(tmpdir: str | None = None) -> tuple[str, str]:
    """
    Return (db_uri, tmpdir). Uses a real SQLite file in a unique temp directory
    to exercise migrations and multiple connections.
    """
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="animica_rpc_test_")
    db_path = os.path.join(tmpdir, "animica.db")
    # sqlite:///absolute_path
    return f"sqlite:///{db_path}", tmpdir


def make_test_config(tmpdir: str | None = None) -> tuple[rpc_config.Config, str]:
    """
    Build a minimal Config suitable for tests (quiet logs, wide-open CORS).
    """
    db_uri, tmp = _temp_db_uri(tmpdir)
    cfg = rpc_config.Config(
        host="127.0.0.1",
        port=0,  # unused by TestClient
        db_uri=db_uri,
        chain_id=1,
        logging="ERROR",
        cors_allow_origins=["*"],
        rate_limit_per_ip=0,  # disable for tests
        rate_limit_per_method=0,
    )
    return cfg, tmp


def new_test_client(tmpdir: str | None = None) -> tuple[TestClient, rpc_config.Config, str]:
    """
    Create a TestClient bound to a fresh app with a temporary SQLite DB.
    Returns (client, cfg, tmpdir).
    """
    cfg, tmp = make_test_config(tmpdir)
    app = rpc_server.create_app(cfg)
    # Ensure the RPC context is initialized with the temp config even if the
    # TestClient does not trigger FastAPI startup events in this environment.
    rpc_server.deps.ensure_started(cfg)
    client = TestClient(app)
    return client, cfg, tmp


def rpc_call(
    client: TestClient,
    method: str,
    params: t.Any | None = None,
    *,
    id: t.Any = 1,
    expect_error: bool = False,
) -> dict:
    """
    Convenience wrapper to POST a JSON-RPC request to /rpc and return the parsed response.
    Set expect_error=True to assert an 'error' object is present.
    """
    payload: dict = {"jsonrpc": "2.0", "method": method, "id": id}
    if params is not None:
        payload["params"] = params
    resp = client.post("/rpc", json=payload)
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
    data = resp.json()
    if expect_error:
        assert "error" in data, f"expected JSON-RPC error, got {data}"
    else:
        assert "result" in data, f"expected JSON-RPC result, got {data}"
    return data


@contextmanager
def ws_connect(client: TestClient, path: str = "/ws"):
    """
    Context manager to open a WebSocket to the RPC app.
    """
    with client.websocket_connect(path) as ws:
        yield ws


def fetch_openrpc(client: TestClient) -> dict:
    """
    Fetch the OpenRPC document served by the app.
    """
    r = client.get("/openrpc.json")
    assert r.status_code == 200, f"OpenRPC not available: {r.status_code}"
    return r.json()


__all__ = [
    "new_test_client",
    "rpc_call",
    "ws_connect",
    "fetch_openrpc",
    "make_test_config",
]
