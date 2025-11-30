import asyncio
import json
import time

import pytest

from animica.stratum_pool.asic import Sha256Job, Sha256StratumServer, _bits_to_target, _double_sha


class DummyAdapter:
    def __init__(self):
        self.submissions = []

    async def submit_block(self, payload):
        self.submissions.append(payload)
        return {"accepted": True, "payload": payload}


async def _read_json(reader):
    line = await reader.readline()
    return json.loads(line.decode())


class AntminerHarness:
    """Minimal emulator that exercises the Antminer stratum handshake."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def subscribe(self) -> dict:
        sub = {"id": 1, "method": "mining.subscribe", "params": ["antminer-harness"]}
        await self._send(sub)
        return await self._recv()

    async def authorize(self, worker: str) -> dict:
        auth = {"id": 2, "method": "mining.authorize", "params": [worker, "x"]}
        await self._send(auth)
        return await self._recv()

    async def submit(self, params: list) -> dict:
        payload = {"id": 3, "method": "mining.submit", "params": params}
        await self._send(payload)
        return await self._recv()

    async def _send(self, obj: dict) -> None:
        self._writer.write((json.dumps(obj) + "\n").encode())
        await self._writer.drain()

    async def _recv(self) -> dict:
        return await _read_json(self._reader)


@pytest.mark.asyncio
async def test_stratum_subscribe_notify_and_submit(tmp_path):
    adapter = DummyAdapter()
    server = Sha256StratumServer(
        host="127.0.0.1",
        port=0,
        adapter=adapter,
        extranonce2_size=4,
        default_difficulty=1e-12,
    )

    job = Sha256Job(
        job_id="job1",
        prevhash="00" * 32,
        coinb1="01000000",
        coinb2="abcd",
        merkle_branch=[],
        version="20000000",
        nbits="1d00ffff",
        ntime=f"{int(time.time()):08x}",
        clean_jobs=True,
        target=_bits_to_target("1d00ffff"),
        difficulty=1e-12,
        height=1,
    )

    await server.start()
    await server.publish_job(job)

    port = server._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    subscribe = {"id": 1, "method": "mining.subscribe", "params": ["tester"]}
    writer.write((json.dumps(subscribe) + "\n").encode())
    await writer.drain()

    sub_res = await _read_json(reader)
    extranonce1 = sub_res["result"][1]

    # set_difficulty and notify should follow
    await _read_json(reader)
    notify = await _read_json(reader)
    assert notify["method"] == "mining.notify"

    auth = {"id": 2, "method": "mining.authorize", "params": ["worker", "password"]}
    writer.write((json.dumps(auth) + "\n").encode())
    await writer.drain()
    await _read_json(reader)

    extranonce2 = "00" * server._extranonce2_size
    coinbase = bytes.fromhex(job.coinb1 + extranonce1 + extranonce2 + job.coinb2)
    merkle_root = _double_sha(coinbase)
    header = (
        bytes.fromhex(job.version)[::-1]
        + bytes.fromhex(job.prevhash)
        + merkle_root[::-1]
        + bytes.fromhex(job.ntime)[::-1]
        + bytes.fromhex(job.nbits)[::-1]
        + bytes.fromhex("00000000")
    )
    _double_sha(header)  # ensure hashing path exercised

    submit = {
        "id": 3,
        "method": "mining.submit",
        "params": ["worker", job.job_id, extranonce2, job.ntime, "00000000"],
    }
    writer.write((json.dumps(submit) + "\n").encode())
    await writer.drain()
    submit_res = await _read_json(reader)

    assert submit_res["result"] is True

    writer.close()
    await writer.wait_closed()
    await server.stop()


@pytest.mark.asyncio
async def test_antminer_harness_round_trip():
    adapter = DummyAdapter()
    server = Sha256StratumServer(
        host="127.0.0.1",
        port=0,
        adapter=adapter,
        extranonce2_size=4,
        default_difficulty=1e-12,
    )

    job = Sha256Job(
        job_id="job2",
        prevhash="00" * 32,
        coinb1="01000000",
        coinb2="abcd",
        merkle_branch=[],
        version="20000000",
        nbits="1d00ffff",
        ntime=f"{int(time.time()):08x}",
        clean_jobs=True,
        target=_bits_to_target("1d00ffff"),
        difficulty=1e-12,
        height=2,
    )

    await server.start()
    await server.publish_job(job)

    port = server._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    harness = AntminerHarness(reader, writer)

    sub_res = await harness.subscribe()
    extranonce1 = sub_res["result"][1]

    diff_msg = await _read_json(reader)
    notify = await _read_json(reader)
    assert diff_msg["params"][0] >= 1e-12
    assert notify["params"][0] == job.job_id

    auth_res = await harness.authorize("worker.antminer")
    assert auth_res["result"] is True

    extranonce2 = "00" * server._extranonce2_size
    coinbase = bytes.fromhex(job.coinb1 + extranonce1 + extranonce2 + job.coinb2)
    merkle_root = _double_sha(coinbase)
    header = (
        bytes.fromhex(job.version)[::-1]
        + bytes.fromhex(job.prevhash)
        + merkle_root[::-1]
        + bytes.fromhex(job.ntime)[::-1]
        + bytes.fromhex(job.nbits)[::-1]
        + bytes.fromhex("00000000")
    )
    _double_sha(header)

    submit_res = await harness.submit(["worker.antminer", job.job_id, extranonce2, job.ntime, "00000000"])
    assert submit_res["result"] is True

    stale_res = await harness.submit(["worker.antminer", "missing", extranonce2, job.ntime, "00000000"])
    assert stale_res["error"][0] == 21

    writer.close()
    await writer.wait_closed()
    await server.stop()


@pytest.mark.asyncio
async def test_stratum_subscribe_order_and_rejects(tmp_path):
    adapter = DummyAdapter()
    server = Sha256StratumServer(
        host="127.0.0.1",
        port=0,
        adapter=adapter,
        extranonce2_size=4,
        default_difficulty=1.0,
    )

    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    subscribe = {"id": 1, "method": "mining.subscribe", "params": ["tester"]}
    writer.write((json.dumps(subscribe) + "\n").encode())
    await writer.drain()

    sub_res = await _read_json(reader)
    assert sub_res["result"][0][0][0] == "mining.set_difficulty"
    assert sub_res["result"][0][1][0] == "mining.notify"

    # Drain the difficulty push
    await asyncio.wait_for(_read_json(reader), timeout=1.0)

    submit = {"id": 2, "method": "mining.submit", "params": ["worker", "missing", "00" * 4, "00000000", "00000000"]}
    writer.write((json.dumps(submit) + "\n").encode())
    await writer.drain()

    submit_res = await _read_json(reader)
    assert submit_res["error"][0] == 21
    assert submit_res["result"] is None

    writer.close()
    await writer.wait_closed()
    await server.stop()
