"""
Thin RPC/WS helpers for tests
=============================

- HttpRpcClient: minimal JSON-RPC over HTTP(S) with retries, batch, and helpers.
- WsRpcClient / WsSubscription: async WebSocket subscriptions with auto-reconnect.
- Utility routines commonly used in tests: await_receipt, get_chain_id, etc.

Only standard libraries + 'httpx' and 'websockets' are required (see tests/requirements.txt).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import (Any, AsyncIterator, Dict, Iterable, List, Optional,
                    Sequence, Tuple, Union)

try:
    import httpx
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "tests/harness/clients.py requires 'httpx'. Is tests/requirements.txt installed?"
    ) from e

try:
    import websockets
    from websockets.client import connect as ws_connect
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "tests/harness/clients.py requires 'websockets'. Is tests/requirements.txt installed?"
    ) from e

# Optional shared helpers if tests/harness/__init__.py exists
try:  # pragma: no cover - optional import with graceful fallback
    from tests.harness import (DEFAULT_HTTP_TIMEOUT, DEFAULT_WS_TIMEOUT,
                               get_logger)
except Exception:  # pragma: no cover
    import logging

    def get_logger(name: str = "tests.harness.clients"):
        logger = logging.getLogger(name)
        if not logger.handlers:
            h = logging.StreamHandler()
            h.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
            logger.addHandler(h)
        logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
        return logger

    DEFAULT_HTTP_TIMEOUT = 30.0
    DEFAULT_WS_TIMEOUT = 30.0


LOG = get_logger("tests.harness.clients")


# ------------------------------ Errors ---------------------------------


@dataclass
class JsonRpcError(Exception):
    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        s = f"JSON-RPC error {self.code}: {self.message}"
        if self.data is not None:
            s += f" | data={self.data!r}"
        return s


# ------------------------------ HTTP JSON-RPC ---------------------------


class HttpRpcClient:
    """
    Minimal JSON-RPC client with retries and batching.

    Example:
        rpc = HttpRpcClient("http://127.0.0.1:8545")
        chain_id = rpc.call_first(["omni_chainId", "eth_chainId"])
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        retries: int = 3,
        backoff: float = 0.2,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = max(0.0, backoff)
        self._id = 0
        self._headers = {"content-type": "application/json"}
        if headers:
            self._headers.update(headers)
        self._client = httpx.Client(timeout=self.timeout)

    # ----- Core -----

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post_json(
        self, payload: Union[Dict[str, Any], List[Dict[str, Any]]]
    ) -> httpx.Response:
        url = self.base_url
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                return self._client.post(url, json=payload, headers=self._headers)
            except Exception as e:  # network error
                last_exc = e
                if attempt >= self.retries:
                    raise
                time.sleep(self.backoff * (2**attempt))
        # Should not reach here
        assert last_exc is not None
        raise last_exc

    def call(
        self, method: str, params: Optional[Union[List[Any], Dict[str, Any]]] = None
    ) -> Any:
        """
        Single JSON-RPC call; raises JsonRpcError on RPC-side errors.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or [],
        }
        resp = self._post_json(payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            err = body["error"]
            raise JsonRpcError(
                err.get("code", -32000),
                err.get("message", "Unknown error"),
                err.get("data"),
            )
        return body.get("result")

    def call_first(
        self,
        methods: Sequence[str],
        params: Optional[Union[List[Any], Dict[str, Any]]] = None,
    ) -> Any:
        """
        Try multiple methods in order; return the first successful result.
        Useful when nodes expose 'omni_*' OR 'eth_*' style methods.
        """
        last_err: Optional[Exception] = None
        for m in methods:
            try:
                return self.call(m, params)
            except Exception as e:
                last_err = e
        assert last_err is not None
        raise last_err

    def batch(
        self,
        calls: Iterable[Tuple[str, Optional[Union[List[Any], Dict[str, Any]]]]],
        *,
        raise_on_error: bool = False,
    ) -> List[Any]:
        """
        Execute a batch of RPCs. Returns results in same order.
        If raise_on_error=False, items with errors are returned as JsonRpcError instances.
        """
        payload: List[Dict[str, Any]] = []
        ids: List[int] = []
        for method, params in calls:
            rid = self._next_id()
            ids.append(rid)
            payload.append(
                {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or []}
            )

        resp = self._post_json(payload)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, list):
            raise RuntimeError(f"Batch response was not a list: {type(body)}")
        # Map by id
        by_id = {item["id"]: item for item in body}
        results: List[Any] = []
        for rid in ids:
            item = by_id.get(rid)
            if not item:
                err = JsonRpcError(-32603, "Missing batch item", {"id": rid})
                if raise_on_error:
                    raise err
                results.append(err)
                continue
            if "error" in item:
                e = item["error"]
                err = JsonRpcError(
                    e.get("code", -32000),
                    e.get("message", "Unknown error"),
                    e.get("data"),
                )
                if raise_on_error:
                    raise err
                results.append(err)
            else:
                results.append(item.get("result"))
        return results

    # ----- Common helpers -----

    def get_chain_id(self) -> Optional[int]:
        try:
            r = self.call_first(["omni_chainId", "eth_chainId"])
            # Could be hex or decimal
            if isinstance(r, str):
                if r.startswith("0x"):
                    return int(r, 16)
                return int(r)
            return int(r)
        except Exception:
            return None

    def get_block_by_number(
        self, number: Union[int, str] = "latest", full: bool = False
    ) -> Any:
        if isinstance(number, int):
            number_hex = hex(number)
        else:
            number_hex = number  # "latest", or already hex
        # Try omni_* then eth_*
        try:
            return self.call("omni_getBlockByNumber", [number_hex, bool(full)])
        except Exception:
            return self.call("eth_getBlockByNumber", [number_hex, bool(full)])

    def send_raw_transaction(self, raw_tx_hex: str) -> str:
        try:
            return self.call("omni_sendRawTransaction", [raw_tx_hex])
        except Exception:
            return self.call("eth_sendRawTransaction", [raw_tx_hex])

    def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.call("omni_getTransactionReceipt", [tx_hash])
        except Exception:
            r = self.call("eth_getTransactionReceipt", [tx_hash])
        return r

    def await_receipt(
        self,
        tx_hash: str,
        *,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Poll for a transaction receipt until timeout. Raises TimeoutError on expiry.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            rec = self.get_transaction_receipt(tx_hash)
            if rec:
                return rec
            time.sleep(poll_interval)
        raise TimeoutError(f"Timed out waiting for receipt {tx_hash}")

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self) -> "HttpRpcClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ------------------------------ WS Subscriptions ------------------------


class WsSubscription:
    """
    Async iterator over subscription messages with simple auto-reconnect.

    Designed for tests (lightweight). If the connection drops, it will attempt
    to reconnect and resubscribe using the original request.

    Usage:
        async with WsRpcClient("ws://127.0.0.1:8546") as ws:
            async for msg in ws.subscribe(method="omni_subscribe", params=["newHeads"]):
                ...
    """

    def __init__(
        self,
        client: "WsRpcClient",
        method: str,
        params: Sequence[Any],
        *,
        reconnect: bool = True,
    ) -> None:
        self._client = client
        self._method = method
        self._params = list(params)
        self._reconnect = reconnect
        self._sub_id: Optional[Union[str, int]] = None
        self._active = True

    async def _ensure_subscribed(self) -> None:
        if self._sub_id is not None:
            return
        req_id = self._client.next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": self._method,
            "params": self._params,
        }
        await self._client._send(json.dumps(payload).encode("utf-8"))
        # Wait for confirmation
        while True:
            msg = await self._client._recv()
            if not msg:
                continue
            try:
                obj = json.loads(msg)
            except Exception:
                continue
            if obj.get("id") == req_id and "result" in obj:
                self._sub_id = obj["result"]
                return
            # Some nodes push existing events before ack; ignore until ack arrives.

    def _matches_event(self, obj: Dict[str, Any]) -> bool:
        # eth_subscribe style: {"method":"eth_subscription","params":{"subscription": "0x..","result": {...}}}
        if obj.get("method") and "params" in obj:
            p = obj["params"]
            sid = p.get("subscription")
            return (sid is not None) and (self._sub_id is None or sid == self._sub_id)
        # omni_subscribe custom events may vary; fallback to passing all notifications when unknown
        return True

    async def __aiter__(self) -> AsyncIterator[Dict[str, Any]]:
        # Subscribe (or resubscribe after reconnects)
        await self._ensure_subscribed()
        while self._active:
            try:
                raw = await self._client._recv()
                if raw is None:
                    # connection dropped; try to reconnect if allowed
                    if not self._reconnect:
                        break
                    await self._client._reconnect()
                    self._sub_id = None
                    await self._ensure_subscribed()
                    continue
                obj = json.loads(raw)
                if self._matches_event(obj):
                    # Normalize result shape:
                    if (
                        "params" in obj
                        and isinstance(obj["params"], dict)
                        and "result" in obj["params"]
                    ):
                        yield obj["params"]["result"]
                    else:
                        yield obj
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOG.warning("WS subscription error: %s", e)
                if not self._reconnect:
                    break
                await asyncio.sleep(self._client.backoff)
                await self._client._reconnect()
                self._sub_id = None
                await self._ensure_subscribed()
        # End of iterator

    async def close(self) -> None:
        self._active = False
        # Best-effort unsubscribe when sub_id + method supports it
        try:
            if self._sub_id is not None:
                # Try eth_unsubscribe first, then omni_unsubscribe
                req_id = self._client.next_id()
                payload = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "eth_unsubscribe",
                    "params": [self._sub_id],
                }
                try:
                    await self._client._send(json.dumps(payload).encode("utf-8"))
                except Exception:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "method": "omni_unsubscribe",
                        "params": [self._sub_id],
                    }
                    await self._client._send(json.dumps(payload).encode("utf-8"))
        except Exception:
            pass


class WsRpcClient:
    """
    Very small WS JSON-RPC client with auto-reconnect.

    Use directly for 'send' / 'recv' JSON-RPC, or via .subscribe() helper.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = DEFAULT_WS_TIMEOUT,
        backoff: float = 0.5,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.backoff = max(0.0, backoff)
        self.headers = headers or {}
        self._conn: Optional[websockets.WebSocketClientProtocol] = None
        self._id = 0
        self._lock = asyncio.Lock()

    def next_id(self) -> int:
        self._id += 1
        return self._id

    async def _connect(self) -> None:
        LOG.info("WS connecting: %s", self.url)
        self._conn = await asyncio.wait_for(
            ws_connect(self.url, extra_headers=self.headers), timeout=self.timeout
        )
        LOG.info("WS connected")

    async def _reconnect(self) -> None:
        await self._close_conn()
        await self._connect()

    async def _close_conn(self) -> None:
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def _send(self, data: bytes) -> None:
        async with self._lock:
            if not self._conn:
                await self._connect()
            assert self._conn is not None
            await asyncio.wait_for(self._conn.send(data), timeout=self.timeout)

    async def _recv(self) -> Optional[str]:
        if not self._conn:
            await self._connect()
        assert self._conn is not None
        try:
            msg = await asyncio.wait_for(self._conn.recv(), timeout=self.timeout)
            if isinstance(msg, bytes):
                return msg.decode("utf-8", "replace")
            return msg
        except asyncio.TimeoutError:
            # heartbeat: return empty to allow loop to continue
            return ""
        except Exception:
            # likely connection dropped
            return None

    # ----- Public API -----

    async def send(
        self, method: str, params: Optional[Union[List[Any], Dict[str, Any]]] = None
    ) -> Any:
        rid = self.next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params or [],
        }
        await self._send(json.dumps(payload).encode("utf-8"))
        while True:
            raw = await self._recv()
            if raw is None:
                # dropped; reconnect and retry the call
                await self._reconnect()
                await self._send(json.dumps(payload).encode("utf-8"))
                continue
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if obj.get("id") == rid:
                if "error" in obj:
                    e = obj["error"]
                    raise JsonRpcError(
                        e.get("code", -32000),
                        e.get("message", "Unknown error"),
                        e.get("data"),
                    )
                return obj.get("result")

    def subscribe(
        self, method: str = "eth_subscribe", params: Sequence[Any] = ("newHeads",)
    ) -> WsSubscription:
        """
        Create a subscription (async iterator). Default is 'eth_subscribe' newHeads.
        Use method='omni_subscribe' if your node exposes that.
        """
        return WsSubscription(self, method, list(params))

    async def close(self) -> None:
        await self._close_conn()

    async def __aenter__(self) -> "WsRpcClient":
        await self._connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


