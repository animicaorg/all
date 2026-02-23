"""QuantumService — safe quantum job management via RPC + CLI."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from animica_studio.services.rpc_client import RpcClient
from animica_studio.storage.config import Config

log = logging.getLogger(__name__)


def _ensure_rpc_path(url: str) -> str:
    url = url.rstrip("/")
    if not url.endswith("/rpc"):
        url = url + "/rpc"
    return url


@dataclass(frozen=True)
class QuantumRpcCapabilities:
    status_method: str | None
    credits_method: str | None
    list_jobs_method: str | None
    submit_job_method: str | None


class QuantumService:
    """Quantum computation job management.

    Notes
    -----
    * RPC methods can vary across node versions.
    * This service lazily discovers available methods and returns actionable
      "not implemented" errors when unsupported.
    """

    _METHOD_CANDIDATES: dict[str, tuple[str, ...]] = {
        "status": (
            "aicf.getQuantumServiceStatus",
            "state.getQuantumServiceStatus",
        ),
        "credits": (
            "aicf.getQuantumCredits",
            "state.getQuantumCredits",
        ),
        "list_jobs": (
            "explorer_list_quantum_jobs",
            "aicf.listQuantumJobs",
        ),
        "submit_job": (
            "aicf.submitQuantumJob",
        ),
    }

    def __init__(self, config: Config) -> None:
        self._config = config
        self._capabilities: QuantumRpcCapabilities | None = None

    def _rpc_url(self, override: str | None = None) -> str:
        raw = override or self._config.get_active_profile().node.rpc_local_url
        return _ensure_rpc_path(raw)

    def _client(self, override: str | None = None) -> RpcClient:
        return RpcClient(self._rpc_url(override), connect_timeout=4.0, read_timeout=15.0, max_retries=2)

    def discover_capabilities(self, rpc_url: str | None = None, *, force: bool = False) -> QuantumRpcCapabilities:
        if self._capabilities is not None and not force:
            return self._capabilities

        client = self._client(rpc_url)
        resolved: dict[str, str | None] = {
            "status": None,
            "credits": None,
            "list_jobs": None,
            "submit_job": None,
        }
        try:
            for key, methods in self._METHOD_CANDIDATES.items():
                for method in methods:
                    try:
                        known = {m.get("name", "") if isinstance(m, dict) else str(m) for m in client.discover().get("methods", [])}
                        if method in known:
                            resolved[key] = method
                            break
                    except Exception:
                        # Discovery optional; keep fallback behaviour by selecting
                        # first candidate so calls still execute and return explicit errors.
                        resolved[key] = methods[0] if methods else None
                        break
        finally:
            client.close()

        self._capabilities = QuantumRpcCapabilities(
            status_method=resolved["status"],
            credits_method=resolved["credits"],
            list_jobs_method=resolved["list_jobs"],
            submit_job_method=resolved["submit_job"],
        )
        return self._capabilities

    def _pick_method(self, key: str, rpc_url: str | None = None) -> str | None:
        caps = self.discover_capabilities(rpc_url)
        return {
            "status": caps.status_method,
            "credits": caps.credits_method,
            "list_jobs": caps.list_jobs_method,
            "submit_job": caps.submit_job_method,
        }[key]

    def get_status(self, rpc_url: str | None = None) -> dict:
        client = self._client(rpc_url)
        try:
            method = self._pick_method("status", rpc_url)
            if not method:
                return {"ok": False, "error": "Not implemented yet (server does not expose quantum status method)"}
            result = client.call(method)
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def get_credits(self, address: str, rpc_url: str | None = None) -> dict:
        client = self._client(rpc_url)
        try:
            method = self._pick_method("credits", rpc_url)
            if not method:
                return {"ok": False, "error": "Not implemented yet (server does not expose quantum credits method)"}
            result = client.call(method, [address])
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        status_filter: str | None = None,
        rpc_url: str | None = None,
    ) -> dict:
        client = self._client(rpc_url)
        try:
            method = self._pick_method("list_jobs", rpc_url)
            if not method:
                return {"ok": False, "error": "Not implemented yet (server does not expose quantum jobs list method)"}
            params: dict = {"limit": limit, "offset": offset}
            if status_filter:
                params["status"] = status_filter
            result = client.call(method, [params])
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
        client = self._client(rpc_url)
        try:
            method = self._pick_method("submit_job", rpc_url)
            if not method:
                return {"ok": False, "error": "Not implemented yet (server does not expose quantum submit method)"}
            params: dict = {"problem": problem_spec, "budget": str(budget)}
            if qubits is not None:
                params["qubits"] = qubits
            if shots is not None:
                params["shots"] = shots
            result = client.call(method, [params])
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()
