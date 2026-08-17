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
from mining.stratum_server import Session, StratumServer  # noqa: F401


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
        # The keepalive interval and the reap deadline are both ~1s here, so a
        # difficulty push can already be buffered when the reaper closes. What
        # matters is that the stream ENDS; drain whatever was in flight first.
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            if chunk == b"":
                break
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
async def test_unterminated_line_is_bounded(monkeypatch):
    # decode_lines only consumes complete "\n"-delimited envelopes, so a client
    # that streams forever without a newline grew the per-session buffer without
    # limit (~100MB in 17s measured) while looking active to the reaper.
    monkeypatch.setenv("ANIMICA_STRATUM_MAX_LINE_BYTES", "65536")
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")
    srv, port = await _start_server(keepalive_secs=0.0)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert await _wait_for(lambda: len(srv._sessions) == 1)
        blob = b"A" * 16384
        for _ in range(16):  # 256KB, no newline anywhere
            writer.write(blob)
            try:
                await asyncio.wait_for(writer.drain(), timeout=2.0)
            except Exception:
                break
        # Server must reject and close rather than buffer indefinitely. The
        # close may surface as the error line, a clean EOF, or an RST (unread
        # inbound data in the socket buffer makes Linux send RST on close).
        assert await _wait_for(lambda: len(srv._sessions) == 0, timeout=8.0)
        try:
            data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            assert b"line too long" in data or data == b""
        except (ConnectionResetError, BrokenPipeError):
            pass
    finally:
        writer.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_refusal_logging_is_throttled_and_counted(monkeypatch, caplog):
    # Refusals are attacker-paced; one log line each would let a connect flood
    # push out the share/job/payout lines that matter (shared journald bucket).
    monkeypatch.setenv("ANIMICA_STRATUM_MAX_SESSIONS_PER_IP", "2")
    monkeypatch.setenv("ANIMICA_STRATUM_REFUSE_LOG_INTERVAL", "3600")
    srv, port = await _start_server(keepalive_secs=0.0)
    conns = []
    try:
        for _ in range(2):
            conns.append(await asyncio.open_connection("127.0.0.1", port))
        assert await _wait_for(lambda: len(srv._sessions) == 2)
        with caplog.at_level("WARNING", logger="mining.stratum_server"):
            for _ in range(12):
                r, w = await asyncio.open_connection("127.0.0.1", port)
                conns.append((r, w))
                await asyncio.wait_for(r.read(), timeout=5.0)
        refuse_lines = [
            rec for rec in caplog.records if "refusing connections" in rec.getMessage()
        ]
        assert len(refuse_lines) == 1, f"expected 1 throttled log, got {len(refuse_lines)}"
        stats = srv.stats()
        assert stats["refused_connections"] == 12
        assert stats["capped_ips"] == 1
    finally:
        for _, w in conns:
            w.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_authorized_without_shares_is_not_a_miner(monkeypatch):
    # mining.authorize accepts any plausible-looking address with no proof, so
    # counting merely-authorized sessions lets an abuser re-inflate the miner
    # count with fabricated addresses. Only work counts.
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")
    srv, port = await _start_server(keepalive_secs=0.0)
    conns = []
    try:
        for i in range(5):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            conns.append((reader, writer))
            writer.write(_authorize_line(f"anim1fabricated{i}"))
            await writer.drain()
        assert await _wait_for(
            lambda: sum(1 for s in srv._sessions.values() if s.authorized) == 5
        )
        metrics = PoolMetrics(PoolConfig(db_url=""), DummyJobManager(), srv)
        # Five authorized addresses, zero accepted shares -> zero miners.
        assert metrics._active_miner_count(900.0) == 0
        # Credit one session with a share; only that one becomes a miner.
        next(iter(srv._sessions.values())).shares_accepted = 1
        assert metrics._active_miner_count(900.0) == 1
    finally:
        for _, w in conns:
            w.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_broadcast_deadline_does_not_wait_for_wedged_peer(monkeypatch):
    # A non-reading peer must not hold job publication (and therefore the
    # template loop / block production) for the full 60s send timeout.
    monkeypatch.setenv("ANIMICA_STRATUM_BROADCAST_DEADLINE", "1")
    monkeypatch.setenv("ANIMICA_STRATUM_SEND_TIMEOUT", "60")
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")
    srv, port = await _start_server(keepalive_secs=0.0)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert await _wait_for(lambda: len(srv._sessions) == 1)
        session = next(iter(srv._sessions.values()))

        # Simulate a wedged peer: drain() never returns.
        class WedgedWriter:
            def __init__(self, inner):
                self._inner = inner

            def write(self, data):
                return None

            async def drain(self):
                await asyncio.sleep(3600)

            def is_closing(self):
                return False

            def close(self):
                self._inner.close()

            async def wait_closed(self):
                return None

            def get_extra_info(self, *a, **k):
                return self._inner.get_extra_info(*a, **k)

        session.writer = WedgedWriter(session.writer)
        started = asyncio.get_event_loop().time()
        await srv._broadcast({"jsonrpc": "2.0", "method": "mining.ping"})
        elapsed = asyncio.get_event_loop().time() - started
        assert elapsed < 3.0, f"broadcast waited {elapsed:.1f}s on a wedged peer"
        # The wedged session is left undecided (not falsely dropped).
        assert len(srv._sessions) == 1
    finally:
        writer.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_active_miner_count_ignores_socket_pileup():
    job_manager = DummyJobManager()
    # 835 authorized, share-producing snapshots all on ONE address (the
    # 2026-08-06 shape) plus a second real miner's accepted share in the window
    # and one stale share.
    pileup = [
        {"authorized": True, "address": "anim1flooder", "shares_accepted": 3}
        for _ in range(835)
    ]
    pileup.append(
        {"authorized": False, "address": "anim1lurker", "shares_accepted": 9}
    )
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


