"""MiningService — mine-blocks, automine toggle, live mining log stream."""

from __future__ import annotations

import logging
import os
from typing import Callable

from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.services.cli_runner import CliRunner
from animica_studio.services.rpc_client import RpcClient, RpcResponseError, RpcTransportError
from animica_studio.storage.config import Config
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


def _animica_bin(config: Config) -> str:
    return config.get_active_profile().cli.animica_bin


class MiningService:
    """Local mining controls backed by CLI and RPC."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._runner = CliRunner()

    # ------------------------------------------------------------------
    # Mine blocks
    # ------------------------------------------------------------------

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
        """Run ``animica mining mine-blocks --count N``.

        Environment variables from *extra_env* (e.g. ANIMICA_MINER_ADDRESS) are
        merged on top of the current environment.
        """
        bin_ = _animica_bin(self._config)
        cmd = [bin_, "mining", "mine-blocks", "--count", str(count)]
        if miner_address:
            cmd += ["--miner", miner_address]

        env: dict[str, str] = {}
        if extra_env:
            env.update(extra_env)

        return self._runner.run(
            cmd,
            env=env or None,
            timeout_s=timeout_s,
            cancel_token=cancel_token,
            stream_cb=stream_cb,
        )

    # ------------------------------------------------------------------
    # Automine via RPC
    # ------------------------------------------------------------------

    def set_automine(self, enabled: bool, rpc_url: str | None = None) -> dict:
        """Toggle automine via RPC (animica_setAutoMine / animica_stopAutoMine)."""
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

    # ------------------------------------------------------------------
    # getWork / submitWork
    # ------------------------------------------------------------------

    def get_work(self, rpc_url: str | None = None) -> dict:
        """Call miner.getWork and return the result dict."""
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
        """Call miner.submitWork with *solution*."""
        url = rpc_url or self._config.get_active_profile().node.rpc_local_url
        client = RpcClient(url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        try:
            result = client.call("miner_submitWork", [solution])
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Hash-worker daemon (CLI-based)
    # ------------------------------------------------------------------

    def start_hash_worker(
        self,
        *,
        threads: int = 1,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
    ) -> ExecResult:
        """Start the hash_worker daemon for continuous CPU mining."""
        bin_ = _animica_bin(self._config)
        cmd = [bin_, "hash-worker", "start", "--threads", str(threads)]
        return self._runner.run(
            cmd,
            cancel_token=cancel_token,
            stream_cb=stream_cb,
            timeout_s=None,
        )
