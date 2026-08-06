"""Session abuse guards: per-IP connection cap, idle-session reaper, and the
miner-count stats that must not be inflatable by one client holding many
sockets (2026-08-06: a single IP held 835 idle solo-port sessions and the
pool advertised "838 miners")."""

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.metrics import PoolMetrics
from mining.stratum_server import Session, StratumServer


class DummyJobManager:
    def __init__(self) -> None:
        self.current = None

    def current_job(self):
        return self.current

    def request_refresh(self) -> None:
        pass


class StaticSessionServer:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)

    def stats(self):
        return {}

    def session_snapshots(self):
        return list(self._snapshots)


async def _start_server(**kwargs) -> tuple[StratumServer, int]:
    srv = StratumServer(host="127.0.0.1", port=0, **kwargs)
    await srv.start()
    port = srv._server.sockets[0].getsockname()[1]
    return srv, port


async def _wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def _authorize_line(identity: str) -> bytes:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "mining.authorize",
                "params": [identity, "x"],
            }
        )
        + "\n"
    ).encode()


@pytest.mark.asyncio
async def test_per_ip_session_cap_refuses_excess_connections(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_MAX_SESSIONS_PER_IP", "3")
    srv, port = await _start_server(keepalive_secs=0.0)
    conns = []
    try:
        for _ in range(3):
            conns.append(await asyncio.open_connection("127.0.0.1", port))
        assert await _wait_for(lambda: len(srv._sessions) == 3)

        # Fourth connection from the same IP: refused with an error line + EOF.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        conns.append((reader, writer))
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        assert b"too many connections" in line
        eof = await asyncio.wait_for(reader.read(), timeout=5.0)
        assert eof == b""
        assert len(srv._sessions) == 3

        # Freeing a slot lets the same IP connect again.
        conns[0][1].close()
        await conns[0][1].wait_closed()
        assert await _wait_for(lambda: len(srv._sessions) == 2)
        reader5, writer5 = await asyncio.open_connection("127.0.0.1", port)
        conns.append((reader5, writer5))
        assert await _wait_for(lambda: len(srv._sessions) == 3)
    finally:
        for _, w in conns:
            w.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_cap_disabled_with_zero(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_MAX_SESSIONS_PER_IP", "0")
    srv, port = await _start_server(keepalive_secs=0.0)
    conns = []
    try:
        for _ in range(5):
            conns.append(await asyncio.open_connection("127.0.0.1", port))
        assert await _wait_for(lambda: len(srv._sessions) == 5)
    finally:
        for _, w in conns:
            w.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_idle_sessions_are_reaped(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "1")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "1")
    srv, port = await _start_server(keepalive_secs=0.5)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert await _wait_for(lambda: len(srv._sessions) == 1)
        # Silent client: no subscribe, no authorize, nothing. Must be reaped.
        assert await _wait_for(lambda: len(srv._sessions) == 0, timeout=6.0)
        eof = await asyncio.wait_for(reader.read(), timeout=5.0)
        assert eof == b""
    finally:
        writer.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_authorized_idle_session_reaped_despite_keepalives(monkeypatch):
    # The 2026-08-06 attacker shape: authorize once, then hold the socket in
    # silence while ACKing our keepalives. The outbound keepalive push calls
    # session.touch() (resets last_seen) every interval, so a reaper keyed on
    # last_seen would NEVER fire — this pins the reaper to last_inbound.
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "2")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "2")
    srv, port = await _start_server(keepalive_secs=0.5)  # keepalives every ~1s
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(_authorize_line("anim1parkedminer"))
        await writer.drain()
        assert await _wait_for(
            lambda: any(s.authorized for s in srv._sessions.values())
        )
        # Keep READING (so keepalive pushes succeed and keep touching
        # last_seen) but send nothing further.
        async def drain_forever():
            with pytest.raises(Exception):
                while True:
                    if not await reader.read(4096):
                        raise ConnectionResetError
        drainer = asyncio.create_task(drain_forever())
        assert await _wait_for(lambda: len(srv._sessions) == 0, timeout=8.0)
        drainer.cancel()
    finally:
        writer.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_active_session_survives_reaper(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "2")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "2")
    srv, port = await _start_server(keepalive_secs=0.5)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert await _wait_for(lambda: len(srv._sessions) == 1)
        writer.write(_authorize_line("anim1chattyminer.rig1"))
        await writer.drain()
        # Keep talking (any inbound message resets last_seen) for > timeout.
        for _ in range(4):
            await asyncio.sleep(1.0)
            writer.write(_authorize_line("anim1chattyminer.rig1"))
            await writer.drain()
        assert len(srv._sessions) == 1
    finally:
        writer.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_unique_miners_stat_dedupes_addresses(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_MAX_SESSIONS_PER_IP", "64")
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    srv, port = await _start_server(keepalive_secs=0.0)
    conns = []
    try:
        # Three sockets, but only two distinct addresses — and one silent
        # (unauthorized) socket that must count as a client, never a miner.
        for identity in (
            "anim1sameaddr.rig1",
            "anim1sameaddr.rig2",
            "anim1otheraddr",
        ):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            conns.append((reader, writer))
            writer.write(_authorize_line(identity))
            await writer.drain()
        silent = await asyncio.open_connection("127.0.0.1", port)
        conns.append(silent)

        assert await _wait_for(
            lambda: len(srv._sessions) == 4
            and sum(1 for s in srv._sessions.values() if s.authorized) == 3
        )
        stats = srv.stats()
        assert stats["clients"] == 4
        assert stats["authorized_sessions"] == 3
        assert stats["unique_miners"] == 2
    finally:
        for _, w in conns:
            w.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_active_miner_count_ignores_socket_pileup():
    job_manager = DummyJobManager()
    # 835 authorized snapshots, all one address (the 2026-08-06 shape) plus a
    # second real miner's accepted share in the window and one stale share.
    pileup = [
        {"authorized": True, "address": "anim1flooder"} for _ in range(835)
    ]
    pileup.append({"authorized": False, "address": "anim1lurker"})
    metrics = PoolMetrics(
        PoolConfig(db_url=""), job_manager, StaticSessionServer(pileup)
    )
    now = time.time()
    metrics._share_events.append(
        {"timestamp": now - 30, "status": "accepted", "address": "anim1sharer"}
    )
    metrics._share_events.append(
        {"timestamp": now - 30, "status": "rejected", "address": "anim1rejected"}
    )
    metrics._share_events.append(
        {"timestamp": now - 3600, "status": "accepted", "address": "anim1stale"}
    )
    assert metrics._active_miner_count(900.0) == 2
