"""QuantumService — quantum job management via existing CLI and RPC."""

from __future__ import annotations

import logging
from typing import Callable

from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.services.cli_runner import CliRunner
from animica_studio.services.rpc_client import RpcClient
from animica_studio.storage.config import Config
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


def _animica_bin(config: Config) -> str:
    return config.get_active_profile().cli.animica_bin


def _ensure_rpc_path(url: str) -> str:
    url = url.rstrip("/")
    if not url.endswith("/rpc"):
        url = url + "/rpc"
    return url


class QuantumService:
    """Quantum computation job management.

    Uses the existing ``animica quantum`` CLI commands and exposes RPC methods
    for status / credits / job submission.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._runner = CliRunner()

    def _rpc_url(self, override: str | None = None) -> str:
        raw = override or self._config.get_active_profile().node.rpc_local_url
        return _ensure_rpc_path(raw)

    def _client(self, override: str | None = None) -> RpcClient:
        return RpcClient(self._rpc_url(override), connect_timeout=4.0, read_timeout=15.0, max_retries=2)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self, rpc_url: str | None = None) -> dict:
        """Return quantum service status from RPC."""
        client = self._client(rpc_url)
        try:
            result = client.call("aicf.getQuantumServiceStatus")
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Credits
    # ------------------------------------------------------------------

    def get_credits(self, address: str, rpc_url: str | None = None) -> dict:
        """Return quantum credits for *address*."""
        client = self._client(rpc_url)
        try:
            result = client.call("aicf.getQuantumCredits", [address])
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        status_filter: str | None = None,
        rpc_url: str | None = None,
    ) -> dict:
        """List quantum jobs."""
        client = self._client(rpc_url)
        try:
            params: dict = {"limit": limit, "offset": offset}
            if status_filter:
                params["status"] = status_filter
            result = client.call("explorer_list_quantum_jobs", [params])
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def submit_job(
        self,
        problem_spec: dict,
        budget: int,
        qubits: int | None = None,
        shots: int | None = None,
        rpc_url: str | None = None,
    ) -> dict:
        """Submit a quantum computation job."""
        client = self._client(rpc_url)
        try:
            params: dict = {"problem": problem_spec, "budget": str(budget)}
            if qubits is not None:
                params["qubits"] = qubits
            if shots is not None:
                params["shots"] = shots
            result = client.call("aicf.submitQuantumJob", [params])
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # CLI-based watch
    # ------------------------------------------------------------------

    def watch_job_cli(
        self,
        job_id: str,
        *,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
        timeout_s: float = 300.0,
    ) -> ExecResult:
        """Stream job watch via the ``animica quantum`` CLI."""
        bin_ = _animica_bin(self._config)
        return self._runner.run(
            [bin_, "quantum", "jobs", "watch", job_id],
            cancel_token=cancel_token,
            stream_cb=stream_cb,
            timeout_s=timeout_s,
        )
