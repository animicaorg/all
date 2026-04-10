import asyncio
import inspect
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
from pq.py.address import decode_address
from rpc.config import Config
from rpc import server as rpc_server

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.core import MiningCoreAdapter
from animica.stratum_pool.job_manager import JobManager
from animica.stratum_pool.metrics import PoolMetrics
from animica.stratum_pool.reference_cpu_miner import MinerConfig, StratumCpuMiner
from animica.stratum_pool.stratum_server import StratumPoolServer
from mining.share_submitter import RpcError


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _premine_address_hex() -> str:
    premine_addr_bech32 = MAINNET_PREMINE_DISTRIBUTION[0][0]
    addr_record = decode_address(premine_addr_bech32)
    digest = (
        bytes(addr_record.digest)
        if isinstance(addr_record.digest, list)
        else addr_record.digest
    )
    premine_addr_bytes = digest[:32].ljust(32, b"\x00")
    return "0x" + premine_addr_bytes.hex()


def _parse_balance(result: dict) -> int:
    balance = result.get("result", 0)
    if isinstance(balance, str):
        return int(balance, 16) if balance.startswith("0x") else int(balance)
    return int(balance)


async def _wait_for(predicate, *, timeout: float = 10.0, step: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        outcome = predicate()
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if outcome:
            return
        await asyncio.sleep(step)
    raise AssertionError("condition not met before timeout")


def _rpc_http_call(rpc_url: str, method: str, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    response = httpx.post(rpc_url, json=payload, timeout=5.0)
    response.raise_for_status()
    body = response.json()
    if "error" in body and body["error"]:
        error = body["error"]
        raise RpcError(
            error.get("code", -32000),
            error.get("message", "unknown error"),
            error.get("data"),
        )
    return body.get("result")


def test_pool_mines_real_block_via_stratum(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_UNSAFE_SKIP_GENESIS_BOOTSTRAP", "0")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")
    monkeypatch.setenv("ANIMICA_P2P_ENABLE", "0")
    monkeypatch.setenv("ANIMICA_P2P_REQUIRED", "0")
    monkeypatch.setenv("ANIMICA_P2P_CORE_ENABLE", "0")
    monkeypatch.setenv("ANIMICA_SNAPSHOT_SYNC_ENABLED", "0")
    monkeypatch.setenv("ANIMICA_SNAPSHOT_AUTO_DISCOVER", "0")

    repo_root = Path(__file__).resolve().parents[4]
    cfg = Config(
        host="127.0.0.1",
        port=0,
        db_uri=f"sqlite:///{tmp_path / 'rpc' / 'animica.db'}",
        chain_id=1337,
        genesis_path=repo_root / "core" / "genesis" / "devnet.json",
        logging="ERROR",
        cors_allow_origins=["*"],
        rate_limit_per_ip=0,
        rate_limit_per_method=0,
    )
    rpc_server.deps.ensure_started(cfg)
    rpc_port = _free_port()
    rpc_url = f"http://127.0.0.1:{rpc_port}/rpc"
    app = rpc_server.create_app(cfg)
    rpc_server_instance = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=rpc_port,
            loop="asyncio",
            log_level="warning",
        )
    )
    rpc_thread = threading.Thread(target=rpc_server_instance.run, daemon=True)
    rpc_thread.start()

    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]
    payout_account_hex = _premine_address_hex()
    try:
        ready_url = f"http://127.0.0.1:{rpc_port}/healthz"
        for _ in range(50):
            try:
                health = httpx.get(ready_url, timeout=1.0)
                if health.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("RPC server did not become ready")

        _rpc_http_call(rpc_url, "miner.mine", {"count": 1, "address": payout_address})
        head_before = _rpc_http_call(rpc_url, "chain.getHead")
        balance_before = _parse_balance(
            {"result": _rpc_http_call(rpc_url, "state.getBalance", [payout_account_hex])}
        )

        pool_config = PoolConfig(
            host="127.0.0.1",
            port=_free_port(),
            rpc_url=rpc_url,
            db_url=f"sqlite:///{tmp_path / 'pool.db'}",
            chain_id=cfg.chain_id,
            pool_address=payout_address,
            rpc_timeout=2.0,
            min_difficulty=1.0,
            max_difficulty=1.0,
            poll_interval=0.05,
            log_level="INFO",
            api_host="127.0.0.1",
            api_port=_free_port(),
            network="devnet",
        )

        async def _exercise() -> None:
            adapter = MiningCoreAdapter(
                pool_config.rpc_url,
                pool_config.chain_id,
                pool_config.pool_address,
                rpc_timeout_s=pool_config.rpc_timeout,
            )

            job_manager = JobManager(adapter, pool_config)
            server = StratumPoolServer(adapter, pool_config, job_manager)
            metrics = PoolMetrics(pool_config, job_manager, server.stratum)
            server.stratum.set_submit_hook(metrics.record_share)

            miner = StratumCpuMiner(
                MinerConfig(
                    host="127.0.0.1",
                    port=pool_config.port,
                    scheme="stratum+tcp",
                    tls=False,
                    address=payout_address,
                    worker="pytest-worker",
                    threads=1,
                    scan_window=25_000,
                    api_base_url="",
                    pool_mode="solo",
                    stats_interval_sec=60.0,
                    log_level="INFO",
                )
            )
            server_started = False
            miner_started = False

            async def _head_advanced() -> bool:
                head = await asyncio.to_thread(_rpc_http_call, rpc_url, "chain.getHead")
                return int(head.get("height", 0)) > int(head_before.get("height", 0))

            try:
                await server.start()
                server_started = True
                await _wait_for(
                    lambda: job_manager.current_job() is not None, timeout=5.0
                )

                await miner.start()
                miner_started = True
                await _wait_for(
                    _head_advanced,
                    timeout=10.0,
                )

                head_after = await asyncio.to_thread(
                    _rpc_http_call, rpc_url, "chain.getHead"
                )
                balance_after = _parse_balance(
                    {
                        "result": await asyncio.to_thread(
                            _rpc_http_call,
                            rpc_url,
                            "state.getBalance",
                            [payout_account_hex],
                        )
                    }
                )
                blocks = metrics.recent_blocks()
                worker = metrics.miner_detail("pytest-worker")

                assert int(head_after.get("height", 0)) > int(
                    head_before.get("height", 0)
                )
                assert balance_after >= balance_before
                assert blocks["blocks_found_total"] >= 1
                assert blocks["items"]
                assert blocks["items"][0]["worker"] == "pytest-worker"
                assert blocks["items"][0]["address"] == payout_address
                assert worker["blocks_found"] >= 1
                assert worker["shares_accepted"] >= 1
            finally:
                if miner_started:
                    await miner.close()
                if server_started:
                    await server.stop()

        asyncio.run(_exercise())
    finally:
        rpc_server_instance.should_exit = True
        rpc_thread.join(timeout=10)
