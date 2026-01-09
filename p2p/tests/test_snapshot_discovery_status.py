import asyncio
from types import SimpleNamespace

import pytest

from p2p.sync.snapshot_sync import _query_peers_for_snapshots


@pytest.mark.asyncio
async def test_snapshot_discovery_counts_empty_responses() -> None:
    peer = SimpleNamespace(remote="peer-1", hello_done=asyncio.Event())
    peer.hello_done.set()

    class _Service:
        def __init__(self) -> None:
            self._peers = {"peer-1": peer}

        async def query_peer_snapshots(self, peer, chain_id, timeout):
            return []

    service = _Service()
    snapshots, status = await _query_peers_for_snapshots(
        service, chain_id=1, include_status=True
    )

    assert snapshots == {}
    assert status["total_peers"] == 1
    assert status["responded"] == 1
    assert status["empty"] == ["peer-1"]
