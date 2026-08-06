"""Sub-block shares (9.1.0): PPS must pay miners who never find a block.

Before this, the wire share target was pinned at 1.0 == θ == the block target
(the xmrig-compat floor), so the only submittable hash WAS a block and PPS
degenerated into finder-takes-all. Sub-block targets are now handed out, but
ONLY to sessions that explicitly opt in on mining.subscribe — a client that
mishandles an unexpected set_difficulty drops its connection and stops mining,
which cost ~2h of mainnet block production on 2026-07-10.
"""

import asyncio
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.utils.pow import MICRO
from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.job_manager import JobManager
from animica.stratum_pool.stratum_server import StratumPoolServer
from mining.stratum_server import (Session, StratumJob, StratumServer,
                                   _parse_subblock_optin)
from mining.stratum_client import _parse_set_difficulty

THETA = 25_600_000  # live-ish mainnet θµ


async def _wait_for(predicate, timeout=5.0, interval=0.02):
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


class DummyAdapter:
    pass


def _pool(**kw) -> StratumPoolServer:
    # The feature ships default-OFF (operators opt in), so policy tests must
    # enable it explicitly.
    kw.setdefault("subblock_shares_enabled", True)
    cfg = PoolConfig(**kw)
    return StratumPoolServer(DummyAdapter(), cfg, JobManager(DummyAdapter(), cfg))


# --------------------------------------------------------------------------
# ratio policy math
# --------------------------------------------------------------------------


def test_ratio_makes_share_worth_one_over_s():
    for shares_per_block in (2, 16, 64, 1000):
        pool = _pool(shares_per_block=shares_per_block, subblock_min_ratio=0.0)
        ratio = pool.subblock_ratio_for_theta(THETA)
        assert ratio is not None
        # credit_fraction = P(share also solves a block) = exp(-θ(1-r)/MICRO)
        credit_fraction = math.exp(-THETA * (1.0 - ratio) / MICRO)
        assert credit_fraction == pytest.approx(1.0 / shares_per_block, rel=1e-9)


def test_share_rate_is_shares_per_block_over_block_time():
    # rate = H·p_share = H·p_block·S = S/T_block, independent of hashrate. This
    # is what bounds the flood, so pin it.
    shares_per_block = 64
    pool = _pool(shares_per_block=shares_per_block)
    ratio = pool.subblock_ratio_for_theta(THETA)
    p_share = math.exp(-THETA * ratio / MICRO)
    p_block = math.exp(-THETA / MICRO)
    for hashrate in (1e6, 38e6, 5e9):
        block_time = 1.0 / (hashrate * p_block)
        assert hashrate * p_share == pytest.approx(
            shares_per_block / block_time, rel=1e-6
        )


def test_policy_disabled_returns_none():
    assert _pool(subblock_shares_enabled=False).subblock_ratio_for_theta(THETA) is None


def test_feature_default_on_with_working_kill_switch():
    # On by default as of 9.2.0, but one env var must still take it out.
    assert PoolConfig().subblock_shares_enabled is True
    assert (
        StratumPoolServer(
            DummyAdapter(), PoolConfig(), JobManager(DummyAdapter(), PoolConfig())
        ).subblock_ratio_for_theta(THETA)
        is not None
    )
    assert _pool(subblock_shares_enabled=False).subblock_ratio_for_theta(THETA) is None


def test_policy_refuses_when_theta_too_small_for_s():
    # θ too small to carve S shares without crossing the ratio floor: the
    # feature must switch OFF for that job, never clamp into a flood.
    pool = _pool(shares_per_block=1000, subblock_min_ratio=0.5)
    assert pool.subblock_ratio_for_theta(1_000_000) is None
    assert pool.subblock_ratio_for_theta(0) is None
    assert pool.subblock_ratio_for_theta(-5) is None


def test_ratio_never_reaches_or_exceeds_one():
    pool = _pool(shares_per_block=64, subblock_min_ratio=0.0)
    for theta in (5_000_000, 25_600_000, 10**9):
        ratio = pool.subblock_ratio_for_theta(theta)
        if ratio is not None:
            assert 0.0 < ratio < 1.0


