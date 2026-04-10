import asyncio
import socket

import pytest

from mining.stratum_client import StratumClient
from mining.stratum_server import StratumJob, StratumServer


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


@pytest.mark.asyncio
async def test_stratum_rejects_stale_job():
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
    assert res.get("error"), "stale job submission should be rejected"

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
