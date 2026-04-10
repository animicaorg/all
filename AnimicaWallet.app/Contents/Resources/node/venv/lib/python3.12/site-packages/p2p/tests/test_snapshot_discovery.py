from __future__ import annotations

import asyncio

import pytest

from p2p.sync.snapshot_sync import _query_peers_for_snapshots


class _DummyPeer:
    def __init__(self, remote: str) -> None:
        self.remote = remote
        self.hello_done = asyncio.Event()
        self.hello_done.set()


class _DummyService:
    def __init__(self) -> None:
        self._peers = {
            "peer-a": _DummyPeer("peer-a"),
            "peer-b": _DummyPeer("peer-b"),
        }

    async def query_peer_snapshots(self, peer, chain_id: int, timeout: float = 10.0):
        if peer.remote == "peer-a":
            return [
                {
                    "chain_id": chain_id,
                    "checkpoint_height": 1200,
                    "checkpoint_hash": "0xabc",
                    "blocks_count": 1200,
                    "accounts_count": 42,
                    "size_mb": 12.5,
                    "timestamp": 123456,
                    "created_at": "2024-01-01T00:00:00Z",
                    "manifest_hash": "0xdead",
                }
            ]
        return []


@pytest.mark.asyncio
async def test_query_peers_for_snapshots_reports_inventory() -> None:
    service = _DummyService()

    snapshots, status = await _query_peers_for_snapshots(
        service, chain_id=1, include_status=True
    )

    assert "peer:peer-a" in snapshots
    assert snapshots["peer:peer-a"][0]["checkpoint_height"] == 1200
    assert status["responded"] == 1
    assert "peer-b" in status["empty"]
