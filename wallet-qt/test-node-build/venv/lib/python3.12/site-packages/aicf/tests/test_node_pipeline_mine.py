import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn

from rpc import config as rpc_config
from rpc import server as rpc_server


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_rpc_server(
    cfg: rpc_config.Config,
) -> tuple[uvicorn.Server, threading.Thread]:
    app = rpc_server.create_app(cfg)
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            urllib.request.urlopen(
                f"http://{cfg.host}:{cfg.port}/healthz", timeout=0.25
            )
            break
        except Exception:
            time.sleep(0.1)
    return server, thread


def test_node_pipeline_mine_roundtrip(tmp_path):
    root = Path(__file__).resolve().parents[2]
    genesis_path = root / "genesis" / "genesis.sample.devnet.json"
    db_uri = f"sqlite:///{tmp_path/'chain.db'}"
    port = _pick_port()
    cfg = rpc_config.Config(
        host="127.0.0.1",
        port=port,
        db_uri=db_uri,
        chain_id=1337,
        logging="ERROR",
        cors_allow_origins=["*"],
        rate_limit_per_ip=0,
        rate_limit_per_method=0,
        genesis_path=genesis_path,
    )

    server, thread = _start_rpc_server(cfg)
    rpc_url = f"http://{cfg.host}:{cfg.port}/rpc"
    try:
        start_status = subprocess.run(
            [
                sys.executable,
                "-m",
                "aicf.cli.node_pipeline",
                "status",
                "--rpc-url",
                rpc_url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        start_out = start_status.stdout.strip()
        start_height = int(start_out.split("height=")[1].split()[0])

        mine = subprocess.run(
            [
                sys.executable,
                "-m",
                "aicf.cli.node_pipeline",
                "mine",
                "--count",
                "2",
                "--rpc-url",
                rpc_url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        mined_height = int(mine.stdout.strip())
        assert mined_height - start_height == 2

        status = subprocess.run(
            [
                sys.executable,
                "-m",
                "aicf.cli.node_pipeline",
                "status",
                "--rpc-url",
                rpc_url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        out = status.stdout.strip()
        assert f"height={start_height + 2}" in out
        assert "chainId=1337" in out
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert thread.is_alive() is False
