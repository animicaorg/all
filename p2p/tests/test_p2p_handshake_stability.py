from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from p2p.deps import P2PDeps
from p2p.node.p2p_service_legacy import P2PService
from p2p.tests import free_port, tcp_multiaddr, wait_for


GENESIS_TEMPLATE = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_devnet_genesis(tmp_path: Path) -> Path:
    genesis_path = tmp_path / "genesis.devnet.json"
    if genesis_path.exists():
        return genesis_path
    base_genesis = json.loads(GENESIS_TEMPLATE.read_text(encoding="utf-8"))
    base_genesis["chainId"] = 1337
    base_genesis["network"] = "animica-devnet"
    consensus = base_genesis.get("consensus") or {}
    consensus["initialThetaMicro"] = 1
    base_genesis["consensus"] = consensus
    params_ref = base_genesis.get("paramsRef") or {}
    params_ref["path"] = str(
        Path(__file__).resolve().parents[2] / "spec" / "params.yaml"
    )
    base_genesis["paramsRef"] = params_ref
    genesis_path.write_text(json.dumps(base_genesis, indent=2), encoding="utf-8")
    return genesis_path


def _make_deps(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    genesis_path = _make_devnet_genesis(tmp_path)
    return P2PDeps.open(f"sqlite:///{db_path}", str(genesis_path))


@pytest.mark.asyncio
async def test_two_node_handshake_stays_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    port_a = free_port()
    port_b = free_port()

    deps_a = _make_deps(tmp_path, "node-a")
    deps_b = _make_deps(tmp_path, "node-b")

    node_a = P2PService(
        listen_addrs=[tcp_multiaddr(port_a)],
        seeds=[],
        chain_id=deps_a.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node-a" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[tcp_multiaddr(port_b)],
        seeds=[tcp_multiaddr(port_a)],
        chain_id=deps_b.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node-b" / "p2p"),
    )

    await node_a.start()
    await node_b.start()

    try:
        async def _outbound_connected() -> bool:
            return any(
                p.get("direction") == "outbound"
                and p.get("state") == "CONNECTED"
                and p.get("identity_ok")
                for p in node_b.peer_registry.snapshot()
            )

        connected = await wait_for(_outbound_connected, timeout=15.0, interval=0.2)
        assert connected, "Outbound handshake did not complete in time"

        await asyncio.sleep(30.0)

        still_connected = any(
            p.get("direction") == "outbound"
            and p.get("state") == "CONNECTED"
            and p.get("identity_ok")
            for p in node_b.peer_registry.snapshot()
        )
        assert still_connected, "Outbound connection dropped after handshake"
    finally:
        await node_b.stop()
        await node_a.stop()
