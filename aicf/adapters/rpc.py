from __future__ import annotations

"""
aicf.adapters.rpc
=================

Minimal JSON-RPC + WS adapter used by AICF to:
- Query node heights/headers (for scheduling, epoch boundaries, randomness beacons, etc.)
- Emit websocket events to the node's WS broadcaster (jobAssigned, jobCompleted, etc.)

Design notes
------------
* Pure stdlib HTTP client (urllib) to avoid hard deps; callers may wrap/replace.
* JSON-RPC 2.0 with small conveniences (timeout, retries, method aliases).
* WS emission is DI-based: provide a callable `ws_emit(event: str, payload: dict)`.
  If omitted, emission becomes a no-op (safe for tests).

Expected JSON-RPC methods (may vary by deployment — configurable):
- "chain.getHead"            -> {"height": int, "hash": "0x...", "time": int, ...}
- "chain.getHeaderByHeight"  -> {<header fields>}
- "chain.getHeaderByHash"    -> {<header fields>}
If your node uses different names (e.g. "chain_head"), pass a method map.
"""

import json
import logging
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from aicf.errors import AICFError

logger = logging.getLogger(__name__)


# ---- Errors --------------------------------------------------------------------

class RPCError(AICFError):
    """Raised when the node RPC returns an error or cannot be reached."""


# ---- Client --------------------------------------------------------------------

@dataclass
class RpcMethodMap:
    get_head: str = "chain.getHead"
    header_by_height: str = "chain.getHeaderByHeight"
    header_by_hash: str = "chain.getHeaderByHash"


