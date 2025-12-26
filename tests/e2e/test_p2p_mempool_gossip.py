"""E2E test: mempool gossip across two P2P-connected nodes."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest


def is_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_for_rpc(port: int, timeout: int = 30) -> bool:
    """Wait for RPC server to become available."""
    import socket

    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(("127.0.0.1", port))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def rpc_call(port: int, method: str, params: Any = None) -> dict[str, Any]:
    """Make JSON-RPC call to node."""
    import json
    import urllib.error
    import urllib.request

    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or [],
        "id": 1,
    }

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/rpc",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(f"RPC call failed: {e}")


def load_signed_tx_hex() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    tx_path = repo_root / "mempool" / "fixtures" / "txs_cbor" / "tx1.cbor"
    if not tx_path.is_file():
        pytest.skip("Missing signed tx fixture at mempool/fixtures/txs_cbor/tx1.cbor")
    return "0x" + tx_path.read_bytes().hex()


@pytest.mark.skipif(
    not is_port_available(8547) or not is_port_available(8548),
    reason="Ports 8547 or 8548 not available",
)
@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("SKIP_E2E_P2P") == "true",
    reason="E2E P2P tests disabled in CI",
)
def test_p2p_mempool_gossip_two_nodes() -> None:
    """Submit tx to node A and verify it appears in node B mempool."""
    repo_root = Path(__file__).resolve().parents[2]
    raw_tx_hex = load_signed_tx_hex()

    with tempfile.TemporaryDirectory() as tmpdir:
        node_a_data = Path(tmpdir) / "node_a"
        node_b_data = Path(tmpdir) / "node_b"
        node_a_data.mkdir()
        node_b_data.mkdir()

        node_a_env = os.environ.copy()
        node_a_env.pop("ANIMICA_TRUSTED_RPC_URL", None)
        node_a_env.update(
            {
                "ANIMICA_CHAIN_ID": "1337",
                "ANIMICA_RPC_PORT": "8547",
                "ANIMICA_RPC_HOST": "127.0.0.1",
                "ANIMICA_RPC_DB_URI": f"sqlite:///{node_a_data}/chain.db",
                "P2P_ENABLE": "true",
                "P2P_LISTEN": "127.0.0.1:30335",
                "P2P_SEEDS": "",
                "ANIMICA_PEER_STORE_PATH": str(node_a_data / "peers"),
                "ANIMICA_LOG_LEVEL": "INFO",
            }
        )

        node_b_env = os.environ.copy()
        node_b_env.pop("ANIMICA_TRUSTED_RPC_URL", None)
        node_b_env.update(
            {
                "ANIMICA_CHAIN_ID": "1337",
                "ANIMICA_RPC_PORT": "8548",
                "ANIMICA_RPC_HOST": "127.0.0.1",
                "ANIMICA_RPC_DB_URI": f"sqlite:///{node_b_data}/chain.db",
                "P2P_ENABLE": "true",
                "P2P_LISTEN": "127.0.0.1:30336",
                "P2P_SEEDS": "/ip4/127.0.0.1/tcp/30335",
                "ANIMICA_PEER_STORE_PATH": str(node_b_data / "peers"),
                "ANIMICA_LOG_LEVEL": "INFO",
            }
        )

        proc_a = subprocess.Popen(
            [sys.executable, "-m", "rpc"],
            cwd=repo_root,
            env=node_a_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            if not wait_for_rpc(8547, timeout=30):
                raise RuntimeError("Node A failed to start")

            proc_b = subprocess.Popen(
                [sys.executable, "-m", "rpc"],
                cwd=repo_root,
                env=node_b_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                if not wait_for_rpc(8548, timeout=30):
                    raise RuntimeError("Node B failed to start")

                time.sleep(5)

                send_resp = rpc_call(8547, "tx.sendRawTransaction", [raw_tx_hex])
                tx_hash = send_resp.get("result")
                assert isinstance(tx_hash, str), f"Unexpected send result: {send_resp}"

                deadline = time.time() + 10
                found = False
                while time.time() < deadline:
                    pending_resp = rpc_call(8548, "mempool.getPending")
                    pending = pending_resp.get("result", [])
                    if tx_hash in pending:
                        found = True
                        break
                    time.sleep(0.5)

                assert found, f"tx {tx_hash} not seen in node B mempool"
            finally:
                proc_b.terminate()
                try:
                    proc_b.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc_b.kill()
        finally:
            proc_a.terminate()
            try:
                proc_a.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc_a.kill()
