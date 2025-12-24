import asyncio

import pytest

from p2p.node.p2p_service import P2PService, _PeerState
from p2p.wire.encoding import encode_payload
from p2p.wire.frames import Framer
from p2p.wire.messages import Hello


@pytest.mark.asyncio
async def test_inbound_hello_stores_listen_port(tmp_path, monkeypatch):
    service = P2PService(
        listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
        seeds=[],
        chain_id=1337,
        peerstore_path=tmp_path / "peerstore",
    )
    monkeypatch.setattr(service, "_genesis_hash", lambda: b"\x00" * 32)
    monkeypatch.setattr(service, "_genesis_identity", lambda: b"\x00" * 32)
    monkeypatch.setattr(service, "_network_params_hash", lambda: b"\x11" * 32)

    async def _noop_send(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(service, "_send", _noop_send)

    def _noop_task(coro, **_kwargs):
        if asyncio.iscoroutine(coro):
            coro.close()

    monkeypatch.setattr(service, "_create_child_task", _noop_task)

    session = service.peer_registry.register("203.0.113.10:54321", "inbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote="203.0.113.10:54321",
        direction="inbound",
        conn=None,
        stream=None,
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )

    hello = Hello(
        chain_id=service.chain_id,
        listen_port=30333,
        peer_id=b"\x11" * 32,
        genesis_hash=service._genesis_hash(),
        genesis_identity=service._genesis_identity(),
        network_params_hash=service._network_params_hash(),
        head_hash=b"\x00" * 32,
    )
    payload = encode_payload(hello)

    await service._handle_hello(peer, payload)

    addrs = [addr for _, addr, _ in service.peerstore.list_addresses(limit=10)]
    assert "tcp://203.0.113.10:30333" in addrs
    assert not any(":54321" in addr for addr in addrs)
