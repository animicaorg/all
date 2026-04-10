from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
from typer.testing import CliRunner

from animica.cli import node as node_cli
from rpc import config as rpc_config
from rpc import server as rpc_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_rpc_server(port: int, tmp_path: Path):
    db_uri = f"sqlite:///{tmp_path / 'animica.db'}"
    cfg = rpc_config.Config(
        host="127.0.0.1",
        port=port,
        db_uri=db_uri,
        chain_id=1,
        logging="ERROR",
        cors_allow_origins=["*"],
        rate_limit_per_ip=0,
        rate_limit_per_method=0,
    )
    app = rpc_server.create_app(cfg)
    rpc_server.deps.ensure_started(cfg)

    import uvicorn

    config = uvicorn.Config(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config=config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


def _rpc_call(url: str, method: str) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": []}
    response = httpx.post(url, json=payload, timeout=2.0)
    response.raise_for_status()
    return response.json()


def test_rpc_ping_and_cli_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_P2P_ENABLE", "0")
    monkeypatch.setenv("ANIMICA_P2P_CORE_ENABLE", "0")

    port = _free_port()
    server, thread = _start_rpc_server(port, tmp_path)
    rpc_url = f"http://127.0.0.1:{port}/rpc"

    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                ping = _rpc_call(rpc_url, "node.ping")
                if ping.get("result", {}).get("ok") is True:
                    break
            except Exception:
                time.sleep(0.2)
        status = _rpc_call(rpc_url, "node.getStatus")
        assert status.get("result", {}).get("rpc_reachable") is True

        runner = CliRunner()
        result = runner.invoke(
            node_cli.app,
            ["status", "--rpc-url", rpc_url, "--max-retries", "1"],
        )
        assert result.exit_code == 0, result.output
        assert "RPC reachable: yes" in result.output
    finally:
        server.should_exit = True
        thread.join(timeout=5)