def test_config_clamps_hostile_values():
    assert PoolConfig().shares_per_block == 64
    from animica.stratum_pool.config import load_config_from_env

    cfg = load_config_from_env(
        overrides={
            "shares_per_block": 1,
            "rpc_url": "http://x",
            "pool_address": "anim1x",
        }
    )
    assert cfg.shares_per_block >= 2
    hostile = load_config_from_env(
        overrides={
            "shares_per_block": 10**9,
            "subblock_min_ratio": 5.0,
            "rpc_url": "http://x",
            "pool_address": "anim1x",
        }
    )
    assert hostile.shares_per_block <= 1024
    assert hostile.subblock_min_ratio <= 1.0


# --------------------------------------------------------------------------
# opt-in parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "features,expected",
    [
        ({}, False),
        ({"framing": "lines"}, False),
        (None, False),
        ("subblockShares", False),
        ({"subblockShares": {"version": 1}}, True),
        ({"subblockShares": True}, True),
        ({"subblockShares": 1}, True),
        ({"subblock_shares": "yes"}, True),
        ({"subShares": True}, True),
        ({"subblockShares": False}, False),
        ({"subblockShares": 0}, False),
        ({"subblockShares": "false"}, False),
        ({"subblockShares": {}}, False),
        ({"subblockShares": {"enabled": False}}, False),
    ],
)
def test_optin_parsing(features, expected):
    assert _parse_subblock_optin(features) is expected


# --------------------------------------------------------------------------
# per-session target application
# --------------------------------------------------------------------------


def _server_with_policy(ratio=0.8375) -> StratumServer:
    srv = StratumServer(host="127.0.0.1", port=0)
    srv.set_subblock_ratio_policy(lambda theta: ratio)
    return srv


def _session(**kw) -> Session:
    defaults = dict(session_id="s", writer=None, theta_micro=THETA, share_target=1.0)
    defaults.update(kw)
    return Session(**defaults)


def test_optin_session_gets_subblock_target():
    srv = _server_with_policy(0.8375)
    s = _session(supports_subblock=True, pool_mode="pps")
    assert srv._effective_share_target(s, 1.0, THETA) == pytest.approx(0.8375)


def test_non_optin_session_unchanged():
    srv = _server_with_policy(0.8375)
    s = _session(supports_subblock=False, pool_mode="pps")
    assert srv._effective_share_target(s, 1.0, THETA) == 1.0


def test_v1_session_never_gets_subblock_target_even_if_it_asks():
    # xmrig-style clients take a scalar wire difficulty that must stay >= the
    # compat floor; a sub-1.0 push puts them in a disconnect loop.
    srv = _server_with_policy(0.8375)
    s = _session(supports_subblock=True, is_v1=True, pool_mode="pps")
    assert srv._effective_share_target(s, 1.0, THETA) == 1.0


def test_solo_session_keeps_block_target():
    # Solo credit is block-only, so sub-block shares would earn nothing.
    srv = _server_with_policy(0.8375)
    s = _session(supports_subblock=True, pool_mode="solo")
    assert srv._effective_share_target(s, 1.0, THETA) == 1.0


def test_policy_can_only_make_target_easier():
    srv = _server_with_policy(0.95)
    s = _session(supports_subblock=True, pool_mode="pps")
    # Already easier than the policy: keep the easier one, never raise.
    assert srv._effective_share_target(s, 0.5, THETA) == 0.5


def test_absent_policy_is_inert():
    srv = StratumServer(host="127.0.0.1", port=0)
    s = _session(supports_subblock=True, pool_mode="pps")
    assert srv._effective_share_target(s, 1.0, THETA) == 1.0


def test_broken_policy_never_breaks_mining():
    srv = StratumServer(host="127.0.0.1", port=0)

    def boom(_theta):
        raise RuntimeError("policy exploded")

    srv.set_subblock_ratio_policy(boom)
    s = _session(supports_subblock=True, pool_mode="pps")
    assert srv._effective_share_target(s, 1.0, THETA) == 1.0


