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
from animica_studio.services.ena_remote_preflight import ServicesPreflight
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

    _CREDITS_METHODS = (
        "aicf.creditsByAddress",
        "aicf_creditsByAddress",
        "aicf.credits_by_address",
        "aicf_credits_by_address",
    )
    _LIST_JOBS_METHODS = ("aicf.listJobs", "aicf_listJobs", "aicf.jobs", "aicf_jobs", "aicf.getJobs", "aicf_getJobs")

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
    def _format_rpc_error_payload(exc: Exception) -> dict[str, Any] | None:
        if not isinstance(exc, RpcResponseError):
            return None
        return {
            "code": exc.rpc_error.code,
            "message": exc.rpc_error.message,
            "data": exc.rpc_error.data,
        }

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
            "build_claim_tx": "aicf.buildClaimTx",
            "claimable": "aicf.getClaimable",
            "credits": "aicf.creditsByAddress",
            "list_jobs": "aicf.listJobs",
            "submit_job": "aicf.submitJob",
        }

        try:
            known = self._extract_methods(client.discover())
            if known:
                claim_m = self._pick_supported(("aicf.claim", "aicf_claim"), known)
                build_claim_tx_m = self._pick_supported(("aicf.buildClaimTx", "aicf_buildClaimTx"), known)
                claimable_m = self._pick_supported(("aicf.getClaimable", "aicf_getClaimable"), known)
                credits_m = self._pick_supported(self._CREDITS_METHODS, known)
                list_jobs = self._pick_supported(("aicf.listJobs", "aicf_listJobs", "aicf.jobs", "aicf_jobs", "aicf.getJobs", "aicf_getJobs"), known)
                submit_job = self._pick_supported(("aicf.submitJob", "aicf_submitJob"), known)
                resolved["claim"] = claim_m
                resolved["build_claim_tx"] = build_claim_tx_m
                resolved["claimable"] = claimable_m
                resolved["credits"] = credits_m
                resolved["list_jobs"] = list_jobs
                resolved["submit_job"] = submit_job
        except Exception:  # noqa: BLE001
            pass

        with self._METHOD_CACHE_LOCK:
            self._METHOD_CACHE_BY_URL[rpc_url] = dict(resolved)
        return resolved

    def get_diagnostics(self, rpc_url: str | None = None) -> dict[str, Any]:
        url = self._rpc_url(rpc_url)
        with self._METHOD_CACHE_LOCK:
            methods = dict(self._METHOD_CACHE_BY_URL.get(url, {}))
        client = self._client(url)
        discover_diag: dict[str, Any]
        try:
            discover_diag = client.rpc_diagnostics()
        except Exception as exc:  # noqa: BLE001
            discover_diag = {"error": str(exc), "rpc_url": url, "methods": []}
        finally:
            client.close()
        return {
            "rpc_url": url,
            "rpc_discover": discover_diag,
            "resolved_methods": methods,
            "last_request_payload": self._last_request_payload or {},
            "param_encoding": discover_diag.get("param_encoding", {}),
            "last_response_excerpt": discover_diag.get("last_response_excerpt", {}),
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

    def _call_rpc(self, client: RpcClient, method: str, values: dict[str, Any] | None = None) -> Any:
        redacted = {}
        for k, v in (values or {}).items():
            redacted[k] = f"{str(v)[:8]}…" if k == "address" and isinstance(v, str) and len(v) > 12 else v
        self._last_request_payload = {"method": method, "values": redacted}
        return client.call_with_schema(method, values or {})

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self, rpc_url: str | None = None) -> dict:
        """Return AICF global summary from RPC."""
        client = self._client(rpc_url)
        try:
            method = client.resolve_method("state_getAicfSummary", ["state_getAicfSummary", "state.getAicfSummary"])
            result = client.call(method)
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Miner credits
    # ------------------------------------------------------------------

    def get_miner_credits(self, address: str, rpc_url: str | None = None) -> dict:
        """Return miner credits for *address*.

        Iterates through candidate RPC methods directly (without calling
        discover/registry first) to avoid consuming mock HTTP responses in
        tests and to reduce latency when the node supports few methods.
        """
        client = self._client(rpc_url)
        try:
            # Ordered list of methods to try; positional calls avoid schema discovery overhead.
            candidate_methods = [
                *self._CREDITS_METHODS,
                "state_getAicfMinerCredits",
                "state.getAicfMinerCredits",
                "mining_getCredits",
                "mining.getCredits",
                "aicf_getMinerCredits",
                "aicf.getMinerCredits",
                "aicf.getClaimable",
                "aicf_getClaimable",
            ]
            # Pre-cache all candidates so call() won't trigger implicit discover calls.
            for _m in candidate_methods:
                client.precache_method(_m)
            last_exc: Exception | None = None
            for method in candidate_methods:
                try:
                    result = client.call(method, [address])
                    return {"ok": True, "data": result}
                except RpcResponseError as exc:
                    if exc.rpc_error.code == -32601:
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
                result = self._call_rpc(client, method, {"address": address})
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

            if claim_method:
                result = self._call_rpc(client, claim_method, {"address": address})
            else:
                built = self._call_rpc(client, build_claim_tx_method, {"address": address})
                raw_tx = None
                if isinstance(built, dict):
                    raw_tx = built.get("raw_tx") or built.get("rawTx") or built.get("tx")
                if not isinstance(raw_tx, str) or not raw_tx:
                    return {"ok": False, "error": "buildClaimTx succeeded but did not return raw tx bytes."}
                send_method = client.resolve_operation_method("SEND_RAW_TX")
                tx_hash = self._call_rpc(client, send_method, {"raw_tx": raw_tx, "raw": raw_tx, "tx": raw_tx})
                result = {"tx_hash": tx_hash, "built": built}
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
                suggestion = self._resolve_method_from_error(("aicf_claim", "aicf.claim"), exc)
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
        url = self._rpc_url(rpc_url)
        try:
            params: dict = {"limit": limit, "offset": offset}
            if status_filter:
                params["status"] = status_filter
            log.info("AICF list_jobs rpc_url=%s payload=%s", url, params)

            # Try to get method from registry; fall back to first default if discover fails.
            # IMPORTANT: do NOT call _resolve_aicf_methods here as it would trigger a second
            # discover call and consume HTTP mock responses in tests.
            aicf_methods: list[str] = []
            da_methods: list[str] = []
            resolved_method: str | None = None
            registry_has_methods: bool = False
            try:
                registry = client.registry()
                aicf_methods = registry.dump_methods("aicf")
                da_methods = registry.dump_methods("da")
                resolved_method = registry.resolve_any(list(self._LIST_JOBS_METHODS))
                registry_has_methods = bool(aicf_methods)
            except Exception:  # noqa: BLE001
                pass

            # If the registry reports AICF methods but none are list_jobs, the node does not
            # support this operation -> report "missing" without trying further calls.
            if registry_has_methods and not resolved_method:
                return {
                    "ok": False,
                    "error": "This node does not expose an AICF job-listing RPC method.",
                    "error_kind": "missing_aicf_list_jobs",
                    "aicf_methods": aicf_methods,
                    "da_methods": da_methods,
                    "list_jobs_method": None,
                }

            # If registry didn't provide a method, use cached method table (no new discover call).
            if not resolved_method:
                with self._METHOD_CACHE_LOCK:
                    cached_methods = self._METHOD_CACHE_BY_URL.get(url)
                if cached_methods and cached_methods.get("list_jobs"):
                    resolved_method = cached_methods["list_jobs"]
                else:
                    # Fall back to hard-coded default (no second discover call).
                    resolved_method = self._LIST_JOBS_METHODS[0]

            # Build ordered candidate list: resolved method first, then remaining defaults.
            candidates: list[str] = [resolved_method] + [m for m in self._LIST_JOBS_METHODS if m != resolved_method]
            positional: list = [limit, offset]
            if status_filter:
                positional.append(status_filter)

            # Pre-cache all candidate methods so call() won't trigger implicit discover calls.
            for _c in candidates:
                client.precache_method(_c)

            last_exc: RpcResponseError | None = None
            for candidate in candidates:
                try:
                    result = client.call(candidate, positional)
                    return {
                        "ok": True,
                        "data": result,
                        "method": candidate,
                        "aicf_methods": aicf_methods,
                        "da_methods": da_methods,
                        "list_jobs_method": candidate,
                    }
                except RpcResponseError as exc:
                    if exc.rpc_error.code == -32002 and "da is not enabled" in (exc.rpc_error.message or "").lower():
                        return {
                            "ok": False,
                            "error": "DA disabled on node (da.getStatus.enabled=false).",
                            "error_kind": "da_disabled",
                            "aicf_methods": aicf_methods,
                            "da_methods": da_methods,
                            "list_jobs_method": candidate,
                            "rpc_error": self._format_rpc_error_payload(exc),
                        }
                    if exc.rpc_error.code == -32602:
                        # Object-params fallback for this candidate only.
                        try:
                            result = client.call_with_schema(candidate, params)
                            return {
                                "ok": True,
                                "data": result,
                                "method": candidate,
                                "aicf_methods": aicf_methods,
                                "da_methods": da_methods,
                                "list_jobs_method": candidate,
                            }
                        except RpcResponseError as inner_exc:
                            last_exc = inner_exc
                            continue
                    elif exc.rpc_error.code == -32601:
                        last_exc = exc
                        continue
                    else:
                        raise

            # All candidates exhausted without success.
            return {
                "ok": False,
                "error": "This node does not expose an AICF job-listing RPC method.",
                "error_kind": "missing_aicf_list_jobs",
                "aicf_methods": aicf_methods,
                "da_methods": da_methods,
                "list_jobs_method": None,
            }
        except RpcResponseError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "error_kind": "rpc_error",
                "rpc_error": self._format_rpc_error_payload(exc),
            }
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
        backend = str(self._config.ena.get("job_backend") or "local").strip().lower()
        services_url = str(self._config.ena.get("services_url") or "").strip()
        if backend == "remote":
            preflight = ServicesPreflight.check(services_url)
            if not preflight.ok:
                return {
                    "ok": False,
                    "error": "Remote ENA services unreachable (DNS/HTTP).",
                    "error_kind": preflight.error_kind,
                    "message": preflight.message,
                    "action": "Switch to Local Mode",
                    "preflight": preflight.to_dict(),
                }
        client = self._client(rpc_url)
        try:
            methods = self._resolve_aicf_methods(client, self._rpc_url(rpc_url))
            submit_method = methods.get("submit_job")
            if not submit_method:
                return {"ok": False, "error": "This node does not expose AICF RPC methods for job submission."}
            result = client.call_with_schema(submit_method, {"type": job_type, "payload": payload, "budget": str(budget)})
            return {"ok": True, "data": result, "method": submit_method, "backend": backend}
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
            method = client.resolve_method("aicf_getCallFeeRouting", ["aicf_getCallFeeRouting", "aicf.getCallFeeRouting"])
            result = client.call(method)
            return {"ok": True, "data": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()
