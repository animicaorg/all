"""AicfService — status, miner-credits, claim, jobs list/submit/watch via CLI + RPC.

Key notes
---------
* RPC base URL MUST end with ``/rpc`` and use POST (handled by RpcClient).
* The 405 error is caused by callers passing a bare ``http://host:port`` URL
  without the ``/rpc`` path, causing GET requests to non-RPC endpoints.
* This service normalises the URL before every call.
"""

from __future__ import annotations

import logging
from typing import Callable

from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.services.cli_capabilities import get_cli_ops
from animica_studio.services.cli_ops import CliOperation
from animica_studio.services.cli_runner import CliRunner
from animica_studio.services.job_runner import resolve_animica_cli_program_and_env
from animica_studio.services.rpc_client import RpcClient
from animica_studio.storage.config import Config
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


def _ensure_rpc_path(url: str) -> str:
    """Ensure *url* ends with ``/rpc``.

    Fixes the 405 Method Not Allowed error caused by posting to bare base URLs.
    """
    url = url.rstrip("/")
    if not url.endswith("/rpc"):
        url = url + "/rpc"
    return url


class AicfService:
    """AICF credit and job operations."""

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
        """Return AICF global summary from RPC."""
        client = self._client(rpc_url)
        try:
            result = client.call("state.getAicfSummary")
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Miner credits
    # ------------------------------------------------------------------

    def get_miner_credits(self, address: str, rpc_url: str | None = None) -> dict:
        """Return miner credits for *address*."""
        client = self._client(rpc_url)
        try:
            result = client.call("aicf.getMinerCredits", [address])
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Claim credits
    # ------------------------------------------------------------------

    def claim_credits(
        self,
        address: str,
        amount: int | None = None,
        rpc_url: str | None = None,
    ) -> dict:
        """Claim credits (full claim if *amount* is None)."""
        client = self._client(rpc_url)
        try:
            params: list = [address]
            if amount is not None:
                params.append(str(amount))  # send as string to avoid BigInt issues
            result = client.call("aicf.claimCredits", params)
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
        """List AICF jobs."""
        client = self._client(rpc_url)
        try:
            params: dict = {"limit": limit, "offset": offset}
            if status_filter:
                params["status"] = status_filter
            result = client.call("aicf.listJobs", [params])
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def submit_job(
        self,
        job_type: str,
        payload: dict,
        budget: int,
        rpc_url: str | None = None,
    ) -> dict:
        """Submit an AICF job."""
        client = self._client(rpc_url)
        try:
            result = client.call("aicf.submitJob", [{"type": job_type, "payload": payload, "budget": str(budget)}])
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def watch_job(
        self,
        job_id: str,
        *,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
        timeout_s: float = 300.0,
    ) -> ExecResult:
        """Stream job watch output via CLI."""
        ops = get_cli_ops(self._config)
        program, base_args, env = resolve_animica_cli_program_and_env(self._config)
        op_args = ops.build(CliOperation.AICF_JOBS_WATCH, {"job_id": job_id})
        return self._runner.run(
            [program, *base_args, *op_args],
            env=env or None,
            cancel_token=cancel_token,
            stream_cb=stream_cb,
            timeout_s=timeout_s,
        )

    # ------------------------------------------------------------------
    # Call-fee routing (ENA)
    # ------------------------------------------------------------------

    def get_call_fee_routing(self, rpc_url: str | None = None) -> dict:
        """Return ENA call-fee routing visibility."""
        client = self._client(rpc_url)
        try:
            result = client.call("aicf.getCallFeeRouting")
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()
