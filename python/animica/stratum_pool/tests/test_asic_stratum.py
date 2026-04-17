import asyncio
import json
import time

import pytest
from animica.stratum_pool.asic import (Sha256Job, Sha256StratumServer,
                                       _bits_to_target, _double_sha,
                                       _share_ratio_for_payout)


class DummyAdapter:
    def __init__(self):
        self.submissions = []

    async def submit_block(self, payload):
        self.submissions.append(payload)
        return {"accepted": True, "payload": payload}


class _FakeWriter:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def write(self, payload: bytes) -> None:
        self.messages.append(json.loads(payload.decode().strip()))

    async def drain(self) -> None:
        return None

    def get_extra_info(self, key: str):
        if key == "peername":
            return ("127.0.0.1", 0)
        return None


class _AlwaysAcceptValidator:
    def validate(self, _job, _session, _submit_params):
        return True, None, False


async def _read_json(reader):
    line = await reader.readline()
    return json.loads(line.decode())


class AntminerHarness:
    """Minimal emulator that exercises the Antminer stratum handshake."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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


def test_share_ratio_for_payout_is_normalized():
    assert _share_ratio_for_payout(64.0, "1d00ffff") == 1.0
    ratio = _share_ratio_for_payout(1.0, "1b0404cb")
    assert 0.0 < ratio < 1.0


@pytest.mark.asyncio
async def test_submit_payload_includes_normalized_share_ratio():
    adapter = DummyAdapter()
    server = Sha256StratumServer(
        host="127.0.0.1",
        port=0,
        adapter=adapter,
        extranonce2_size=4,
        default_difficulty=1.0,
    )
    server._validator = _AlwaysAcceptValidator()  # noqa: SLF001

    writer = _FakeWriter()
    session = server._alloc_session(writer)  # noqa: SLF001
    session.worker = "anim1miner.worker-01"
    session.address = "anim1miner"
    session.authorized = True
    job = Sha256Job(
        job_id="job-ratio",
        prevhash="00" * 32,
        coinb1="",
        coinb2="",
        merkle_branch=[],
        version="20000000",
        nbits="1b0404cb",
        ntime="00000000",
        clean_jobs=True,
        target=_bits_to_target("1b0404cb"),
        difficulty=16384.0,
        height=99,
    )
    server._jobs[job.job_id] = job  # noqa: SLF001
    server._current_job = job  # noqa: SLF001

    captured: dict[str, object] = {}

    async def _capture_submit(
        _session, _job, submit_payload, _accepted, _reason, _is_block, _tx_count
    ) -> None:
        captured["payload"] = dict(submit_payload)

    server.set_submit_hook(_capture_submit)
    await server._process_message(  # noqa: SLF001
        session,
        {
            "id": 7,
            "method": "mining.submit",
            "params": [
                "anim1miner.worker-01",
                "job-ratio",
                "00" * server._extranonce2_size,  # noqa: SLF001
                "00000000",
                "00000000",
            ],
        },
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    expected_ratio = _share_ratio_for_payout(session.difficulty, job.nbits)
    assert payload["d_ratio"] == pytest.approx(expected_ratio)
    assert 0.0 < float(payload["d_ratio"]) < 1.0


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

    auth_res = await harness.authorize("anim1antminer.worker-ant")
    assert auth_res["result"] is True
    snapshots = server.session_snapshots()
    assert snapshots
    assert snapshots[0]["address"] == "anim1antminer"
    assert snapshots[0]["worker"] == "worker-ant"

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

    submit_res = await harness.submit(
        ["anim1antminer.worker-ant", job.job_id, extranonce2, job.ntime, "00000000"]
    )
    assert submit_res["result"] is True

    stale_res = await harness.submit(
        ["anim1antminer.worker-ant", "missing", extranonce2, job.ntime, "00000000"]
    )
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

    submit = {
        "id": 2,
        "method": "mining.submit",
        "params": ["worker", "missing", "00" * 4, "00000000", "00000000"],
    }
    writer.write((json.dumps(submit) + "\n").encode())
    await writer.drain()

    submit_res = await _read_json(reader)
    assert submit_res["error"][0] == 21
    assert submit_res["result"] is None

    writer.close()
    await writer.wait_closed()
    await server.stop()
