from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from animica.config import get_network_defaults
from animica_qt_wallet.walletd.config import resolve_node_data_dir, resolve_node_log_path

NODE_READY_SECONDS = 2.0
NODE_READY_POLL_INTERVAL = 0.2
NODE_STOP_TIMEOUT = 10.0
NODE_MAX_BACKOFF = 30.0


@dataclass
class NodeConfig:
    network: str
    rpc_port: int
    p2p_port: int
    metrics_port: int
    data_dir: Path
    rpc_url: str


@dataclass
class NodeStatus:
    running: bool
    pid: int | None
    network: str | None
    rpc_url: str | None
    restarting: bool
    last_exit_code: int | None
    last_error: str | None
    backoff_seconds: float
    started_at: float | None


class NodeManager:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._logger = logging.getLogger(__name__)
        self._process: subprocess.Popen[str] | None = None
        self._restart_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._status = NodeStatus(
            running=False,
            pid=None,
            network=None,
            rpc_url=None,
            restarting=False,
            last_exit_code=None,
            last_error=None,
            backoff_seconds=0.0,
            started_at=None,
        )
        self._extra_args: list[str] = []
        self._log_handle: Any | None = None

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def status(self) -> NodeStatus:
        return self._status

    async def start(self, network: str, extra_args: list[str] | None = None) -> NodeStatus:
        if self._process and self._process.poll() is None:
            return self._status
        self._stopping = False
        self._extra_args = list(extra_args or [])
        self._status = self._status.__class__(
            running=False,
            pid=None,
            network=network,
            rpc_url=None,
            restarting=False,
            last_exit_code=None,
            last_error=None,
            backoff_seconds=0.0,
            started_at=None,
        )
        await self._spawn_process(network)
        await self._wait_for_ready()
        if self._restart_task is None or self._restart_task.done():
            self._restart_task = asyncio.create_task(self._restart_loop())
        return self._status

    async def stop(self) -> None:
        self._stopping = True
        if self._restart_task:
            self._restart_task.cancel()
            self._restart_task = None
        await self._terminate_process()
        self._status = self._status.__class__(
            running=False,
            pid=None,
            network=self._status.network,
            rpc_url=self._status.rpc_url,
            restarting=False,
            last_exit_code=self._status.last_exit_code,
            last_error=self._status.last_error,
            backoff_seconds=0.0,
            started_at=None,
        )

    async def _spawn_process(self, network: str) -> None:
        config = self._resolve_config(network)
        log_path = resolve_node_log_path(self._data_dir, network)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_logs(log_path)
        self._log_handle = log_path.open("a", encoding="utf-8")
        
        # Use Python module invocation instead of binary wrapper for better compatibility
        # with frozen/packaged applications (PyInstaller, etc.)
        import sys
        cmd = [
            sys.executable,
            "-m",
            "animica.cli.main",
            "--network",
            network,
            "node",
            "up",
            "--no-detach",
            "--no-wait-sync",
        ]
        env = os.environ.copy()
        env["ANIMICA_NETWORK"] = network
        env["ANIMICA_DATA_DIR"] = str(config.data_dir.parent)
        env["HOST_RPC_PORT"] = str(config.rpc_port)
        env["HOST_P2P_PORT"] = str(config.p2p_port)
        env["HOST_P2P_TCP_PORT"] = str(config.p2p_port)
        env["HOST_METRICS_PORT"] = str(config.metrics_port)
        env["ANIMICA_RPC_HOST"] = "127.0.0.1"
        if self._extra_args:
            cmd.extend(self._extra_args)
        self._logger.info("Starting node: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            stdout=self._log_handle,
            stderr=self._log_handle,
            text=True,
            env=env,
        )
        self._status = self._status.__class__(
            running=False,
            pid=self._process.pid,
            network=network,
            rpc_url=config.rpc_url,
            restarting=False,
            last_exit_code=None,
            last_error=None,
            backoff_seconds=0.0,
            started_at=time.time(),
        )

    async def _terminate_process(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._logger.info("Stopping node process (pid=%s)", self._process.pid)
            self._process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self._process.wait), timeout=NODE_STOP_TIMEOUT)
            except asyncio.TimeoutError:
                self._logger.warning("Node did not exit; killing (pid=%s)", self._process.pid)
                self._process.kill()
                await asyncio.to_thread(self._process.wait)
        self._close_log_handle()
        self._process = None

    async def _restart_loop(self) -> None:
        backoff = 1.0
        while not self._stopping:
            await asyncio.sleep(NODE_READY_POLL_INTERVAL)
            if not self._process:
                continue
            if self._process.poll() is None:
                self._status.running = True
                self._status.restarting = False
                continue
            exit_code = self._process.returncode
            self._status.last_exit_code = exit_code
            self._status.running = False
            if self._stopping:
                return
            self._close_log_handle()
            self._status.restarting = True
            self._status.backoff_seconds = backoff
            self._logger.warning("Node exited (code=%s); restarting in %.1fs", exit_code, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, NODE_MAX_BACKOFF)
            try:
                await self._spawn_process(self._status.network or "mainnet")
                await self._wait_for_ready()
            except Exception as exc:  # noqa: BLE001
                self._status.last_error = str(exc)
                self._logger.exception("Failed to restart node")

    async def _wait_for_ready(self) -> None:
        deadline = time.time() + NODE_READY_SECONDS
        while time.time() < deadline:
            if not self._process or self._process.poll() is not None:
                self._status.running = False
                return
            self._status.running = True
            await asyncio.sleep(NODE_READY_POLL_INTERVAL)
        if not self._process or self._process.poll() is not None:
            self._status.running = False
            self._status.last_error = "Node exited during startup"
        else:
            self._status.running = True

    def _resolve_config(self, network: str) -> NodeConfig:
        defaults = get_network_defaults(network)
        data_dir = resolve_node_data_dir(self._data_dir, network)
        data_dir.mkdir(parents=True, exist_ok=True)
        rpc_port = defaults["rpc_port"]
        p2p_port = defaults["p2p_port"]
        metrics_port = defaults["metrics_port"]
        rpc_url = f"http://127.0.0.1:{rpc_port}/rpc"
        return NodeConfig(
            network=network,
            rpc_port=rpc_port,
            p2p_port=p2p_port,
            metrics_port=metrics_port,
            data_dir=data_dir,
            rpc_url=rpc_url,
        )



    def _rotate_logs(self, log_path: Path, keep: int = 5) -> None:
        if log_path.exists():
            for idx in range(keep, 0, -1):
                src = Path(f"{log_path}.{idx}") if idx > 1 else log_path
                dst = Path(f"{log_path}.{idx + 1}")
                if src.exists():
                    if idx == keep:
                        src.unlink(missing_ok=True)
                    else:
                        src.rename(dst)

    def _close_log_handle(self) -> None:
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
