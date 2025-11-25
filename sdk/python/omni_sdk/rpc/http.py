from __future__ import annotations

"""
HTTP JSON-RPC client (sync).

- Uses httpx if available (preferred), otherwise falls back to requests.
- Minimal dependencies, friendly to unit tests and mocks.
- Retries idempotent RPC calls on transient transport failures and 5xx HTTP.

Example:
    from omni_sdk.rpc.http import RpcClient
    rpc = RpcClient("http://localhost:8545")
    head = rpc.request("chain_getHead")
    print(head["height"])
"""

import json
import os
import random
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Union

try:  # preferred
    import httpx  # type: ignore
    _HAVE_HTTPX = True
except Exception:  # pragma: no cover
    httpx = None  # type: ignore
    _HAVE_HTTPX = False

try:  # fallback
    import requests  # type: ignore
    _HAVE_REQUESTS = True
except Exception:  # pragma: no cover
    requests = None  # type: ignore
    _HAVE_REQUESTS = False

from ..version import __version__ as SDK_VERSION  # type: ignore
from ..errors import RpcError  # type: ignore


JSON = Union[dict, list, str, int, float, bool, None]
Params = Union[Sequence[Any], Mapping[str, Any], None]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_retriable_http(status: int) -> bool:
    # Typical transient HTTP statuses: 429/502/503/504
    return status in (429, 502, 503, 504)


def _jitter_backoff(base: float, factor: float, attempt: int, jitter: float) -> float:
    # Exponential backoff with jitter in [0, jitter]
    return base * (factor ** max(attempt - 1, 0)) + random.random() * jitter


