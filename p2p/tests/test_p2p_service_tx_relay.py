import asyncio
import hashlib

import pytest

from p2p.node.p2p_service import P2PService
from p2p.tests import free_port, tcp_multiaddr, wait_for


class InMemoryTxPool:
    def __init__(self) -> None:
        self._txs: dict[bytes, bytes] = {}
        self.admit_count = 0
        self.duplicate_count = 0
        self.block_db = _MockBlockDB()

    async def admit_tx(self, raw: bytes) -> tuple[bool, str | None]:
        tx_hash = hashlib.sha3_256(raw).digest()
        if tx_hash in self._txs:
            self.duplicate_count += 1
            return True, "duplicate"
        self._txs[tx_hash] = raw
        self.admit_count += 1
        return True, None

    async def get_tx_raw(self, tx_hash: bytes) -> bytes | None:
        return self._txs.get(tx_hash)

    async def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        return list(self._txs.keys())[:limit]

    def has_tx(self, tx_hash: bytes) -> bool:
        return tx_hash in self._txs


class _MockBlockDB:
    def __init__(self) -> None:
        self._genesis = b"\x11" * 32

    def get_genesis_hash(self) -> bytes:
        return self._genesis

    def get_canonical_hash(self, height: int) -> bytes | None:
        if height == 0:
            return self._genesis
        return None


@pytest.mark.asyncio
async def test_p2p_service_tx_relay_between_nodes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    port_a = free_port()
    port_b = free_port()

    deps_a = InMemoryTxPool()
    deps_b = InMemoryTxPool()

    svc_a = P2PService(
        listen_addrs=[tcp_multiaddr(port_a)],
        seeds=[],
        chain_id=1337,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node-a" / "p2p"),
    )
    svc_b = P2PService(
        listen_addrs=[tcp_multiaddr(port_b)],
        seeds=[],
        chain_id=1337,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node-b" / "p2p"),
    )

    await svc_a.start()
    await svc_b.start()
    try:
        await svc_b.dial(tcp_multiaddr(port_a))
        connected = await wait_for(
            lambda: svc_a.status_snapshot().peers_total >= 1
            and svc_b.status_snapshot().peers_total >= 1,
            timeout=10.0,
        )
        assert connected

        raw_tx = b"tx-relay-test"
        tx_hash = hashlib.sha3_256(raw_tx).digest()
        await svc_a.relay_tx(raw_tx)

        relayed = await wait_for(lambda: deps_b.has_tx(tx_hash), timeout=5.0)
        assert relayed
    finally:
        await svc_b.stop()
        await svc_a.stop()


@pytest.mark.asyncio
async def test_p2p_service_tx_relay_no_loop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    port_a = free_port()
    port_b = free_port()

    deps_a = InMemoryTxPool()
    deps_b = InMemoryTxPool()

    svc_a = P2PService(
        listen_addrs=[tcp_multiaddr(port_a)],
        seeds=[],
        chain_id=1337,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node-a" / "p2p"),
    )
    svc_b = P2PService(
        listen_addrs=[tcp_multiaddr(port_b)],
        seeds=[],
        chain_id=1337,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node-b" / "p2p"),
    )

    await svc_a.start()
    await svc_b.start()
    try:
        await svc_b.dial(tcp_multiaddr(port_a))
        connected = await wait_for(
            lambda: svc_a.status_snapshot().peers_total >= 1
            and svc_b.status_snapshot().peers_total >= 1,
            timeout=10.0,
        )
        assert connected

        raw_tx = b"tx-relay-loop-test"
        await svc_a.relay_tx(raw_tx)
        await wait_for(
            lambda: deps_b.has_tx(hashlib.sha3_256(raw_tx).digest()), timeout=5.0
        )

        await asyncio.sleep(0.2)
        assert deps_a.admit_count == 1
        assert deps_a.duplicate_count == 0
    finally:
        await svc_b.stop()
        await svc_a.stop()