@pytest.mark.parametrize("bad", [0.0, -1.0, 1.5, float("nan")])
def test_policy_out_of_range_is_ignored(bad):
    srv = _server_with_policy(bad)
    s = _session(supports_subblock=True, pool_mode="pps")
    assert srv._effective_share_target(s, 1.0, THETA) == 1.0


# --------------------------------------------------------------------------
# end-to-end over a real socket
# --------------------------------------------------------------------------


async def _subscribe(port, features):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "mining.subscribe",
                    "params": {"agent": "test/1", "features": features},
                }
            )
            + "\n"
        ).encode()
    )
    await writer.drain()
    pushes = []
    for _ in range(3):
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=3.0)
        except asyncio.TimeoutError:
            break
        if not line:
            break
        pushes.append(json.loads(line))
    return reader, writer, pushes


@pytest.mark.asyncio
async def test_wire_target_differs_per_session_optin(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")
    srv = StratumServer(host="127.0.0.1", port=0, keepalive_secs=0.0)
    srv._default_share_target = 1.0
    srv._default_theta_micro = THETA
    srv.set_subblock_ratio_policy(lambda theta: 0.8375)
    await srv.start()
    port = srv._server.sockets[0].getsockname()[1]
    conns = []
    try:
        # Opted-in client sees a sub-block ratio on the wire.
        r1, w1, pushes1 = await _subscribe(port, {"subblockShares": {"version": 1}})
        conns += [(r1, w1)]
        diffs1 = [
            p for p in pushes1 if p.get("method") == "mining.set_difficulty"
        ]
        assert diffs1, f"no set_difficulty seen: {pushes1}"
        assert diffs1[0]["params"]["shareTarget"] == pytest.approx(0.8375)

        # Plain client still gets exactly the block target it always got.
        r2, w2, pushes2 = await _subscribe(port, {"framing": "lines"})
        conns += [(r2, w2)]
        diffs2 = [
            p for p in pushes2 if p.get("method") == "mining.set_difficulty"
        ]
        assert diffs2, f"no set_difficulty seen: {pushes2}"
        assert diffs2[0]["params"]["shareTarget"] == 1.0

        stats = srv.stats()
        assert stats["subblock_sessions"] == 1
    finally:
        for _, w in conns:
            w.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_global_difficulty_keeps_per_session_targets(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")
    srv = StratumServer(host="127.0.0.1", port=0, keepalive_secs=0.0)
    srv.set_subblock_ratio_policy(lambda theta: 0.8375)
    await srv.start()
    port = srv._server.sockets[0].getsockname()[1]
    conns = []
    try:
        r1, w1, _ = await _subscribe(port, {"subblockShares": True})
        r2, w2, _ = await _subscribe(port, {})
        conns += [(r1, w1), (r2, w2)]
        assert len(srv._sessions) == 2
        # A θ retarget must not overwrite the opted-in session's easier target.
        await srv.set_global_difficulty(1.0, THETA)
        targets = sorted(s.share_target for s in srv._sessions.values())
        assert targets[0] == pytest.approx(0.8375)
        assert targets[1] == 1.0
    finally:
        for _, w in conns:
            w.close()
        await srv.stop()


# --------------------------------------------------------------------------
# miner-side defensive parsing (the 2026-07-10 disconnect class)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"shareTarget": 0.8375, "thetaMicro": THETA}, (0.8375, THETA)),
        ({"share_target": 0.5, "theta_micro": 7}, (0.5, 7)),
        ({"shareRatio": 0.25}, (0.25, None)),
        ({"thetaMicro": THETA}, (None, THETA)),
        ({}, (None, None)),
        ([1.0], (None, None)),          # classic v1 list form
        (None, (None, None)),
        ("garbage", (None, None)),
        ({"shareTarget": "not-a-number"}, (None, None)),
        ({"shareTarget": 0}, (None, None)),
        ({"shareTarget": 5.0}, (1.0, None)),   # clamped, never > 1
        ({"thetaMicro": -3}, (None, None)),
    ],
)
def test_miner_set_difficulty_parser_never_raises(params, expected):
    assert _parse_set_difficulty(params) == expected