@dataclass
class RpcClient:
    """Synchronous JSON-RPC 2.0 client over HTTP."""

    url: str
    timeout: float = 30.0
    max_retries: int = 3
    backoff_base: float = 0.15
    backoff_factor: float = 1.8
    backoff_jitter: float = 0.2
    headers: Optional[Mapping[str, str]] = None
    _id_counter: Iterable[int] = field(default_factory=lambda: count(start=_now_ms()))
    _client: Any = field(init=False, default=None)
    _use_httpx: bool = field(init=False, default=_HAVE_HTTPX)

    def __post_init__(self) -> None:
        ua = f"omni-sdk-python/{SDK_VERSION}"
        merged_headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ua,
        }
        if self.headers:
            merged_headers.update(dict(self.headers))

        if self._use_httpx:
            if not _HAVE_HTTPX:  # pragma: no cover - defensive
                raise RuntimeError("httpx not available")
            self._client = httpx.Client(
                timeout=self.timeout,
                headers=merged_headers,
            )
        else:
            if not _HAVE_REQUESTS:
                raise RuntimeError("Neither httpx nor requests is available")
            self._client = requests.Session()
            self._client.headers.update(merged_headers)

    # --- context manager -------------------------------------------------

    def __enter__(self) -> "RpcClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass

    # --- public API ------------------------------------------------------

    def request(self, method: str, params: Params = None, *, id: Optional[Union[int, str]] = None) -> JSON:
        """Perform a single JSON-RPC request and return `result` or raise RpcError."""
        payload = self._make_payload(method, params, id)
        resp = self._send_with_retries(payload)
        return self._handle_response(resp)

    def batch(self, calls: Sequence[Tuple[str, Params]]) -> List[JSON]:
        """Perform a JSON-RPC batch; returns list of results in the same order as `calls`."""
        batch_payload: List[Dict[str, Any]] = []
        id_list: List[int] = []
        for method, params in calls:
            p = self._make_payload(method, params)
            batch_payload.append(p)
            id_list.append(p["id"])
        resp = self._send_with_retries(batch_payload)
        # Response is an array of objects with id/result or id/error (order not guaranteed)
        if not isinstance(resp, list):
            raise RpcError(code=-32603, message="Invalid batch response (not a list)", data=resp)

        by_id: Dict[int, JSON] = {}
        for item in resp:
            if not isinstance(item, dict) or "id" not in item:
                raise RpcError(code=-32603, message="Malformed item in batch response", data=item)
            rid = item["id"]
            if "error" in item:
                err = item["error"]
                raise RpcError(code=err.get("code", -32603), message=err.get("message", "Unknown error"), data=err.get("data"))
            by_id[int(rid)] = item.get("result")

        ordered: List[JSON] = []
        for rid in id_list:
            if rid not in by_id:
                raise RpcError(code=-32603, message=f"Missing result for id {rid}", data=resp)
            ordered.append(by_id[rid])
        return ordered

    # --- internals -------------------------------------------------------

    def _make_payload(self, method: str, params: Params, id: Optional[Union[int, str]] = None) -> Dict[str, Any]:
        if id is None:
            id = next(self._id_counter)
        if params is None:
            params = []
        elif isinstance(params, Mapping):
            params = dict(params)
        elif isinstance(params, Sequence) and not isinstance(params, (str, bytes, bytearray)):
            params = list(params)
        else:
            # Coerce single param into positional list
            params = [params]  # type: ignore[list-item]
        return {"jsonrpc": "2.0", "id": id, "method": method, "params": params}

    def _send_with_retries(self, payload: Union[Dict[str, Any], List[Dict[str, Any]]]) -> JSON:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 2):  # N retries -> N+1 attempts
            try:
                return self._send_once(payload)
            except RpcError as e:
                # Server indicated an application error: do not retry
                raise e
            except Exception as e:  # network/timeout/protocol errors
                last_exc = e
                if attempt > self.max_retries:
                    break
                # Backoff
                delay = _jitter_backoff(self.backoff_base, self.backoff_factor, attempt, self.backoff_jitter)
                time.sleep(delay)
        # If we fall through, raise a generic RpcError wrapping last_exc
        raise RpcError(code=-32098, message="RPC transport failed", data=str(last_exc))

    def _send_once(self, payload: Union[Dict[str, Any], List[Dict[str, Any]]]) -> JSON:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if self._use_httpx:
            assert _HAVE_HTTPX
            try:
                r = self._client.post(self.url, content=body)
            except (httpx.TimeoutException, httpx.NetworkError) as e:  # type: ignore[attr-defined]
                raise RpcError(code=-32098, message="Network error", data=str(e))
            if _is_retriable_http(r.status_code):
                raise RuntimeError(f"HTTP {r.status_code}")
            # Avoid httpx.raise_for_status() to keep error body visible below
            try:
                resp = r.json()
            except Exception as e:
                raise RpcError(code=-32603, message="Non-JSON response from RPC", data=f"HTTP {r.status_code}: {r.text[:256]}") from e
        else:
            assert _HAVE_REQUESTS
            try:
                r = self._client.post(self.url, data=body, timeout=self.timeout)
            except requests.exceptions.RequestException as e:  # type: ignore[attr-defined]
                raise RpcError(code=-32098, message="Network error", data=str(e))
            if _is_retriable_http(r.status_code):
                raise RuntimeError(f"HTTP {r.status_code}")
            try:
                resp = r.json()
            except Exception as e:
                raise RpcError(code=-32603, message="Non-JSON response from RPC", data=f"HTTP {r.status_code}: {r.text[:256]}") from e

        # Single vs batch
        if isinstance(resp, dict):
            if "error" in resp:
                err = resp["error"] or {}
                raise RpcError(code=err.get("code", -32603), message=err.get("message", "Unknown error"), data=err.get("data"))
            if "result" not in resp:
                raise RpcError(code=-32603, message="Malformed JSON-RPC response", data=resp)
            return resp["result"]
        elif isinstance(resp, list):
            # Batch returns list-of-objects; let caller handle structure.
            return resp
        else:
            raise RpcError(code=-32603, message="Invalid JSON-RPC response type", data=type(resp).__name__)


__all__ = ["RpcClient"]
