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


def _sample_job(job_id: str) -> StratumJob:
    sign_hex = "0x" + "aa" * 32
    hints = {"mixSeed": "0x" + "bb" * 32}
    return StratumJob(
        job_id=job_id,
        header={"signBytes": sign_hex, "height": 100},
        share_target=0.01,
        theta_micro=800_000,
        hints=hints,
        target="0x" + "ff" * 32,
        sign_bytes=sign_hex,
        height=100,
        parent_hash="0x" + "11" * 32,
        parent_height=99,
        chain_id=1,
    )


@pytest.mark.asyncio
async def test_notify_sent_after_authorize():
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()

    job = _sample_job("initial-job")
    await server.publish_job(job)

    client = StratumClient(host="127.0.0.1", port=port)
    notify_event = asyncio.Event()
    notified_jobs = []

    async def on_notify(job_data):
        notified_jobs.append(job_data)
        notify_event.set()

    client.on_notify = on_notify
    await client.connect()
    await client.subscribe()

    await asyncio.sleep(0.1)
    notify_event.clear()
    client.last_job = None

    await client.authorize(worker="notify-worker", address="anim1notify")

    await asyncio.wait_for(notify_event.wait(), timeout=1.0)
    assert notified_jobs[-1].get("jobId") == job.job_id

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_authorize_sends_difficulty_and_notify_when_job_ready():
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port)
    await server.start()

    job = _sample_job("ready-job")
    await server.publish_job(job)

    client = StratumClient(host="127.0.0.1", port=port)
    difficulty_event = asyncio.Event()
    notify_event = asyncio.Event()

    async def on_set_difficulty(_share_target, _theta_micro):
        difficulty_event.set()

    async def on_notify(job_data):
        if job_data.get("jobId") == job.job_id:
            notify_event.set()

    client.on_set_difficulty = on_set_difficulty
    client.on_notify = on_notify

    await client.connect()
    await client.subscribe()
    difficulty_event.clear()
    notify_event.clear()

    await client.authorize(worker="sequence-worker", address="anim1sequence")

    await asyncio.wait_for(difficulty_event.wait(), timeout=1.0)
    await asyncio.wait_for(notify_event.wait(), timeout=1.0)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_authorize_waits_for_job_without_closing():
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port, initial_job_timeout=0.5)
    await server.start()

    client = StratumClient(host="127.0.0.1", port=port)
    notify_event = asyncio.Event()

    async def on_notify(_job_data):
        notify_event.set()

    client.on_notify = on_notify

    await client.connect()
    await client.subscribe()
    await client.authorize(worker="retry-worker", address="anim1retry")

    async def publish_later():
        await asyncio.sleep(1.2)
        await server.publish_job(_sample_job("delayed-job"))

    asyncio.create_task(publish_later())

    await asyncio.wait_for(notify_event.wait(), timeout=3.0)

    await asyncio.sleep(5.0)
    assert not client._closed
    assert client.writer and not client.writer.is_closing()

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_idle_connection_stays_open():
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port, initial_job_timeout=0.5)
    await server.start()

    client = StratumClient(host="127.0.0.1", port=port)
    await client.connect()
    await client.subscribe()
    await client.authorize(worker="idle-worker", address="anim1idle")

    await asyncio.sleep(3.0)

    assert not client._closed
    assert client.writer and not client.writer.is_closing()

    await client.close()
    await server.stop()
