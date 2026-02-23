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
import threading
from typing import Any, Callable

from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.services.cli_capabilities import get_cli_ops
from animica_studio.services.cli_ops import CliOperation
from animica_studio.services.cli_runner import CliRunner
from animica_studio.services.job_runner import resolve_animica_cli_program_and_env
from animica_studio.services.profile_helpers import get_active_rpc_url
from animica_studio.services.rpc_client import RpcClient, RpcResponseError
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
        self._last_error: str = ""
        self._last_request_payload: dict[str, Any] | None = None

    _METHOD_CACHE_LOCK = threading.Lock()
    _METHOD_CACHE_BY_URL: dict[str, dict[str, str | None]] = {}

    _CREDITS_METHODS = ("aicf.creditsByAddress", "aicf.credits_by_address", "aicf_creditsByAddress")

    def _rpc_url(self, override: str | None = None) -> str:
        raw = override or get_active_rpc_url(self._config) or self._config.get_active_profile().node.rpc_local_url
        return _ensure_rpc_path(raw)

    def _client(self, override: str | None = None) -> RpcClient:
        return RpcClient(self._rpc_url(override), connect_timeout=4.0, read_timeout=15.0, max_retries=2)

    def _build_claim_params(self, address: str) -> list[str]:
        return [address]

    def _build_claimable_params(self, address: str) -> list[str]:
        return [address]

    @staticmethod
    def _extract_methods(discover_payload: dict[str, Any]) -> set[str]:
        methods_raw = discover_payload.get("methods", [])
        methods: set[str] = set()
        if isinstance(methods_raw, list):
            for item in methods_raw:
                if isinstance(item, dict) and item.get("name"):
                    methods.add(str(item["name"]))
                elif isinstance(item, str):
                    methods.add(item)
        return methods

    @staticmethod
    def _pick_supported(candidates: tuple[str, ...], known_methods: set[str]) -> str | None:
        for method in candidates:
            if method in known_methods:
                return method
        return None

    @staticmethod
    def _pick_from_did_you_mean(candidates: tuple[str, ...], did_you_mean: Any) -> str | None:
        if not isinstance(did_you_mean, list):
            return None
        suggestions = {str(item) for item in did_you_mean}
        for method in candidates:
            if method in suggestions:
                return method
        return None

    @classmethod
    def _resolve_method_from_error(cls, candidates: tuple[str, ...], exc: RpcResponseError) -> str | None:
        return cls._pick_from_did_you_mean(candidates, (exc.rpc_error.data or {}).get("did_you_mean"))

    def _resolve_aicf_methods(self, client: RpcClient, rpc_url: str) -> dict[str, str | None]:
        with self._METHOD_CACHE_LOCK:
            cached = self._METHOD_CACHE_BY_URL.get(rpc_url)
            if cached is not None:
                return dict(cached)

        resolved: dict[str, str | None] = {
            "claim": "aicf.claim",
            "claimable": "aicf.getClaimable",
            "credits": self._CREDITS_METHODS[0],
        }

        try:
            known = self._extract_methods(client.discover())
            if known:
                claim_m = self._pick_supported(("aicf.claim",), known)
                claimable_m = self._pick_supported(("aicf.getClaimable",), known)
                credits_m = self._pick_supported(self._CREDITS_METHODS, known)
                resolved["claim"] = claim_m
                resolved["claimable"] = claimable_m
                resolved["credits"] = credits_m
        except Exception:  # noqa: BLE001
            pass

        with self._METHOD_CACHE_LOCK:
            self._METHOD_CACHE_BY_URL[rpc_url] = dict(resolved)
        return resolved

    def get_diagnostics(self, rpc_url: str | None = None) -> dict[str, Any]:
        url = self._rpc_url(rpc_url)
        with self._METHOD_CACHE_LOCK:
            methods = dict(self._METHOD_CACHE_BY_URL.get(url, {}))
        return {
            "rpc_url": url,
            "resolved_methods": methods,
            "last_request_payload": self._last_request_payload or {},
            "last_error": self._last_error,
        }

    @staticmethod
    def _to_int_amount(value: Any) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            if value.startswith("0x"):
                return int(value, 16)
            return int(value)
        return int(value)

    def _call_rpc(self, client: RpcClient, method: str, params: list[Any] | dict[str, Any] | None) -> Any:
        redacted = params
        if isinstance(params, list):
            redacted = [f"{str(p)[:8]}…" if isinstance(p, str) and len(p) > 12 else p for p in params]
        elif isinstance(params, dict):
            redacted = {
                k: (f"{str(v)[:8]}…" if k == "address" and isinstance(v, str) and len(v) > 12 else v)
                for k, v in params.items()
            }
        self._last_request_payload = {"method": method, "params": redacted}
        return client.call(method, params)

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
        url = self._rpc_url(rpc_url)
        try:
            methods = self._resolve_aicf_methods(client, url)
            last_exc: Exception | None = None
            attempts = [
                (methods.get("credits"), {"address": address}),
                (methods.get("credits"), [address]),
                ("state.getAicfMinerCredits", [address]),
                ("mining.getCredits", [address]),
                ("aicf.getMinerCredits", [address]),
                (methods.get("claimable"), self._build_claimable_params(address)),
            ]
            for method, params in attempts:
                if not method:
                    continue
                try:
                    result = self._call_rpc(client, method, params)
                    return {"ok": True, "data": result}
                except RpcResponseError as exc:
                    if exc.rpc_error.code == -32601:
                        if method in self._CREDITS_METHODS and methods.get("credits") in (None, method):
                            fallback = self._resolve_method_from_error(self._CREDITS_METHODS, exc)
                            if fallback:
                                methods["credits"] = fallback
                                with self._METHOD_CACHE_LOCK:
                                    self._METHOD_CACHE_BY_URL[url] = dict(methods)
                        last_exc = exc
                        continue
                    raise
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("No available RPC method for miner credits")
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    def get_claimable(self, address: str, rpc_url: str | None = None) -> dict:
        client = self._client(rpc_url)
        url = self._rpc_url(rpc_url)
        try:
            methods = self._resolve_aicf_methods(client, url)
            method = methods.get("claimable")
            if method:
                result = self._call_rpc(client, method, self._build_claimable_params(address))
                claimable = self._to_int_amount((result or {}).get("claimable", 0)) if isinstance(result, dict) else 0
                return {"ok": True, "data": result, "claimable": claimable}

            credits = self.get_miner_credits(address, rpc_url)
            if not credits.get("ok"):
                return credits
            payload = credits.get("data") if isinstance(credits.get("data"), dict) else {}
            claimable = self._to_int_amount(payload.get("balance", 0))
            return {"ok": True, "data": payload, "claimable": claimable}
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
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
        url = self._rpc_url(rpc_url)
        try:
            methods = self._resolve_aicf_methods(client, url)
            claim_method = methods.get("claim")
            if not claim_method:
                known = sorted(m for m in methods.values() if m)
                return {
                    "ok": False,
                    "error": (
                        "This node build does not support credit claiming via RPC. "
                        f"Supported methods: {', '.join(known) or 'none detected'}"
                    ),
                }

            claimable_info = self.get_claimable(address, rpc_url)
            if not claimable_info.get("ok"):
                return claimable_info
            if int(claimable_info.get("claimable", 0)) <= 0:
                return {"ok": False, "error": "No claimable credits available for this address."}

            params = self._build_claim_params(address)
            result = self._call_rpc(client, claim_method, params)
            if isinstance(result, dict):
                tx_hash = result.get("tx_hash") or result.get("hash")
                refreshed_claimable = self.get_claimable(address, rpc_url)
                refreshed_credits = self.get_miner_credits(address, rpc_url)
                return {
                    "ok": True,
                    "data": result,
                    "tx_hash": tx_hash,
                    "refresh": {
                        "claimable": refreshed_claimable.get("data"),
                        "credits": refreshed_credits.get("data"),
                    },
                    "amount_ignored": amount is not None,
                }
            return {"ok": True, "data": result, "amount_ignored": amount is not None}
        except RpcResponseError as exc:
            if exc.rpc_error.code == -32601:
                suggestion = self._resolve_method_from_error(("aicf.claim",), exc)
                if suggestion:
                    methods = self._resolve_aicf_methods(client, url)
                    methods["claim"] = suggestion
                    with self._METHOD_CACHE_LOCK:
                        self._METHOD_CACHE_BY_URL[url] = dict(methods)
            self._last_error = str(exc)
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
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
            log.info("AICF list_jobs rpc_url=%s payload=%s", self._rpc_url(rpc_url), params)
            last_exc: Exception | None = None
            for method, rpc_params in (
                ("aicf.listJobs", [params]),
                ("aicf_listJobs", [params]),
                ("aicf.listJobs", params),
                ("aicf_listJobs", params),
            ):
                try:
                    result = client.call(method, rpc_params)
                    return {"ok": True, "data": result}
                except RpcResponseError as exc:
                    if exc.rpc_error.code in (-32601, -32602):
                        last_exc = exc
                        continue
                    raise
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("No available RPC method for jobs list")
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
