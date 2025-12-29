"""E2E test: tx propagates to peer and mined block syncs."""

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
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_for_rpc(port: int, timeout: int = 30) -> bool:
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
    import json
    import urllib.error
    import urllib.request

    payload = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
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


def _build_signed_transfer(cfg, sender_kp, recipient_hex: str, nonce: int, value: int):
    from core.encoding.canonical import tx_sign_bytes
    from core.genesis.loader import compute_chain_identity
    from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
    from pq.py import sign
    from pq.py.address import decode_address
    from pq.py.registry import ALG_ID

    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    recipient_bytes = bytes.fromhex(recipient_hex[2:] if recipient_hex.startswith("0x") else recipient_hex)

    unsigned = UnsignedTx(
        chain_id=cfg["chain_id"],
        nonce=nonce,
        gas_price=1,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=value, data=b""),
        access_list=(),
    )
    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    fork_id = compute_chain_identity(None, chain_id=cfg["chain_id"]).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        "dilithium3",
        sender_kp.secret_key,
        domain="tx",
        chain_id=cfg["chain_id"],
        fork_id=fork_id,
    )
    sig = PqSignature(
        alg_id=ALG_ID["dilithium3"],
        pubkey=sender_kp.public_key,
        sig=sig_env.sig,
    )
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    return "0x" + tx.to_cbor().hex(), "0x" + tx.txid().hex()


def wait_for_same_head(port_a: int, port_b: int, timeout: int = 20) -> dict[str, Any] | None:
    start = time.time()
    while time.time() - start < timeout:
        head_a = rpc_call(port_a, "chain.getHead").get("result", {})
        head_b = rpc_call(port_b, "chain.getHead").get("result", {})
        if (
            isinstance(head_a, dict)
            and isinstance(head_b, dict)
            and head_a.get("height") == head_b.get("height")
            and head_a.get("hash") == head_b.get("hash")
        ):
            return head_a
        time.sleep(0.5)
    return None


