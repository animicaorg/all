from __future__ import annotations

import asyncio

import pytest

from p2p.node.p2p_service import P2PService, _PeerState
from p2p.wire.frames import Framer


class _NullConn:
    async def close(self) -> None:
        return None


class _NullStream:
    async def send(self, _data: bytes) -> None:
        return None

    async def recv(self) -> bytes:
        await asyncio.sleep(3600)
        return b""


@pytest.mark.asyncio
async def test_hello_timeout_grace_keeps_peer_during_active_sync(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path, monkeypatch

    node = object.__new__(P2PService)
    node._peer_registry = type("R", (), {"handshake_timeout_s": 0.05})()
    node._hello_timeout_grace_s = 0.2
    node._hello_timeout_grace_used = set()
    node._sync_target_height = 3
    node._network_best_height = lambda: 3  # type: ignore[method-assign]
    node._peer_key = lambda remote, direction: (remote, direction)  # type: ignore[method-assign]

    dropped: list[str] = []

    async def _capture_drop(peer: _PeerState, *, reason: str) -> None:
        dropped.append(reason)

    node._drop_peer = _capture_drop  # type: ignore[assignment]

    peer = _PeerState(
        session_id="session-1",
        remote="127.0.0.1:30333",
        direction="outbound",
        conn=_NullConn(),
        stream=_NullStream(),
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )
    task = asyncio.create_task(node._enforce_handshake_timeout(peer))
    await asyncio.sleep(0.08)
    assert dropped == []
    peer.hello_done.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert dropped == []
