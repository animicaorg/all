"""Robust JSON-RPC 2.0 HTTP client with retries, backoff, and discover caching.

Features
--------
* Strict response parsing: enforces ``jsonrpc == "2.0"``, handles result/error.
* Exponential backoff with jitter on network errors, 5xx, and 429 responses.
* Method-name discovery via ``rpc.discover``; results cached for 60 s.
* Fallback method names for Animica's ``underscore`` vs ``dot`` variants.
* Configurable connect + read timeouts.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from difflib import get_close_matches
from typing import Any

import requests
import requests.exceptions

from animica_studio.models.rpc_models import (
    BalanceResponse,
    Head,
    RpcError,
    RpcResponse,
    parse_hex_quantity,
)
from animica_studio.services.error_format import safe_json_dumps

log = logging.getLogger(__name__)

_DEFAULT_CONNECT_TIMEOUT = 5.0  # seconds
_DEFAULT_READ_TIMEOUT = 15.0  # seconds
_MAX_RETRIES = 3
_BASE_BACKOFF_S = 0.5
_DISCOVER_CACHE_TTL_S = 300.0

_DISCOVER_CACHE_BY_URL: dict[str, tuple[float, dict[str, Any], "RpcRegistry"]] = {}
_DISCOVER_CACHE_LOCK = threading.Lock()
_PARAM_ENCODING_BY_URL: dict[str, dict[str, str]] = {}
_RESOLVED_METHODS_BY_URL: dict[str, dict[str, str]] = {}


_RPC_OPERATION_CANDIDATES: dict[str, tuple[str, ...]] = {
    "GET_HEAD": ("chain_getHead", "chain.getHead"),
    "GET_BALANCE": ("state_getBalance", "state.getBalance", "wallet_getBalance", "wallet.getBalance"),
    "GET_PENDING_NONCE": ("state_getPendingNonce", "state.getPendingNonce"),
    "SEND_RAW_TX": ("tx_sendRawTransaction", "tx.sendRawTransaction", "tx_submitRawTransaction"),
    "GET_TX_BY_HASH": ("tx_getTransactionByHash", "tx.getTransactionByHash"),
    "GET_TX_RECEIPT": ("tx_getTransactionReceipt", "tx.getTransactionReceipt", "tx_getReceipt"),
    "GET_CHAIN_ID": ("chain_getChainId", "chain.getChainId", "eth_chainId"),
    "DA_PUT_BLOB": ("da_putBlob", "da.putBlob"),
    "DA_GET_BLOB": ("da_getBlob", "da.getBlob"),
    "DA_GET_PROOF": ("da_getProof", "da.getProof"),
    "DA_CONFIGURE": ("da_configure", "da.configure"),
    "DA_GET_STATUS": ("da_getStatus", "da.getStatus", "da_status", "da.status"),
    "AICF_CLAIM": ("aicf_claim", "aicf.claim"),
    "AICF_CREDITS_BY_ADDRESS": ("aicf_creditsByAddress", "aicf.creditsByAddress", "aicf_credits_by_address", "aicf.credits_by_address"),
    "AICF_LIST_JOBS": ("aicf_listJobs", "aicf.listJobs", "aicf_jobs", "aicf_getJobs"),
    "AICF_SUBMIT_JOB": ("aicf_submitJob", "aicf.submitJob"),
}


class RpcRegistry:
    """Per-RPC URL OpenRPC registry from ``rpc.discover``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.server_info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        self.methods: dict[str, dict[str, Any]] = {}
        self.exact_methods: set[str] = set()
        self.normalized_methods: dict[str, list[str]] = {}
        methods_raw = payload.get("methods", [])
        if isinstance(methods_raw, list):
            for item in methods_raw:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name:
                    continue
                params: list[dict[str, Any]] = []
                params_raw = item.get("params", [])
                declared_structure = str(item.get("paramStructure") or "").strip().lower()
                param_structure = "unknown"
                if declared_structure in {"by-name", "object", "named"}:
                    param_structure = "object"
                elif declared_structure in {"by-position", "positional", "array"}:
                    param_structure = "positional"

                if isinstance(params_raw, list):
                    if param_structure == "unknown":
                        param_structure = "positional"
                    for p in params_raw:
                        if not isinstance(p, dict):
                            continue
                        schema = p.get("schema") if isinstance(p.get("schema"), dict) else {}
                        params.append(
                            {
                                "name": p.get("name"),
                                "required": bool(p.get("required", False)),
                                "schema_type": schema.get("type"),
                            }
                        )
                elif isinstance(params_raw, dict):
                    param_structure = "object"
                    schema = params_raw.get("schema") if isinstance(params_raw.get("schema"), dict) else {}
                    params.append(
                        {
                            "name": params_raw.get("name") or "params",
                            "required": bool(params_raw.get("required", False)),
                            "schema_type": schema.get("type") or "object",
                        }
                    )
                result_schema = item.get("result") if isinstance(item.get("result"), dict) else None
                self.methods[name] = {
                    "name": name,
                    "params": params,
                    "param_structure": param_structure,
                    "result": result_schema,
                    "raw": item,
                }
                self.exact_methods.add(name)
                normalized = self.normalize(name)
                self.normalized_methods.setdefault(normalized, []).append(name)

    @property
    def method_names(self) -> set[str]:
        return set(self.exact_methods)

    @staticmethod
    def normalize(name: str) -> str:
        lowered = name.lower().replace(".", "_")
        return re.sub(r"_+", "_", lowered).strip("_")

    @staticmethod
    def _match_sort_key(name: str) -> tuple[int, str]:
        # Prefer dotted variants first, then underscore; tie-break lexicographically.
        return (0 if "." in name else 1, name)

    def has_method(self, name: str) -> bool:
        return name in self.exact_methods

    def has_any(self, prefix_candidates: list[str] | tuple[str, ...]) -> bool:
        for prefix in prefix_candidates:
            normalized_prefix = self.normalize(prefix)
            needle = f"{normalized_prefix}_"
            for method_name in self.normalized_methods:
                if method_name.startswith(needle):
                    return True
        return False

    def normalize_legacy(self, name: str) -> str:
        if self.has_method(name):
            return name
        if "." in name:
            underscored = name.replace(".", "_")
            if self.has_method(underscored):
                return underscored
        return name

    def resolve_any(self, candidates: list[str]) -> str | None:
        for candidate in candidates:
            if self.has_method(candidate):
                return candidate
        for candidate in candidates:
            normalized = self.normalize(candidate)
            matches = self.normalized_methods.get(normalized, [])
            if matches:
                return sorted(matches, key=self._match_sort_key)[0]
        return None

    def list_methods(self, prefix: str) -> list[str]:
        normalized_prefix = self.normalize(prefix)
        needle = f"{normalized_prefix}_"
        found = {
            method
            for normalized, methods in self.normalized_methods.items()
            if normalized.startswith(needle)
            for method in methods
        }
        return sorted(found, key=self._match_sort_key)

    def dump_methods(self, prefix: str) -> list[str]:
        return self.list_methods(prefix)

    def closest_matches(self, name: str, limit: int = 5) -> list[str]:
        return get_close_matches(name, sorted(self.methods.keys()), n=limit, cutoff=0.35)

    def get_param_spec(self, method: str) -> list[dict[str, Any]]:
        meta = self.methods.get(method)
        if meta is None:
            resolved = self.resolve_any([method])
            if resolved:
                meta = self.methods.get(resolved)
        meta = meta or {}
        params = meta.get("params") if isinstance(meta, dict) else None
        if not isinstance(params, list):
            return []
        return [p for p in params if isinstance(p, dict)]

    def get_method_meta(self, method: str) -> dict[str, Any]:
        meta = self.methods.get(method)
        if meta is None:
            resolved = self.resolve_any([method])
            if resolved:
                meta = self.methods.get(resolved)
        return dict(meta or {})


