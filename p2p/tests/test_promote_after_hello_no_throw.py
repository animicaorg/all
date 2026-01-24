from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from p2p.node.peer_registry import PeerRegistry
from p2p.node.p2p_service_legacy import P2PService, _PeerState
from p2p.node.runtime import P2PRuntime


@pytest.mark.asyncio
async def test_promote_peer_after_hello_no_throw() -> None:
    registry = PeerRegistry()

    service = P2PService.__new__(P2PService)
    service._peer_registry = registry
    service.peerstore = MagicMock()
    service._runtime = P2PRuntime(registry, peerstore=service.peerstore)
    service._sync_verbose = False
    service._sync_wakeup = asyncio.Event()
    service._txrelay = MagicMock()
    service._txrelay.request_mempool_sync = AsyncMock()
    service._txrelay.register_peer = MagicMock()
    service._peer_tx_key = lambda peer: peer.session_id
    service._create_child_task = lambda coro, name=None: None
    service._normalize_peer_addr = MagicMock(return_value=SimpleNamespace(addr=None))
    service._sanitize_peer_addr = MagicMock(return_value=None)
    service._allow_self_peers = True
    service._is_self_address = MagicMock(return_value=False)
    service._addrman = MagicMock()
    service._canon_hash0x = lambda value: "0x" + ("00" * 32) if value is not None else None
    service._update_peer_meta = MagicMock()
    service._schedule_peer_persist = MagicMock()
    service._send = AsyncMock()
    service._announce_pending_txs = AsyncMock()
    service._maybe_announce_headers_on_hello = AsyncMock()
    service._send_addr_sample = AsyncMock()
    service._send_peer_exchange = AsyncMock()
    service._send_get_peers = AsyncMock()
    service._tip_manager = MagicMock()
    service._tip_manager.on_handshake_complete = MagicMock(return_value=True)
    service._request_peer_head_status = AsyncMock(return_value=True)
    service._request_headers_on_outbound_hello = AsyncMock()
    service._close_feeler_after_delay = AsyncMock()
    service._addr_relay_sample = 10
    service._peer_exchange_limit = 10
    service._peers_by_session = {}
    service._stats = {"peers": 0}
    service._update_peer_head_table = MagicMock()

    session = registry.register("tcp://peer:30333", "outbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote=session.remote,
        direction=session.direction,
        conn=MagicMock(),
        stream=MagicMock(),
        framer=MagicMock(),
        write_lock=asyncio.Lock(),
    )
    peer.peer_id = "peer1"
    service._peers_by_session[peer.session_id] = peer

    normalized = {
        "head_height": 0,
        "head_hash": b"",
        "chain_id": 1,
        "network_magic": b"",
        "genesis_hash": b"",
        "genesis_identity": b"",
        "fork_id": 0,
        "consensus_id": "test",
        "protocol_version": "1",
        "network_params_hash": b"",
        "network_name": "local",
    }
    handshake = SimpleNamespace(listen_addrs=[])

    await service._promote_peer_after_hello(peer, handshake, normalized)