# ------------------------------ Convenience ----------------------------


def await_head_height(
    rpc: HttpRpcClient, target_height: int, *, timeout: float = 30.0, poll: float = 0.25
) -> Dict[str, Any]:
    """
    Wait until latest block >= target_height and return that block header (light).
    """
    deadline = time.time() + timeout
    last_block: Any = None
    while time.time() < deadline:
        last_block = rpc.get_block_by_number("latest", full=False)
        if not last_block:
            time.sleep(poll)
            continue
        # eth-style numbers are hex strings
        number = last_block.get("number")
        if isinstance(number, str) and number.startswith("0x"):
            h = int(number, 16)
        else:
            try:
                h = int(number)
            except Exception:
                h = -1
        if h >= target_height:
            return last_block
        time.sleep(poll)
    raise TimeoutError(
        f"Timed out waiting for head >= {target_height}. Last={last_block}"
    )


def send_and_wait(
    rpc: HttpRpcClient, raw_tx_hex: str, *, timeout: float = 60.0
) -> Dict[str, Any]:
    """
    Convenience: send raw tx and wait for its receipt.
    """
    tx_hash = rpc.send_raw_transaction(raw_tx_hex)
    return rpc.await_receipt(tx_hash, timeout=timeout)


# ------------------------------ Self-test ------------------------------


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="Quick JSON-RPC client check")
    ap.add_argument("--rpc", default=os.environ.get("RPC_URL", "http://127.0.0.1:8545"))
    ap.add_argument("--ws", default=os.environ.get("WS_URL", "ws://127.0.0.1:8546"))
    args = ap.parse_args()

    with HttpRpcClient(args.rpc) as rpc:
        cid = rpc.get_chain_id()
        print("chainId:", cid)
        latest = rpc.get_block_by_number("latest", full=False)
        print(
            "latest:",
            json.dumps(
                {k: latest.get(k) for k in ["number", "hash", "parentHash"]}, indent=2
            ),
        )

    async def _ws_demo() -> None:
        try:
            async with WsRpcClient(args.ws) as ws:
                sub = ws.subscribe(method="eth_subscribe", params=["newHeads"])
                async for ev in sub:
                    print("newHead:", json.dumps(ev, indent=2))
                    break
                await sub.close()
        except Exception as e:
            print("WS demo failed (maybe WS not enabled?):", e)

    asyncio.run(_ws_demo())