# --------------------------------------------------------------------------
# real client <-> real server over a socket (wire contract)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_miner_client_receives_and_uses_subblock_ratio(monkeypatch):
    """Drive the ACTUAL mining.stratum_client.StratumClient against the ACTUAL
    StratumServer. This is the contract that broke on 2026-07-10: the pool
    emitted a difficulty the miner mishandled, the miner tore down its
    connection during the handshake and mined nothing. Assert the miner ends up
    authorized, holding the sub-block ratio, and thresholding on it rather than
    on the block target."""
    monkeypatch.setenv("ANIMICA_AICF_DISABLE", "1")
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")
    from mining.stratum_client import StratumClient
    from core.utils.pow import micro_threshold_to_target256

    srv = StratumServer(host="127.0.0.1", port=0, keepalive_secs=0.0)
    srv._default_share_target = 1.0
    srv._default_theta_micro = THETA
    ratio = 0.8375
    srv.set_subblock_ratio_policy(lambda theta: ratio)
    await srv.start()
    port = srv._server.sockets[0].getsockname()[1]

    client = StratumClient(host="127.0.0.1", port=port)
    try:
        await client.connect()
        await client.subscribe()
        await client.authorize(worker="rig-1", address="anim1testminerxxxxxxxxxxxxxxxxxx")

        # The server saw the opt-in the real client advertises.
        session = next(iter(srv._sessions.values()))
        assert session.supports_subblock is True, "real client did not advertise opt-in"
        assert session.authorized is True, "handshake did not complete"

        # The client parsed the sub-block difficulty push (no exception, no drop).
        for _ in range(50):
            if client.share_target and client.share_target < 1.0:
                break
            await asyncio.sleep(0.05)
        assert client.share_target == pytest.approx(ratio), (
            f"client share_target={client.share_target} (expected {ratio})"
        )
        assert client.theta_micro == THETA

        # And it would mine at the EASIER threshold, not the block target.
        miner_target = micro_threshold_to_target256(
            max(1, int(client.theta_micro * client.share_target))
        )
        block_target = micro_threshold_to_target256(client.theta_micro)
        assert miner_target > block_target, "miner is still mining the block target"
    finally:
        await client.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_real_miner_client_survives_hostile_set_difficulty(monkeypatch):
    # The 2026-07-10 failure mode, directly: an unexpected set_difficulty shape
    # must not kill the connection or stop mining.
    monkeypatch.setenv("ANIMICA_AICF_DISABLE", "1")
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")
    from mining.stratum_client import StratumClient

    srv = StratumServer(host="127.0.0.1", port=0, keepalive_secs=0.0)
    srv._default_share_target = 1.0
    srv._default_theta_micro = THETA
    await srv.start()
    port = srv._server.sockets[0].getsockname()[1]

    client = StratumClient(host="127.0.0.1", port=port)
    try:
        await client.connect()
        await client.subscribe()
        await client.authorize(worker="rig-1", address="anim1testminerxxxxxxxxxxxxxxxxxx")
        session = next(iter(srv._sessions.values()))
        before = client.share_target

        # Classic v1 list form, then a renamed field, then junk.
        for hostile in (
            {"id": None, "method": "mining.set_difficulty", "params": [4.0]},
            {"id": None, "method": "mining.set_difficulty", "params": {"difficulty": 7}},
            {"id": None, "method": "mining.set_difficulty", "params": "nonsense"},
        ):
            await srv._send(session, hostile)
            await asyncio.sleep(0.15)

        assert client.share_target == before, "hostile push corrupted the ratio"
        assert len(srv._sessions) == 1, "miner dropped its connection"
        # Still alive: a well-formed push after the hostile ones still lands.
        await srv._push_difficulty(session, 0.9, THETA)
        for _ in range(40):
            if client.share_target == pytest.approx(0.9):
                break
            await asyncio.sleep(0.05)
        assert client.share_target == pytest.approx(0.9)
    finally:
        await client.close()
        await srv.stop()


