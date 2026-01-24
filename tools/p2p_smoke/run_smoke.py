from __future__ import annotations

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass

from p2p.node.p2p_service_legacy import P2PService
from p2p.tests import free_port, tcp_multiaddr


@dataclass
class FakeBlockDB:
    genesis_hash: bytes
    height: int = 0
    head_hash: bytes = b"\x00" * 32

    def get_genesis_hash(self) -> bytes:
        return self.genesis_hash

    def get_canonical_hash(self, height: int) -> bytes | None:
        if height == 0:
            return self.genesis_hash
        if height == self.height:
            return self.head_hash
        return None

    def get_canonical_head(self):
        return (self.height, self.head_hash)

    def get_head(self):
        return (self.height, self.head_hash)

    def get_canonical_height(self) -> int:
        return self.height

    def set_head(self, height: int, head_hash: bytes) -> None:
        self.height = int(height)
        self.head_hash = bytes(head_hash)


@dataclass
class FakeDeps:
    block_db: FakeBlockDB
    expected_genesis_hash: bytes
    protocol_version: str = "1.0"
    consensus_id: str = "poies/test"
    fork_id: int = 1

    def head(self):
        return self.block_db.get_head()


async def _wait_for_connected(service: P2PService, *, timeout_s: float = 30.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        snap = service.status_snapshot()
        if snap.peers_connected >= 1:
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("Handshake did not complete within timeout")


async def _wait_for_tip_height(service: P2PService, *, height: int, timeout_s: float = 30.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        best_height, _hash, _peer, _age = service._tip_manager.get_best_tip()
        if best_height is not None and best_height >= height:
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("Tip height did not update within timeout")


async def main() -> None:
    os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    port_a = free_port()
    port_b = free_port()

    genesis = b"\x11" * 32
    deps_a = FakeDeps(block_db=FakeBlockDB(genesis_hash=genesis), expected_genesis_hash=genesis)
    deps_b = FakeDeps(block_db=FakeBlockDB(genesis_hash=genesis), expected_genesis_hash=genesis)

    with tempfile.TemporaryDirectory() as tmp:
        service_a = P2PService(
            listen_addrs=[tcp_multiaddr(port_a)],
            seeds=[f"127.0.0.1:{port_b}"],
            chain_id=1,
            deps=deps_a,
            peerstore_path=os.path.join(tmp, "node-a"),
        )
        service_b = P2PService(
            listen_addrs=[tcp_multiaddr(port_b)],
            seeds=[f"127.0.0.1:{port_a}"],
            chain_id=1,
            deps=deps_b,
            peerstore_path=os.path.join(tmp, "node-b"),
        )

        await service_a.start()
        await service_b.start()
        try:
            await _wait_for_connected(service_a)
            await _wait_for_connected(service_b)

            await asyncio.sleep(30.0)

            # Simulate mining a block on A and propagate head status
            new_hash = os.urandom(32)
            deps_a.block_db.set_head(1, new_hash)
            await service_a._propagate_network_height_update(1)

            await _wait_for_tip_height(service_b, height=1)
            print("✅ smoke passed: handshake stable, tip propagated to height 1")
        finally:
            await service_a.stop()
            await service_b.stop()


if __name__ == "__main__":
    asyncio.run(main())