class RpcTransportError(Exception):
    """Raised when the HTTP request itself fails (network, timeout, etc.)."""


class RpcResponseError(Exception):
    """Raised when the server returns a JSON-RPC error object."""

    def __init__(self, error: RpcError) -> None:
        super().__init__(str(error))
        self.rpc_error = error


class RpcParseError(Exception):
    """Raised when the response cannot be parsed as valid JSON-RPC 2.0."""


class RpcClient:
    """JSON-RPC 2.0 client targeting an Animica node HTTP endpoint.

    Parameters
    ----------
    url:
        Full HTTP/HTTPS URL of the RPC endpoint.
    connect_timeout:
        TCP connection timeout in seconds.
    read_timeout:
        Socket read timeout in seconds.
    max_retries:
        Maximum number of attempts (including the first) per call.
    """

    def __init__(
        self,
        url: str,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = _DEFAULT_READ_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_retries = max(1, max_retries)
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        if default_headers:
            self._session.headers.update({str(k): str(v) for k, v in default_headers.items() if str(k).strip() and str(v).strip()})

        # discover cache
        self._discover_cache: dict[str, Any] | None = None
        self._discover_ts: float = 0.0

        self._req_id = 0
        self._lock = threading.Lock()
        self._last_param_encoding_by_method = _PARAM_ENCODING_BY_URL.setdefault(self._url, {})
        self._resolved_methods = _RESOLVED_METHODS_BY_URL.setdefault(self._url, {})
        self._last_request_excerpt: dict[str, Any] = {}
        self._last_response_excerpt: dict[str, Any] = {}


    def set_header(self, name: str, value: str | None) -> None:
        key = str(name or "").strip()
        if not key:
            return
        if value is None or not str(value).strip():
            self._session.headers.pop(key, None)
            return
        self._session.headers[key] = str(value)
    # ------------------------------------------------------------------
    # Low-level call
    # ------------------------------------------------------------------

    def precache_method(self, method: str) -> None:
        """Pre-populate ``_resolved_methods`` so that :meth:`call` skips discovery.

        Use this when the method name is known and a discover call should not
        be made (e.g. in retry loops where each iteration must not consume an
        extra HTTP mock response).
        """
        cache_key = RpcRegistry.normalize(method)
        if cache_key not in self._resolved_methods:
            self._resolved_methods[cache_key] = method

    def call(
        self,
        method: str,
        params: list[Any] | dict[str, Any] | None = None,
        request_id: int | str | None = None,
    ) -> Any:
        """Perform a JSON-RPC 2.0 call and return the ``result`` value.

        Raises
        ------
        RpcTransportError
            On network failures.
        RpcResponseError
            When the server returns a JSON-RPC error object.
        RpcParseError
            When the response is not valid JSON-RPC 2.0.
        """
        if method == "rpc.discover":
            resolved_method = method
        elif method != "" and "." not in method and "_" not in method:
            resolved_method = method
        else:
            cache_key = RpcRegistry.normalize(method)
            resolved_method = self._resolved_methods.get(cache_key, method)
            if cache_key not in self._resolved_methods:
                # Not pre-cached: attempt registry-based normalization.
                try:
                    registry = self.registry()
                    if not registry.has_method(method):
                        resolved_method = self.resolve_method(method, [method])
                except Exception:
                    resolved_method = method
        rpc_params, param_type = self._encode_params(resolved_method, params)
        if request_id is None:
            with self._lock:
                self._req_id += 1
                request_id = self._req_id

        payload = {
            "jsonrpc": "2.0",
            "method": resolved_method,
            "id": request_id,
        }
        if rpc_params is not None:
            payload["params"] = rpc_params

        body = safe_json_dumps(payload)
        last_exc: Exception | None = None
        request_params = payload.get("params")
        self._last_request_excerpt = {
            "method": resolved_method,
            "param_type": param_type,
            "params": request_params,
            "params_len": len(request_params) if isinstance(request_params, (list, dict)) else 0,
        }

        for attempt in range(self._max_retries):
            if attempt > 0:
                backoff = _BASE_BACKOFF_S * (2 ** (attempt - 1)) + random.uniform(0, 0.1)
                log.debug("RpcClient: retry %d/%d in %.2fs for %s", attempt + 1, self._max_retries, backoff, resolved_method)
                time.sleep(backoff)

            try:
                resp = self._session.post(
                    self._url,
                    data=body,
                    timeout=(self._connect_timeout, self._read_timeout),
                )
            except requests.exceptions.Timeout as exc:
                last_exc = RpcTransportError(f"Request timed out: {exc}")
                log.warning("RpcClient: timeout on attempt %d: %s", attempt + 1, exc)
                continue
            except requests.exceptions.ConnectionError as exc:
                last_exc = RpcTransportError(f"Connection error: {exc}")
                log.warning("RpcClient: connection error on attempt %d: %s", attempt + 1, exc)
                continue
            except requests.exceptions.RequestException as exc:
                last_exc = RpcTransportError(f"Request error: {exc}")
                log.warning("RpcClient: request error on attempt %d: %s", attempt + 1, exc)
                continue

            # Retry on 5xx / 429
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = RpcTransportError(f"HTTP {resp.status_code}")
                log.warning("RpcClient: HTTP %d on attempt %d", resp.status_code, attempt + 1)
                continue

            # Parse JSON
            try:
                data: Any = resp.json()
            except ValueError as exc:
                last_exc = RpcParseError(f"Non-JSON response: {exc}")
                log.warning("RpcClient: JSON parse error on attempt %d: %s", attempt + 1, exc)
                continue

            # Validate JSON-RPC 2.0 envelope
            rpc_response = self._parse_response(data, resolved_method)
            self._last_response_excerpt = data if isinstance(data, dict) else {"raw": data}
            if rpc_response.error is not None:
                err = RpcResponseError(rpc_response.error)
                if self._should_retry_with_positional(err, params):
                    retry_params = self._params_from_dict(resolved_method, params if isinstance(params, dict) else {})
                    self._last_param_encoding_by_method[resolved_method] = "positional"
                    return self.call(resolved_method, retry_params, request_id=request_id)
                raise err
            self._last_param_encoding_by_method[resolved_method] = param_type
            return rpc_response.result

        raise (last_exc or RpcTransportError(f"All {self._max_retries} attempts failed for {resolved_method!r}"))

    def _should_retry_with_positional(self, exc: RpcResponseError, params: list[Any] | dict[str, Any] | None) -> bool:
        if not isinstance(params, dict):
            return False
        msg = (exc.rpc_error.message or "").lower()
        return exc.rpc_error.code == -32602 and "unexpected keyword argument" in msg and bool(params)

    def _params_from_dict(self, method: str, values: dict[str, Any]) -> list[Any]:
        spec = self.get_param_spec(method)
        if not spec:
            raise RpcParseError(f"Cannot convert object params to positional for {method}: missing OpenRPC params[] spec")
        ordered: list[Any] = []
        for p in spec:
            name = p.get("name")
            if isinstance(name, str) and name in values:
                ordered.append(values[name])
            elif p.get("required"):
                raise RpcParseError(f"Missing required param {name!r} for method {method}")
        return ordered

    def _encode_params(self, method: str, params: list[Any] | dict[str, Any] | None) -> tuple[list[Any] | dict[str, Any] | None, str]:
        if isinstance(params, list):
            return params, "positional"
        if params is None:
            return None, "none"
        cached = self._last_param_encoding_by_method.get(method)
        if cached == "object":
            return params, "object"
        spec = self.get_param_spec(method)
        meta = self.registry().get_method_meta(method) if method else {}
        param_structure = str(meta.get("param_structure") or "unknown")
        if spec and param_structure == "positional":
            return self._params_from_dict(method, params), "positional"
        # Unknown/empty schema must not block object-params calls (e.g. da.configure).
        return params, "object"

    def _parse_response(self, data: Any, method: str) -> RpcResponse[Any]:
        if not isinstance(data, dict):
            raise RpcParseError(f"Expected JSON object, got {type(data).__name__} for method {method!r}")
        if data.get("jsonrpc") != "2.0":
            raise RpcParseError(
                f"Missing or incorrect jsonrpc version in response for {method!r}: {data.get('jsonrpc')!r}"
            )
        if "error" in data and data["error"] is not None:
            err = data["error"]
            if not isinstance(err, dict):
                raise RpcParseError(f"Malformed error object for {method!r}: {err!r}")
            rpc_err = RpcError(
                code=int(err.get("code", 0)),
                message=str(err.get("message", "")),
                data=err.get("data"),
            )
            return RpcResponse(id=data.get("id"), error=rpc_err, raw=data)
        if "result" not in data:
            raise RpcParseError(f"Response missing both 'result' and 'error' for {method!r}")
        return RpcResponse(id=data.get("id"), result=data["result"], raw=data)

    # ------------------------------------------------------------------
    # High-level methods
    # ------------------------------------------------------------------

    def discover(self) -> dict[str, Any]:
        """Call ``rpc.discover`` and return the methods description dict."""
        return self.registry().payload

    def registry(self) -> RpcRegistry:
        """Return cached :class:`RpcRegistry` for this RPC URL."""
        now = time.time()
        with _DISCOVER_CACHE_LOCK:
            cached = _DISCOVER_CACHE_BY_URL.get(self._url)
            if cached is not None:
                ts, payload, cached_registry = cached
                if (now - ts) < _DISCOVER_CACHE_TTL_S:
                    self._discover_cache = payload
                    self._discover_ts = ts
                    return cached_registry

        discover_client = RpcClient(self._url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
        try:
            result = discover_client.call("rpc.discover")
        finally:
            discover_client.close()
        if not isinstance(result, dict):
            result = {"raw": result}
        self._discover_cache = result
        self._discover_ts = now
        registry = RpcRegistry(result)
        with _DISCOVER_CACHE_LOCK:
            _DISCOVER_CACHE_BY_URL[self._url] = (now, result, registry)
        log.debug("RpcClient: discover cache updated for %s", self._url)
        return registry

    def _known_methods(self) -> set[str]:
        """Return the set of method names from the cached/fetched discover result.

        Falls back to empty set on any error (caller falls back to defaults).
        """
        try:
            return self.registry().method_names
        except Exception:  # noqa: BLE001
            return set()

    def resolve_method(self, requested: str, candidates: list[str] | None = None) -> str:
        """Resolve *requested* against registry, raising -32601-style error if missing."""
        names = candidates or [requested]
        cache_key = RpcRegistry.normalize(requested)
        cached_resolved = self._resolved_methods.get(cache_key)
        if cached_resolved:
            return cached_resolved
        try:
            registry = self.registry()
        except Exception:
            # Discovery unavailable: keep client functional with deterministic fallback.
            resolved = requested.replace(".", "_") if "." in requested else requested
            self._resolved_methods[cache_key] = resolved
            return resolved
        if not registry.method_names:
            resolved = requested.replace(".", "_") if "." in requested else requested
            self._resolved_methods[cache_key] = resolved
            return resolved
        resolved = registry.resolve_any(names)
        if resolved:
            self._resolved_methods[cache_key] = resolved
            return resolved
        normalized = registry.normalize_legacy(requested)
        if registry.has_method(normalized):
            self._resolved_methods[cache_key] = normalized
            return normalized
        suggestions: list[str] = []
        for name in names:
            suggestions.extend(registry.closest_matches(name))
        if not suggestions:
            suggestions = registry.closest_matches(requested)
        uniq_suggestions = list(dict.fromkeys(suggestions))
        raise RpcResponseError(
            RpcError(
                code=-32601,
                message=f"Method not found: {requested}",
                data={"requested": requested, "did_you_mean": uniq_suggestions},
            )
        )

    def resolve_operation_method(self, operation: str, *, extra_candidates: tuple[str, ...] = ()) -> str:
        candidates = list(_RPC_OPERATION_CANDIDATES.get(operation, ())) + list(extra_candidates)
        if not candidates:
            raise RpcParseError(f"Unknown RPC operation {operation!r}")
        resolved = self.resolve_method(candidates[0], candidates)
        self._resolved_methods[RpcRegistry.normalize(operation)] = resolved
        return resolved

    def get_param_spec(self, method: str) -> list[dict[str, Any]]:
        try:
            return self.registry().get_param_spec(method)
        except Exception:
            return []

    def _build_params_from_schema(self, method: str, values: dict[str, Any] | None) -> tuple[list[Any] | dict[str, Any] | None, str]:
        values = values or {}
        spec = self.get_param_spec(method)
        meta = self.registry().get_method_meta(method)
        param_structure = str(meta.get("param_structure") or "unknown")
        if not spec or param_structure != "positional":
            if values:
                return values, "object"
            return None, "none"
        ordered: list[Any] = []
        for p in spec:
            name = p.get("name")
            if not isinstance(name, str) or not name:
                continue
            if name in values:
                ordered.append(values[name])
            elif p.get("required"):
                raise RpcParseError(f"Missing required param {name!r} for method {method}")
        return ordered, "positional"

    def call_with_schema(
        self,
        method: str,
        values: dict[str, Any] | None = None,
        *,
        allow_object_fallback: bool = True,
    ) -> Any:
        params, encoding = self._build_params_from_schema(method, values)
        try:
            result = self.call(method, params)
            self._last_param_encoding_by_method[method] = encoding
            return result
        except RpcResponseError as exc:
            msg = (exc.rpc_error.message or "").lower()
            if (
                encoding == "object"
                and allow_object_fallback
                and exc.rpc_error.code == -32602
                and "unexpected keyword argument" in msg
            ):
                # Retry with positional args using schema order.
                spec = self.get_param_spec(method)
                positional: list[Any] = []
                for p in spec:
                    name = p.get("name")
                    if isinstance(name, str) and name in (values or {}):
                        positional.append((values or {})[name])
                result = self.call(method, positional)
                self._last_param_encoding_by_method[method] = "positional(retry)"
                return result
            raise

    def call_operation(
        self,
        operation: str,
        params: list[Any] | dict[str, Any] | None = None,
        *,
        extra_candidates: tuple[str, ...] = (),
    ) -> Any:
        method = self.resolve_operation_method(operation, extra_candidates=extra_candidates)
        return self.call(method, params)

    def _pick_method(self, *candidates: str) -> str:
        """Return first candidate present in discover list; fallback to first candidate."""
        if not candidates:
            raise RpcParseError("No method candidates provided")
        return self.resolve_method(candidates[0], list(candidates))

    def rpc_diagnostics(self, prefixes: tuple[str, ...] = ("chain", "tx", "da", "aicf")) -> dict[str, Any]:
        registry = self.registry()
        methods = sorted(
            m
            for m in registry.method_names
            if not prefixes or any(m.startswith(f"{prefix}_") or m.startswith(f"{prefix}.") for prefix in prefixes)
        )
        return {
            "rpc_url": self._url,
            "discover_info": registry.server_info,
            "method_count": len(registry.method_names),
            "methods": methods,
            "resolved_methods": dict(self._resolved_methods),
            "param_encoding": dict(self._last_param_encoding_by_method),
            "last_request_excerpt": self._last_request_excerpt,
            "last_response_excerpt": self._last_response_excerpt,
        }

    def get_head(self) -> Head:
        """Return the latest chain head.

        Tries ``chain_getHead`` first, then ``chain.getHead``.
        """
        method = self._pick_method(*_RPC_OPERATION_CANDIDATES["GET_HEAD"])
        result = self.call(method)
        if not isinstance(result, dict):
            raise RpcParseError(f"Expected dict from {method}, got {type(result).__name__}")
        return Head.from_dict(result)

    def get_balance(self, address: str) -> int:
        """Return the balance (as integer) for *address*.

        Tries ``state_getBalance``, then ``state.getBalance``.

        Some Animica node versions require named-object params instead of
        positional-list params. We probe both styles before failing.
        """
        methods = list(_RPC_OPERATION_CANDIDATES["GET_BALANCE"])
        chosen = self._pick_method(*methods)
        attempts: list[tuple[str, list[Any] | dict[str, Any]]] = [
            (chosen, [address]),
            (chosen, {"address": address}),
        ]
        if chosen != methods[0]:
            attempts.extend(
                [
                    (methods[0], [address]),
                    (methods[0], {"address": address}),
                ]
            )

        last_exc: Exception | None = None
        for method, params in attempts:
            try:
                result = self.call(method, params)
                if isinstance(result, dict):
                    for key in ("balance", "amount", "value"):
                        if key in result:
                            result = result[key]
                            break
                return parse_hex_quantity(result, "balance")
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RpcParseError("Unable to fetch balance from RPC")

    def get_pending_nonce(self, address: str) -> int:
        """Return the pending nonce for *address*.

        Tries ``state_getPendingNonce``, then ``state.getPendingNonce``.
        """
        method = self._pick_method(*_RPC_OPERATION_CANDIDATES["GET_PENDING_NONCE"])
        result = self.call(method, [address])
        return parse_hex_quantity(result, "nonce")

    def send_raw_tx(self, raw_tx_hex: str) -> str:
        """Broadcast a raw signed transaction and return the tx hash.

        Tries ``tx_sendRawTransaction``, ``tx.sendRawTransaction``,
        ``tx_submitRawTransaction`` in that order.

        Raises
        ------
        TypeError
            If *raw_tx_hex* is not a ``str`` (guards against ``[object Object]``
            errors that occur when a dict is accidentally passed as the raw tx).
        """
        # Guard: prevent non-string params from producing -32602 / "[object Object]" RPC errors
        if not isinstance(raw_tx_hex, str):
            raise TypeError(
                f"raw_tx_hex must be a hex str (e.g. '0x…'), got {type(raw_tx_hex).__name__}"
            )
        method = self._pick_method(*_RPC_OPERATION_CANDIDATES["SEND_RAW_TX"])
        result = self.call(method, [raw_tx_hex])
        if not isinstance(result, str):
            raise RpcParseError(f"Expected str tx hash from {method}, got {type(result).__name__}")
        return result

    def get_chain_id(self) -> int:
        """Return the node's chain ID.

        Tries the following methods in order, using discovery to pick the best one:

        1. ``chain_getChainId``
        2. ``chain.getChainId``
        3. ``eth_chainId`` (returns hex-encoded integer)

        Raises
        ------
        RpcTransportError
            On network failures.
        RpcResponseError
            When the server returns a JSON-RPC error.
        RpcParseError
            When the chain ID cannot be parsed.
        """
        from animica_studio.models.rpc_models import parse_hex_quantity  # noqa: PLC0415

        method = self._pick_method(*_RPC_OPERATION_CANDIDATES["GET_CHAIN_ID"])
        result = self.call(method)
        # Integers are returned directly; hex strings come from eth_chainId
        if isinstance(result, int):
            return result
        if isinstance(result, str):
            try:
                return parse_hex_quantity(result, "chain_id")
            except ValueError as exc:
                raise RpcParseError(f"Cannot parse chain_id from {result!r}: {exc}") from exc
        raise RpcParseError(f"Unexpected chain_id result type {type(result).__name__}: {result!r}")

    def ping_details(self) -> dict[str, Any]:
        """Return structured ping diagnostics suitable for background workers."""
        details: dict[str, Any] = {
            "ok": False,
            "method": "",
            "head_number": None,
            "head_hash": None,
            "error": "",
            "exception": "",
        }
        method = self._pick_method(*_RPC_OPERATION_CANDIDATES["GET_HEAD"])
        details["method"] = method
        try:
            result = self.call(method)
            if isinstance(result, dict):
                details["ok"] = True
                details["head_number"] = result.get("number")
                details["head_hash"] = result.get("hash")
            else:
                details["error"] = f"Unexpected result type: {type(result).__name__}"
        except Exception as exc:  # noqa: BLE001
            details["error"] = str(exc)
            details["exception"] = exc.__class__.__name__
        return details

    def ping(self) -> bool:
        """Attempt a lightweight RPC call to check if the node is reachable."""
        return bool(self.ping_details().get("ok", False))

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "RpcClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