# --------------------------------------------------------------------------
# every in-repo miner must ask for sub-block shares
# --------------------------------------------------------------------------


def test_all_inrepo_miners_advertise_subblock_optin():
    """`animica up` must earn per share without the operator configuring
    anything, and so must every other miner we ship. The opt-in lives in each
    miner's mining.subscribe features; a new miner that forgets it silently
    goes back to earning only on blocks it finds, which is exactly the bug
    9.1.0 exists to fix. Pin the invariant on the source."""
    repo = Path(__file__).resolve().parents[4]
    miners = [
        repo / "mining" / "stratum_client.py",                 # animica up / miner start
        repo / "python" / "animica" / "stratum_pool" / "reference_cpu_miner.py",
        repo / "python" / "animica" / "animica_cpu_miner_repoexact.py",
        repo / "tools" / "animica-opencl-miner" / "opencl_miner" / "stratum_client.py",
    ]
    missing = []
    for path in miners:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "subblockShares" not in text:
            missing.append(str(path.relative_to(repo)))
    assert not missing, (
        "these shipped miners never ask the pool for sub-block shares, so they "
        f"only earn when they find a whole block: {missing}"
    )


def test_up_reports_subblock_status():
    # The earnings shape differs enormously between per-share and per-block, so
    # `animica up` must say which one the operator is getting.
    repo = Path(__file__).resolve().parents[4]
    up = (repo / "python" / "animica" / "cli" / "up.py").read_text(encoding="utf-8")
    assert "_report_subblock" in up
    assert "per-share payouts" in up


# --------------------------------------------------------------------------
# the miner may not price its own shares
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_miner_cannot_inflate_credit_by_claiming_full_ratio(monkeypatch):
    """PPS credit is priced from exp(-θ(1-r)/MICRO) where r is the share ratio.
    If r came from the SUBMIT payload, an opted-in miner could mine the easy
    sub-block target (64x more shares) and stamp d_ratio=1.0 on each one to be
    paid a whole block reward per share — 64x theft, silent, because validation
    uses the session's real target. The pool must overwrite both d_ratio and
    shareTarget with the target it assigned."""
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")

    seen = {}

    class CapturingValidator:
        async def validate(self, job, submit_params):
            seen.update(submit_params)
            return True, None, False, 0

    srv = StratumServer(
        host="127.0.0.1", port=0, keepalive_secs=0.0, validator=CapturingValidator()
    )
    srv._default_share_target = 1.0
    srv._default_theta_micro = THETA
    srv.set_subblock_ratio_policy(lambda theta: 0.8375)
    await srv.start()
    port = srv._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "mining.subscribe",
                        "params": {
                            "agent": "greedy/1",
                            "features": {"subblockShares": True},
                        },
                    }
                )
                + "\n"
            ).encode()
        )
        writer.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "mining.authorize",
                        "params": {"worker": "greedy", "address": "anim1greedyminer"},
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        assert await _wait_for(
            lambda: any(
                s.authorized and s.share_target < 1.0 for s in srv._sessions.values()
            )
        )
        session = next(iter(srv._sessions.values()))
        job = StratumJob(
            job_id="job-greedy",
            header={"number": 7},
            share_target=1.0,
            theta_micro=THETA,
            raw={},
        )
        srv._jobs["job-greedy"] = job
        srv._current_job_id = "job-greedy"

        # The miner lies: easy target mined, full-block ratio claimed.
        writer.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "mining.submit",
                        "params": {
                            "worker": session.session_id,
                            "jobId": "job-greedy",
                            "extranonce2": "0x00",
                            "d_ratio": 1.0,
                            "shareTarget": 1.0,
                            "hashshare": {"nonce": "0x1", "body": {"hMicro": 1}},
                        },
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        assert await _wait_for(lambda: "d_ratio" in seen, timeout=5.0)

        # Both pricing inputs must equal the ASSIGNED target, not the claim.
        assert seen["d_ratio"] == pytest.approx(0.8375), seen["d_ratio"]
        assert seen["shareTarget"] == pytest.approx(0.8375), seen["shareTarget"]
        # And the reported difficulty is not spoofable either.
        assert session.current_difficulty == pytest.approx(0.8375)
    finally:
        writer.close()
        await srv.stop()


