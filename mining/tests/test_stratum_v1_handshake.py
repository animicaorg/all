import asyncio
import json
import socket

import pytest

from mining.stratum_protocol import RpcErrorCodes
from mining.stratum_server import StratumJob, StratumServer


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


@pytest.mark.asyncio
async def test_v1_handshake_and_submit_round_trip():
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()

    job = StratumJob(
        job_id="job-v1",
        header={
            "parentHash": "0x" + "11" * 32,
            "coinb1": "0x1234",
            "coinb2": "0x5678",
            "merkleBranch": ["0x" + "22" * 32],
            "version": 0x20000000,
            "nbits": "1d00ffff",
            "timestamp": 0x5f5e100,
        },
        share_target=1.0,
        theta_micro=800_000,
    )
    await server.publish_job(job)

    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    async def _send(obj):
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()

    async def _recv():
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        assert line, "connection closed unexpectedly"
        return json.loads(line.decode())

    # Subscribe and expect difficulty + notify
    try:
        await _send({"id": 1, "method": "mining.subscribe", "params": []})
        sub_res = await _recv()
        assert sub_res["result"][1]  # extranonce1

        diff_msg = await _recv()
        notify_msg = await _recv()
        assert diff_msg.get("method") == "mining.set_difficulty"
        assert notify_msg.get("method") == "mining.notify"

        # Authorize
        await _send({"id": 2, "method": "mining.authorize", "params": ["worker.test", "p"]})
        auth_res = await _recv()
        auth_result = auth_res.get("result")
        assert auth_result is True or (isinstance(auth_result, dict) and auth_result.get("authorized"))

        # Submit a share
        ntime = notify_msg["params"][7]
        await _send(
            {"id": 3, "method": "mining.submit", "params": ["worker.test", job.job_id, "00000001", ntime, "00000000"]}
        )
        submit_res = await _recv()
        assert submit_res.get("result") is True

        # Submitting against stale job should return an error
        await _send(
            {"id": 4, "method": "mining.submit", "params": ["worker.test", "stale", "00000001", ntime, "00000000"]}
        )
        stale_res = await _recv()
        assert stale_res.get("error", {}).get("code") == RpcErrorCodes.STALE_JOB
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