@pytest.mark.asyncio
async def test_a_miner_between_blocks_is_still_counted():
    """THE DEFECT: num_miners required an accepted share inside 15 minutes.

    Only sessions that opted into sub-block shares get a target they hit often.
    Every other client mines the FULL block target, so "an accepted share" means
    "found a block" — at a small share of pool hashrate that is hours apart. Such a
    miner mines continuously and used to disappear from the count between blocks.
    """
    now = time.time()
    # Live authorized session, no share THIS session, last block found 40 min ago —
    # i.e. mining the whole time, just unlucky inside the display window.
    snaps = [{
        "authorized": True,
        "address": "anim1steadyminer",
        "shares_accepted": 0,
        "last_inbound": now - 5,
    }]
    metrics = PoolMetrics(
        PoolConfig(db_url=""), DummyJobManager(), StaticSessionServer(snaps)
    )
    metrics._share_events.append(
        {"timestamp": now - 2400, "status": "accepted", "address": "anim1steadyminer"}
    )
    assert metrics._active_miner_count(900.0) == 1, "a miner between blocks vanished"

    p = metrics._miner_presence(900.0)
    assert p["proven"] == 1 and p["unproven"] == 0


@pytest.mark.asyncio
async def test_fabricated_addresses_still_cannot_inflate_num_miners():
    """The 9.0.10 guard must survive the fix: mining.authorize proves nothing, so a
    live authorized session with NO work anywhere must not raise num_miners. It is
    reported under connected_miners instead, where its unverified status is visible.
    """
    now = time.time()
    snaps = [
        {"authorized": True, "address": f"anim1fabricated{i}",
         "shares_accepted": 0, "last_inbound": now - 1}
        for i in range(50)
    ]
    metrics = PoolMetrics(
        PoolConfig(db_url=""), DummyJobManager(), StaticSessionServer(snaps)
    )
    p = metrics._miner_presence(900.0)
    assert p["proven"] == 0, "fabricated addresses inflated the proven miner count"
    assert p["connected"] == 50 and p["unproven"] == 50


@pytest.mark.asyncio
async def test_idle_sockets_are_not_live_miners():
    """The 2026-08-06 shape: sockets that authorized then went silent. Liveness must
    come from last_inbound; last_seen is touched by our OWN keepalives every ~45s, so
    a session idle for an hour still has a fresh last_seen and must NOT count."""
    now = time.time()
    snaps = [{
        "authorized": True,
        "address": f"anim1idle{i}",
        "shares_accepted": 0,
        "last_inbound": now - 3600,   # silent for an hour
        "last_seen": now - 1,         # our keepalive touched it a second ago
    } for i in range(835)]
    metrics = PoolMetrics(
        PoolConfig(db_url=""), DummyJobManager(), StaticSessionServer(snaps)
    )
    p = metrics._miner_presence(900.0)
    assert p["live_sessions"] == 0, "last_seen was trusted over last_inbound"
    assert p["proven"] == 0 and p["connected"] == 0
