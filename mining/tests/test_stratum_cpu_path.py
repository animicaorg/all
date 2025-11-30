import asyncio
import socket

import pytest

from mining.hash_search import HashScanner, micro_threshold_to_target256
from mining.stratum_client import StratumClient
from mining.stratum_server import StratumJob, StratumServer


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


async def _start_stack(prefix: bytes, share_ratio: float, theta_micro: int, target_hex: str):
    port = _free_port()
    server = StratumServer(host="127.0.0.1", port=port, default_share_target=share_ratio, default_theta_micro=theta_micro)
    records = []

    async def hook(session, job, params, ok, reason, is_block, tx_count):
        records.append((ok, is_block, params))

    server.set_submit_hook(hook)
    await server.start()

    header = {"signBytes": "0x" + prefix.hex(), "number": 1, "target": target_hex}
    job = StratumJob(
        job_id="job-cpu",
        header=header,
        share_target=share_ratio,
        theta_micro=theta_micro,
        target=target_hex,
        sign_bytes=header["signBytes"],
        height=1,
    )
    await server.publish_job(job)

    client = StratumClient(host="127.0.0.1", port=port, agent="pytest-cpu")
    await client.connect()
    await client.subscribe()
    await client.authorize(worker="worker", address="addr")

    return server, client, records


@pytest.mark.asyncio
async def test_submit_valid_share_and_block_roundtrip():
    theta_micro = 800_000
    share_ratio = 0.05
    prefix = b"animica-stratum-cpu-test"
    t_share = int(theta_micro * share_ratio)
    scanner = HashScanner()
    shares = scanner.scan_batch(prefix, t_share, nonce_start=0, nonce_count=10_000, theta_micro=theta_micro)
    assert shares, "expected to find at least one share in the test window"
    share = shares[0]

    block_target_hex = hex(micro_threshold_to_target256(t_share))

    server, client, records = await _start_stack(prefix, share_ratio, theta_micro, "0x1")

    try:
        # Submit a standard share (should be accepted)
        result = await client.submit_share("job-cpu", {"nonce": hex(share.nonce), "body": {"hMicro": share.h_micro}})
        assert result.get("accepted"), f"share rejected: {result}"

        # Wait for submit hook to fire
        await asyncio.sleep(0.05)
        assert records, "submit hook did not fire"
        ok, is_block, params = records[-1]
        assert ok is True
        assert params.get("hashshare", {}).get("nonce") == hex(share.nonce)
        assert is_block is False

        # Publish another job that reuses the same threshold but treats it as a block target
        block_job = StratumJob(
            job_id="job-block",
            header={"signBytes": "0x" + prefix.hex(), "target": block_target_hex, "number": 2},
            share_target=share_ratio,
            theta_micro=theta_micro,
            target=block_target_hex,
            sign_bytes="0x" + prefix.hex(),
            height=2,
        )
        await server.publish_job(block_job)
        block_result = await client.submit_share("job-block", {"nonce": hex(share.nonce), "body": {"hMicro": share.h_micro}})
        assert block_result.get("accepted") is True
        assert block_result.get("isBlock") is True
    finally:
        await client.close()
        await server.stop()