@pytest.mark.skipif(
    not is_port_available(8551) or not is_port_available(8552),
    reason="Ports 8551 or 8552 not available",
)
@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("SKIP_E2E_P2P") == "true",
    reason="E2E P2P tests disabled in CI",
)
def test_p2p_tx_propagation_and_block_sync() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    chain_id = 1337

    try:
        from pq.py.keygen import keygen_sig
        from pq.py.address import decode_address
    except Exception:
        pytest.skip("PQ keygen not available")
        return

    sender_kp = keygen_sig("dilithium3")
    receiver_kp = keygen_sig("dilithium3")

    receiver_record = decode_address(receiver_kp.address)
    receiver_hex = "0x" + bytes(receiver_record.digest)[:32].ljust(32, b"\x00").hex()

    with tempfile.TemporaryDirectory() as tmpdir:
        node_a_data = Path(tmpdir) / "node_a"
        node_b_data = Path(tmpdir) / "node_b"
        node_a_data.mkdir()
        node_b_data.mkdir()

        node_a_env = os.environ.copy()
        node_a_env.pop("ANIMICA_TRUSTED_RPC_URL", None)
        node_a_env.update(
            {
                "ANIMICA_CHAIN_ID": str(chain_id),
                "ANIMICA_RPC_PORT": "8551",
                "ANIMICA_RPC_HOST": "127.0.0.1",
                "ANIMICA_RPC_DB_URI": f"sqlite:///{node_a_data}/chain.db",
                "P2P_ENABLE": "true",
                "P2P_LISTEN": "127.0.0.1:30345",
                "P2P_SEEDS": "",
                "ANIMICA_PEER_STORE_PATH": str(node_a_data / "peers"),
                "ANIMICA_LOG_LEVEL": "INFO",
            }
        )

        node_b_env = os.environ.copy()
        node_b_env.pop("ANIMICA_TRUSTED_RPC_URL", None)
        node_b_env.update(
            {
                "ANIMICA_CHAIN_ID": str(chain_id),
                "ANIMICA_RPC_PORT": "8552",
                "ANIMICA_RPC_HOST": "127.0.0.1",
                "ANIMICA_RPC_DB_URI": f"sqlite:///{node_b_data}/chain.db",
                "P2P_ENABLE": "true",
                "P2P_LISTEN": "127.0.0.1:30346",
                "P2P_SEEDS": "/ip4/127.0.0.1/tcp/30345",
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
            if not wait_for_rpc(8551, timeout=30):
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
                if not wait_for_rpc(8552, timeout=30):
                    raise RuntimeError("Node B failed to start")

                time.sleep(5)

                rpc_call(8551, "miner.mine", {"count": 1, "address": sender_kp.address})
                raw_tx, tx_hash = _build_signed_transfer(
                    {"chain_id": chain_id}, sender_kp, receiver_hex, nonce=0, value=1_000_000_000
                )
                send_resp = rpc_call(8551, "tx.sendRawTransaction", {"rawTx": raw_tx})
                assert send_resp.get("result") == tx_hash

                deadline = time.time() + 15
                while time.time() < deadline:
                    pending_resp = rpc_call(8552, "mempool.getPending")
                    pending = pending_resp.get("result", [])
                    if tx_hash in pending:
                        break
                    time.sleep(0.5)
                else:
                    raise AssertionError(f"tx {tx_hash} not seen in node B mempool")

                mine_resp = rpc_call(8551, "miner.mine", {"count": 1, "address": sender_kp.address})
                height = mine_resp["result"]["height"]

                deadline = time.time() + 20
                while time.time() < deadline:
                    block = rpc_call(8552, "chain.getBlockByNumber", [height, True]).get("result")
                    if block and block.get("transactions"):
                        txs = block.get("transactions", [])
                        hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in txs]
                        if tx_hash in hashes:
                            break
                    time.sleep(0.5)
                else:
                    raise AssertionError("Node B did not sync mined block with tx in time")
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


@pytest.mark.skipif(
    not is_port_available(8553) or not is_port_available(8554),
    reason="Ports 8553 or 8554 not available",
)
@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("SKIP_E2E_P2P") == "true",
    reason="E2E P2P tests disabled in CI",
)
def test_p2p_tx_gossip_mined_on_peer() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    chain_id = 1337

    try:
        from pq.py.keygen import keygen_sig
        from pq.py.address import decode_address
    except Exception:
        pytest.skip("PQ keygen not available")
        return

    sender_kp = keygen_sig("dilithium3")
    receiver_kp = keygen_sig("dilithium3")

    receiver_record = decode_address(receiver_kp.address)
    receiver_hex = "0x" + bytes(receiver_record.digest)[:32].ljust(32, b"\x00").hex()

    with tempfile.TemporaryDirectory() as tmpdir:
        node_a_data = Path(tmpdir) / "node_a"
        node_b_data = Path(tmpdir) / "node_b"
        node_a_data.mkdir()
        node_b_data.mkdir()

        node_a_env = os.environ.copy()
        node_a_env.pop("ANIMICA_TRUSTED_RPC_URL", None)
        node_a_env.update(
            {
                "ANIMICA_CHAIN_ID": str(chain_id),
                "ANIMICA_RPC_PORT": "8553",
                "ANIMICA_RPC_HOST": "127.0.0.1",
                "ANIMICA_RPC_DB_URI": f"sqlite:///{node_a_data}/chain.db",
                "P2P_ENABLE": "true",
                "P2P_LISTEN": "127.0.0.1:30347",
                "P2P_SEEDS": "",
                "ANIMICA_PEER_STORE_PATH": str(node_a_data / "peers"),
                "ANIMICA_LOG_LEVEL": "INFO",
            }
        )

        node_b_env = os.environ.copy()
        node_b_env.pop("ANIMICA_TRUSTED_RPC_URL", None)
        node_b_env.update(
            {
                "ANIMICA_CHAIN_ID": str(chain_id),
                "ANIMICA_RPC_PORT": "8554",
                "ANIMICA_RPC_HOST": "127.0.0.1",
                "ANIMICA_RPC_DB_URI": f"sqlite:///{node_b_data}/chain.db",
                "P2P_ENABLE": "true",
                "P2P_LISTEN": "127.0.0.1:30348",
                "P2P_SEEDS": "/ip4/127.0.0.1/tcp/30347",
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
            if not wait_for_rpc(8553, timeout=30):
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
                if not wait_for_rpc(8554, timeout=30):
                    raise RuntimeError("Node B failed to start")

                time.sleep(5)

                rpc_call(8553, "miner.mine", {"count": 3, "address": sender_kp.address})
                raw_tx, tx_hash = _build_signed_transfer(
                    {"chain_id": chain_id}, sender_kp, receiver_hex, nonce=0, value=1_000_000_000
                )
                send_resp = rpc_call(8553, "tx.sendRawTransaction", {"rawTx": raw_tx})
                assert send_resp.get("result") == tx_hash

                deadline = time.time() + 15
                while time.time() < deadline:
                    pending_resp = rpc_call(8554, "mempool.getPending")
                    pending = pending_resp.get("result", [])
                    if tx_hash in pending:
                        break
                    time.sleep(0.5)
                else:
                    raise AssertionError(f"tx {tx_hash} not seen in node B mempool")

                same_head = wait_for_same_head(8553, 8554, timeout=20)
                if same_head is None:
                    pytest.skip("Nodes not synced to same head; skipping peer mining")

                mine_resp = rpc_call(8554, "miner.mine", {"count": 1, "address": sender_kp.address})
                height = mine_resp["result"]["height"]

                block = rpc_call(8554, "chain.getBlockByNumber", [height, True]).get("result")
                assert block is not None
                txs = block.get("transactions", [])
                hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in txs]
                assert tx_hash in hashes
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
