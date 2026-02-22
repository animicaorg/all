"""MiningService — mine-blocks, automine toggle, live mining log stream."""

from __future__ import annotations

import logging
from typing import Callable

from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.services.cli_capabilities import get_cli_ops, get_cli_registry
from animica_studio.services.cli_ops import CliOperation, CliOperationError
from animica_studio.services.job_runner import run_cli_blocking
from animica_studio.services.rpc_client import RpcClient, RpcResponseError, RpcTransportError
from animica_studio.storage.config import Config
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


class MiningService:
    """Local mining controls backed by CLI and RPC."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def build_mine_blocks_command(
        self,
        count: int,
        miner_address: str | None = None,
        threads: int = 0,
    ) -> tuple[list[str], dict[str, str]]:
        ops = get_cli_ops(self._config)
        op_args = ops.build(CliOperation.MINE_BLOCKS, {"count": count, "address": miner_address, "threads": threads})
        return op_args, {}

    def mining_diagnostics(self) -> str:
        registry = get_cli_registry(self._config)
        return registry.diagnostics(["miner", "mine-blocks"])

    def mine_blocks(
        self,
        count: int = 1,
        *,
        miner_address: str | None = None,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
        timeout_s: float = 120.0,
        extra_env: dict[str, str] | None = None,
    ) -> ExecResult:
        try:
            ops = get_cli_ops(self._config)
            op_args = ops.build(
                CliOperation.MINE_BLOCKS,
                {"count": count, "address": miner_address, "threads": 0},
            )
            resolved_env = {}
        except (FileNotFoundError, CliOperationError) as exc:
            registry = get_cli_registry(self._config)
            diag = registry.diagnostics(["miner", "mine-blocks"])
            return ExecResult(
                cmd=["animica", "miner", "mine-blocks"],
                returncode=2,
                timed_out=False,
                cancelled=False,
                start_ts=0,
                end_ts=0,
                duration_ms=0,
                stdout="",
                stderr=f"{exc}\n\n{diag}",
                stdout_lines=[],
                stderr_lines=[str(exc)],
                error=str(exc),
            )

        cmd = list(op_args)
        cp = run_cli_blocking(cmd, timeout_s=int(timeout_s), config=self._config, env=extra_env)
        return ExecResult(cmd=cmd, returncode=cp.returncode, timed_out=False, cancelled=False, start_ts=0, end_ts=0, duration_ms=0, stdout=cp.stdout or "", stderr=cp.stderr or "", stdout_lines=(cp.stdout or "").splitlines(), stderr_lines=(cp.stderr or "").splitlines(), error=None if cp.returncode == 0 else (cp.stderr or cp.stdout or "").strip())

    def set_automine(self, enabled: bool, rpc_url: str | None = None) -> dict:
        url = rpc_url or self._config.get_active_profile().node.rpc_local_url
        client = RpcClient(url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        try:
            method = "animica_setAutoMine" if enabled else "animica_stopAutoMine"
            result = client.call(method)
            return {"ok": True, "result": result}
        except (RpcResponseError, RpcTransportError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def get_work(self, rpc_url: str | None = None) -> dict:
        url = rpc_url or self._config.get_active_profile().node.rpc_local_url
        client = RpcClient(url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        try:
            result = client.call("miner_getWork")
            return {"ok": True, "work": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def submit_work(self, solution: dict, rpc_url: str | None = None) -> dict:
        url = rpc_url or self._config.get_active_profile().node.rpc_local_url
        client = RpcClient(url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        try:
            result = client.call("miner_submitWork", [solution])
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def start_hash_worker(
        self,
        *,
        threads: int = 1,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
    ) -> ExecResult:
        cmd = ["hash-worker", "start", "--threads", str(threads)]
        cp = run_cli_blocking(cmd, timeout_s=120, config=self._config)
        return ExecResult(cmd=cmd, returncode=cp.returncode, timed_out=False, cancelled=False, start_ts=0, end_ts=0, duration_ms=0, stdout=cp.stdout or "", stderr=cp.stderr or "", stdout_lines=(cp.stdout or "").splitlines(), stderr_lines=(cp.stderr or "").splitlines(), error=None if cp.returncode == 0 else (cp.stderr or cp.stdout or "").strip())
