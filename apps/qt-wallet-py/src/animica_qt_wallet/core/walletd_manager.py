from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from animica_qt_wallet.core.paths import get_app_data_dir
from animica_qt_wallet.walletd.config import load_or_create_token, resolve_port


@dataclass
class WalletdStatus:
    running: bool
    pid: int | None
    rpc_url: str
    last_error: str | None


class WalletdManager:
    def __init__(self, port: int | None = None) -> None:
        self._data_dir = get_app_data_dir()
        self._token = load_or_create_token(self._data_dir)
        self._port = resolve_port(port)
        self._rpc_url = f"http://127.0.0.1:{self._port}"
        self._process: subprocess.Popen[str] | None = None
        self._started_by_app = False
        self._logger = logging.getLogger(__name__)

    @property
    def rpc_url(self) -> str:
        return self._rpc_url

    async def ensure_running(self) -> WalletdStatus:
        status = await self._get_status()
        if status.running:
            return status

        self._start_process()
        status = await self._wait_for_ready()
        return status

    async def shutdown(self) -> None:
        if self._process and self._started_by_app:
            self._logger.info("Stopping walletd")
            self._process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self._process.wait), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        self._process = None
        self._started_by_app = False

    async def _get_status(self) -> WalletdStatus:
        try:
            result = await self._rpc_call("walletd.getStatus")
            return WalletdStatus(
                running=True,
                pid=result.get("pid"),
                rpc_url=result.get("rpc_url", self._rpc_url),
                last_error=result.get("last_error"),
            )
        except Exception as exc:  # noqa: BLE001
            return WalletdStatus(
                running=False,
                pid=None,
                rpc_url=self._rpc_url,
                last_error=str(exc),
            )

    def _start_process(self) -> None:
        if self._process and self._process.poll() is None:
            return
        self._logger.info("Starting walletd process on %s", self._rpc_url)
        log_path = self._data_dir / "walletd-process.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "animica_qt_wallet.walletd",
                "--port",
                str(self._port),
                "--data-dir",
                str(self._data_dir),
            ],
            stdout=log_file,
            stderr=log_file,
            text=True,
        )
        self._started_by_app = True

    async def _wait_for_ready(self) -> WalletdStatus:
        for _ in range(20):
            status = await self._get_status()
            if status.running:
                return status
            await asyncio.sleep(0.25)
        return await self._get_status()

    async def _rpc_call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        headers = {"Authorization": f"Bearer {self._token}"}
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._rpc_url, json=payload, headers=headers) as response:
                if response.status != 200:
                    raise RuntimeError(f"walletd error: {response.status}")
                data = await response.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "walletd error"))
        return data.get("result", {})