class JsonRpcClient:
    """
    Tiny JSON-RPC 2.0 client using stdlib urllib with optional retries.
    """

    def __init__(
        self,
        url: str,
        timeout: float = 5.0,
        headers: Optional[Dict[str, str]] = None,
        retries: int = 1,
        backoff_sec: float = 0.25,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if headers:
            self.headers.update(headers)
        self._id = 0
        self.retries = max(0, retries)
        self.backoff_sec = max(0.0, backoff_sec)

    def call(self, method: str, params: Optional[Sequence[Any]] = None) -> Any:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": list(params or []),
        }
        body = json.dumps(payload).encode("utf-8")

        last_err: Optional[Exception] = None
        attempts = 1 + self.retries
        for attempt in range(attempts):
            try:
                req = urllib.request.Request(self.url, data=body, headers=self.headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                obj = json.loads(data.decode("utf-8"))
                if "error" in obj and obj["error"]:
                    raise RPCError(f"RPC error {obj['error']}")
                return obj.get("result")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
                if attempt < attempts - 1 and self.backoff_sec > 0:
                    time.sleep(self.backoff_sec * (2 ** attempt))
                else:
                    break
        raise RPCError(f"RPC call failed after {attempts} attempt(s): {last_err}")


# ---- Adapter -------------------------------------------------------------------

class NodeRPCAdapter:
    """
    High-level helper around JsonRpcClient for common chain queries & WS emissions.

    Example:
        rpc = NodeRPCAdapter(
            rpc_url="http://127.0.0.1:8545",
            ws_emit=my_ws_broadcast,  # Callable[[str, dict], None]
        )
        height = rpc.get_height()
        head = rpc.get_head()
        hdr_100 = rpc.get_header_by_height(100)
        rpc.emit_job_assigned(job_id, provider_id, lease_id, height)

    WS Emission
    -----------
    The adapter doesn't open WS connections; instead it accepts a broadcaster
    callable (e.g., your FastAPI/WebSocket manager) that will fan out messages
    to connected clients. The event naming matches `aicf.rpc.ws` conventions:
      - "jobAssigned"
      - "jobCompleted"
      - "providerSlashed"
      - "epochSettled"
    """

    def __init__(
        self,
        rpc_url: str,
        *,
        timeout_sec: float = 5.0,
        retries: int = 1,
        headers: Optional[Dict[str, str]] = None,
        method_map: Optional[RpcMethodMap] = None,
        ws_emit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.client = JsonRpcClient(rpc_url, timeout=timeout_sec, headers=headers, retries=retries)
        self.methods = method_map or RpcMethodMap()
        # Broadcasting callable; if None, becomes a no-op
        self._ws_emit = ws_emit

    # ---- Chain queries --------------------------------------------------------

    def get_head(self) -> Dict[str, Any]:
        """
        Return the current head object. Must include at least {"height": int}.
        """
        try:
            head = self.client.call(self.methods.get_head)
        except RPCError as e:
            # Some deployments may expose "chain_head"
            logger.debug("get_head failed with %s; trying alias 'chain_head'", e)
            try:
                head = self.client.call("chain_head")
            except Exception as e2:
                raise RPCError(f"get_head failed: {e2}") from e
        if not isinstance(head, dict) or "height" not in head:
            raise RPCError(f"unexpected head format: {head!r}")
        return head

    def get_height(self) -> int:
        head = self.get_head()
        h = head.get("height")
        if not isinstance(h, int):
            raise RPCError(f"unexpected height type: {type(h)}")
        return h

    def get_header_by_height(self, height: int) -> Dict[str, Any]:
        res = self.client.call(self.methods.header_by_height, [int(height)])
        if not isinstance(res, dict):
            raise RPCError(f"unexpected header format (height={height}): {res!r}")
        return res

    def get_header_by_hash(self, block_hash: str) -> Dict[str, Any]:
        res = self.client.call(self.methods.header_by_hash, [block_hash])
        if not isinstance(res, dict):
            raise RPCError(f"unexpected header format (hash={block_hash}): {res!r}")
        return res

    # ---- WS Event emission ----------------------------------------------------

    def emit(self, event: str, payload: Dict[str, Any]) -> None:
        """
        Generic emitter. If no broadcaster is configured, this is a no-op.
        """
        if self._ws_emit is None:
            logger.debug("WS emit skipped (no broadcaster): %s %s", event, payload)
            return
        try:
            self._ws_emit(event, payload)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("WS emit failed: %s (%s)", event, e)

    # Convenience wrappers that align with aicf.rpc.ws topics

    def emit_job_assigned(
        self,
        job_id: str,
        provider_id: str,
        lease_id: str,
        height: Optional[int] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.emit(
            "jobAssigned",
            {
                "jobId": job_id,
                "providerId": provider_id,
                "leaseId": lease_id,
                "height": height,
                **(extras or {}),
            },
        )

    def emit_job_completed(
        self,
        job_id: str,
        provider_id: str,
        success: bool,
        digest: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None,
        height: Optional[int] = None,
    ) -> None:
        payload = {
            "jobId": job_id,
            "providerId": provider_id,
            "success": bool(success),
            "height": height,
        }
        if digest is not None:
            payload["digest"] = digest
        if metrics:
            payload["metrics"] = metrics
        self.emit("jobCompleted", payload)

    def emit_provider_slashed(
        self,
        provider_id: str,
        reason_code: str,
        penalty: Optional[int] = None,
        height: Optional[int] = None,
    ) -> None:
        payload = {
            "providerId": provider_id,
            "reason": reason_code,
            "height": height,
        }
        if penalty is not None:
            payload["penalty"] = int(penalty)
        self.emit("providerSlashed", payload)

    def emit_epoch_settled(
        self,
        epoch_index: int,
        payouts_count: int,
        total_amount: int,
        height: Optional[int] = None,
    ) -> None:
        self.emit(
            "epochSettled",
            {
                "epoch": int(epoch_index),
                "payouts": int(payouts_count),
                "amount": int(total_amount),
                "height": height,
            },
        )


__all__ = [
    "RPCError",
    "RpcMethodMap",
    "JsonRpcClient",
    "NodeRPCAdapter",
]