@pytest.mark.asyncio
async def test_duplicate_share_is_rejected_and_transient_failure_is_retryable(monkeypatch):
    """A sub-block share never reaches the node (stratum_pool/core.py returns
    early for `not is_block`), so nothing else dedupes it — one valid solution
    could be replayed for unlimited PPS credit. Block shares were always covered
    because the node reports duplicates. Dedup must be pool-wide, and must NOT
    burn a solution whose submit failed transiently (a transport timeout on a
    real block would otherwise be unrecoverable)."""
    monkeypatch.setenv("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "0")
    monkeypatch.setenv("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "0")

    outcome = {"ok": True}
    calls = {"n": 0}

    class ScriptedValidator:
        async def validate(self, job, submit_params):
            calls["n"] += 1
            return outcome["ok"], (None if outcome["ok"] else "transport_error:timeout"), False, 0

    srv = StratumServer(
        host="127.0.0.1", port=0, keepalive_secs=0.0, validator=ScriptedValidator()
    )
    srv._default_theta_micro = THETA
    await srv.start()
    port = srv._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    def submit(nonce, sid, req_id):
        return (
            json.dumps(
                {
                    "jsonrpc": "2.0", "id": req_id, "method": "mining.submit",
                    "params": {
                        "worker": sid, "jobId": "job-1", "extranonce2": "0x00",
                        "hashshare": {"nonce": nonce, "body": {"hMicro": 1}},
                    },
                }
            ) + "\n"
        ).encode()

    try:
        writer.write(
            (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "mining.subscribe",
                         "params": {"agent": "t/1", "features": {}}}) + "\n").encode()
        )
        await writer.drain()
        assert await _wait_for(lambda: len(srv._sessions) == 1)
        session = next(iter(srv._sessions.values()))
        srv._jobs["job-1"] = StratumJob(
            job_id="job-1", header={"number": 7}, share_target=1.0,
            theta_micro=THETA, raw={},
        )
        srv._job_order.append("job-1")
        srv._current_job_id = "job-1"

        # 1st submit accepted, 2nd identical submit must be refused.
        writer.write(submit("0xabc", session.session_id, 2))
        await writer.drain()
        assert await _wait_for(lambda: session.shares_accepted == 1, timeout=5.0)
        writer.write(submit("0xabc", session.session_id, 3))
        await writer.drain()
        assert await _wait_for(lambda: session.shares_rejected == 1, timeout=5.0)
        assert session.shares_accepted == 1
        # Equivalent nonce spellings must collide, not slip through.
        writer.write(submit("abc", session.session_id, 4))
        await writer.drain()
        assert await _wait_for(lambda: session.shares_rejected == 2, timeout=5.0)

        # A share that FAILED transiently stays re-submittable.
        outcome["ok"] = False
        writer.write(submit("0xdef", session.session_id, 5))
        await writer.drain()
        assert await _wait_for(lambda: session.shares_rejected == 3, timeout=5.0)
        outcome["ok"] = True
        writer.write(submit("0xdef", session.session_id, 6))
        await writer.drain()
        assert await _wait_for(lambda: session.shares_accepted == 2, timeout=5.0), \
            "a transiently-failed solution was permanently burned"
    finally:
        writer.close()
        await srv.stop()


def test_shares_per_block_cap_bounds_the_share_rate():
    from animica.stratum_pool.config import load_config_from_env
    cfg = load_config_from_env(
        overrides={"shares_per_block": 100_000, "rpc_url": "http://x",
                   "pool_address": "anim1x", "subblock_shares_enabled": True}
    )
    # The ratio floor cannot catch a huge S (ln grows too slowly: S=100k still
    # yields r>0.55 at live θ), so the cap has to.
    assert cfg.shares_per_block <= 1024
    pool = _pool(shares_per_block=100_000, subblock_min_ratio=0.5)
    assert pool.subblock_ratio_for_theta(THETA) is not None  # would have passed the floor
