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
import time
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
_DISCOVER_CACHE_TTL_S = 60.0


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
    ) -> None:
        self._url = url
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_retries = max(1, max_retries)
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

        # discover cache
        self._discover_cache: dict[str, Any] | None = None
        self._discover_ts: float = 0.0

        self._req_id = 0

    # ------------------------------------------------------------------
    # Low-level call
    # ------------------------------------------------------------------

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
        if request_id is None:
            self._req_id += 1
            request_id = self._req_id

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": request_id,
        }
        if params is not None:
            payload["params"] = params

        body = safe_json_dumps(payload)
        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            if attempt > 0:
                backoff = _BASE_BACKOFF_S * (2 ** (attempt - 1)) + random.uniform(0, 0.1)
                log.debug("RpcClient: retry %d/%d in %.2fs for %s", attempt + 1, self._max_retries, backoff, method)
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
            rpc_response = self._parse_response(data, method)
            if rpc_response.error is not None:
                raise RpcResponseError(rpc_response.error)
            return rpc_response.result

        raise (last_exc or RpcTransportError(f"All {self._max_retries} attempts failed for {method!r}"))

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
        """Call ``rpc.discover`` and return the methods description dict.

        Results are cached for :attr:`_DISCOVER_CACHE_TTL_S` seconds.
        """
        now = time.time()
        if self._discover_cache is not None and (now - self._discover_ts) < _DISCOVER_CACHE_TTL_S:
            return self._discover_cache

        result = self.call("rpc.discover")
        if not isinstance(result, dict):
            result = {"raw": result}
        self._discover_cache = result
        self._discover_ts = now
        log.debug("RpcClient: discover cache updated")
        return result

    def _known_methods(self) -> set[str]:
        """Return the set of method names from the cached/fetched discover result.

        Falls back to empty set on any error (caller falls back to defaults).
        """
        try:
            disc = self.discover()
            methods_raw = disc.get("methods", [])
            if isinstance(methods_raw, list):
                return {m.get("name", "") if isinstance(m, dict) else str(m) for m in methods_raw}
        except Exception:  # noqa: BLE001
            pass
        return set()

    def _pick_method(self, *candidates: str) -> str:
        """Return the first candidate found in the discover methods list.

        Falls back to the first candidate if discovery fails or has no match.
        """
        known = self._known_methods()
        if known:
            for c in candidates:
                if c in known:
                    return c
        return candidates[0]

    def get_head(self) -> Head:
        """Return the latest chain head.

        Tries ``chain_getHead`` first, then ``chain.getHead``.
        """
        method = self._pick_method("chain_getHead", "chain.getHead")
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
        methods = ["state_getBalance", "state.getBalance", "wallet_getBalance", "wallet.getBalance"]
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
        method = self._pick_method("state_getPendingNonce", "state.getPendingNonce")
        result = self.call(method, [address])
        return parse_hex_quantity(result, "nonce")

    def send_raw_tx(self, raw_tx_hex: str) -> str:
        """Broadcast a raw signed transaction and return the tx hash.

        Tries ``tx_sendRawTransaction``, ``tx.sendRawTransaction``,
        ``tx_submitRawTransaction`` in that order.
        """
        method = self._pick_method(
            "tx_sendRawTransaction",
            "tx.sendRawTransaction",
            "tx_submitRawTransaction",
        )
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

        method = self._pick_method("chain_getChainId", "chain.getChainId", "eth_chainId")
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

    def ping(self) -> bool:
        """Attempt a lightweight RPC call to check if the node is reachable.

        Returns ``True`` on success, ``False`` on any error.
        """
        try:
            self.get_head()
            return True
        except Exception:  # noqa: BLE001
            pass
        # Fallback: try discover
        try:
            self.discover()
            return True
        except Exception:  # noqa: BLE001
            return False

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
