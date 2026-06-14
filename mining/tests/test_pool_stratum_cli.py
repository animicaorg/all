"""
End-to-end test for the new `animica miner mine-blocks --pool-stratum` flow.

Spins up an in-process StratumServer, publishes a job that the helper can
solve in a few thousand nonce iterations, then runs `_run_pool_stratum_miner`
against it and verifies it returns 1 accepted block.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from mining.hash_search import HashScanner
from mining.stratum_server import StratumJob, StratumServer


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def test_select_device_backend_falls_back_to_cpu(monkeypatch):
    """`_select_device_backend('auto')` must always return something usable."""
    from mining.internal_cpu_miner import _select_device_backend

    dev, name = _select_device_backend("auto")
    # On a stock CI box without GPU drivers, the CPU backend must be picked.
    # On a GPU box, a non-cpu backend will be returned. Either way the
    # device must be non-None and the name must be a known kind.
    assert dev is not None, "expected a real device backend"
    assert name in {"cpu", "cuda", "rocm", "opencl", "metal"}


def test_select_device_backend_unknown_falls_back_to_cpu():
    """Requesting a bogus backend must not raise — caller gets CPU."""
    from mining.internal_cpu_miner import _select_device_backend

    dev, name = _select_device_backend("not-a-real-backend")
    assert dev is not None
    assert name == "cpu"


@pytest.mark.asyncio
async def test_pool_stratum_miner_finds_and_submits_block():
    # Lazy import so the test only loads when the CLI module is available.
    pytest.importorskip("animica.cli.mining")
    from animica.cli.mining import _parse_pool_stratum_url, _run_pool_stratum_miner

    # URL parser sanity: returns (host, port, tls).
    host_parsed, port_parsed, tls_parsed = _parse_pool_stratum_url(
        "stratum+tcp://127.0.0.1:1234"
    )
    assert (host_parsed, port_parsed, tls_parsed) == ("127.0.0.1", 1234, False)
    # A TLS scheme must flip the tls flag.
    assert _parse_pool_stratum_url("stratum+ssl://127.0.0.1:1234") == (
        "127.0.0.1",
        1234,
        True,
    )

    theta_micro = 800_000
    share_ratio = 0.05
    prefix = b"animica-pool-stratum-cli-test"

    # Pre-solve a nonce so the helper finds it quickly.
    t_share = int(theta_micro * share_ratio)
    shares = HashScanner().scan_batch(
        prefix, t_share, nonce_start=0, nonce_count=20_000, theta_micro=theta_micro
    )
    assert shares, "test setup: no share found in window"

    port = _free_port()
    server = StratumServer(
        host="127.0.0.1",
        port=port,
        default_share_target=share_ratio,
        default_theta_micro=theta_micro,
    )
    await server.start()

    # Publish a job whose target accepts the precomputed share as a block.
    job = StratumJob(
        job_id="job-cli",
        header={
            "signBytes": "0x" + prefix.hex(),
            "number": 1,
            "target": "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        },
        share_target=share_ratio,
        theta_micro=theta_micro,
        target="0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        sign_bytes="0x" + prefix.hex(),
        height=1,
    )
    await server.publish_job(job)

    try:
        accepted = await asyncio.wait_for(
            _run_pool_stratum_miner(
                host="127.0.0.1",
                port=port,
                worker="cli-test",
                address="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3j5kq",
                target_blocks=1,
                scan_window=30_000,
            ),
            timeout=10.0,
        )
    finally:
        await server.stop()

    assert accepted >= 1, f"expected at least one block accepted, got {accepted}"
