import asyncio
import socket
import time

import pytest

from mining.stratum_client import StratumClient
from mining.stratum_server import Session, StratumJob, StratumServer


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


@pytest.mark.asyncio
async def test_stratum_accepts_recent_previous_job_after_rollover():
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    await client.subscribe()
    await client.authorize(worker="rig1", address="anim1qqq")

    sign_hex = "0x" + "00" * 32
    hints = {"mixSeed": "0x" + "00" * 32}
    job1 = StratumJob(
        job_id="job1",
        header={"signBytes": sign_hex},
        share_target=1.0,
        theta_micro=1,
        hints=hints,
        target="0x" + "ff" * 32,
        sign_bytes=sign_hex,
        height=1,
        parent_hash="0x" + "11" * 32,
        parent_height=0,
        chain_id=1,
    )
    job2 = StratumJob(
        job_id="job2",
        header={"signBytes": sign_hex},
        share_target=1.0,
        theta_micro=1,
        hints=hints,
        target="0x" + "ff" * 32,
        sign_bytes=sign_hex,
        height=2,
        parent_hash="0x" + "22" * 32,
        parent_height=1,
        chain_id=1,
    )

    await server.publish_job(job1)
    await server.publish_job(job2)

    res = await client.submit_share(
        job_id="job1",
        hashshare={"nonce": "0x01", "body": {"hMicro": 1}},
    )
    assert res.get("accepted") is True, f"recent prior job should remain valid: {res}"

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stratum_rejects_expired_cached_job():
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    await client.subscribe()
    await client.authorize(worker="rig1", address="anim1qqq")

    sign_hex = "0x" + "00" * 32
    hints = {"mixSeed": "0x" + "00" * 32}
    job1 = StratumJob(
        job_id="job1",
        header={"signBytes": sign_hex},
        share_target=1.0,
        theta_micro=1,
        hints=hints,
        target="0x" + "ff" * 32,
        sign_bytes=sign_hex,
        height=1,
        parent_hash="0x" + "11" * 32,
        parent_height=0,
        chain_id=1,
        expires_at=time.time() - 1.0,
    )
    job2 = StratumJob(
        job_id="job2",
        header={"signBytes": sign_hex},
        share_target=1.0,
        theta_micro=1,
        hints=hints,
        target="0x" + "ff" * 32,
        sign_bytes=sign_hex,
        height=2,
        parent_hash="0x" + "22" * 32,
        parent_height=1,
        chain_id=1,
    )

    await server.publish_job(job1)
    await server.publish_job(job2)

    res = await client.submit_share(
        job_id="job1",
        hashshare={"nonce": "0x01", "body": {"hMicro": 1}},
    )
    assert res.get("error"), "expired cached job should be rejected"

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stratum_block_submit_hook_called():
    port = _free_port()
    got_block = asyncio.Event()

    async def _hook(*_args):
        got_block.set()

    server = StratumServer(host="127.0.0.1", port=port, submit_hook=_hook)
    await server.start()
    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    await client.subscribe()
    await client.authorize(worker="rig1", address="anim1qqq")

    sign_hex = "0x" + "00" * 32
    hints = {"mixSeed": "0x" + "00" * 32}
    job = StratumJob(
        job_id="job1",
        header={"signBytes": sign_hex},
        share_target=1.0,
        theta_micro=1,
        hints=hints,
        target="0x" + "ff" * 32,
        sign_bytes=sign_hex,
        height=1,
        parent_hash="0x" + "11" * 32,
        parent_height=0,
        chain_id=1,
    )
    await server.publish_job(job)

    res = await client.submit_share(
        job_id="job1",
        hashshare={"nonce": "0x01", "body": {"hMicro": 1}},
    )
    assert res.get("result", {}).get("accepted") is True
    await asyncio.wait_for(got_block.wait(), timeout=2.0)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_publish_job_does_not_block_fast_miners_on_stalled_peer():
    server = StratumServer(send_timeout_secs=0.05)

    slow = Session(session_id="slow", writer=object())  # type: ignore[arg-type]
    fast = Session(session_id="fast", writer=object())  # type: ignore[arg-type]
    server._sessions = {"slow": slow, "fast": fast}

    sent_to: list[str] = []

    async def _fake_send(session: Session, _obj: dict) -> None:
        if session.session_id == "slow":
            await asyncio.sleep(1.0)
            return
        sent_to.append(session.session_id)

    server._send = _fake_send  # type: ignore[assignment]

    job = StratumJob(
        job_id="job-fanout",
        header={"signBytes": "0x" + "00" * 32},
        share_target=1.0,
        theta_micro=1,
        hints={"mixSeed": "0x" + "00" * 32},
        target="0x" + "ff" * 32,
        sign_bytes="0x" + "00" * 32,
        height=1,
        parent_hash="0x" + "11" * 32,
        parent_height=0,
        chain_id=1,
    )

    await asyncio.wait_for(server.publish_job(job), timeout=0.5)

    assert "fast" in sent_to
    assert fast.jobs_seen[-1] == "job-fanout"
    assert "slow" not in server._sessions
