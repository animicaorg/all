"""
Minimal JSON-RPC/WS client helpers for the {{ project_slug }} Indexer Lite.

This module focuses on:
- **Reliable HTTP JSON-RPC** with retries and timeouts.
- **Batched RPC** for efficient block pulls.
- **WebSocket subscriptions** with automatic reconnect and backoff
  (for streams like `newHeads` and `newPendingTransactions`).

Dependencies
------------
- HTTP: `httpx` (async)
- WS: `websockets` (async)

Both are intentionally lightweight and widely used.

Typical use
-----------
>>> from .config import from_env
>>> cfg = from_env()
>>> client = JsonRpcClient(cfg)
>>> async with client:
...     head = await client.block_number()
...     blk = await client.get_block_by_number(head, full_txs=False)

WebSocket streams
-----------------
>>> async with client:
...     async for ev in client.stream_new_heads():
...         print("new head", ev["number"])
...         break

Notes
-----
- JSON-RPC methods used here mirror Ethereum-style names for familiarity
  (`eth_blockNumber`, `eth_getBlockByNumber`, etc.). If your chain uses
  slightly different method names, adjust the constants below.
- The WS subscription flow uses `eth_subscribe` and receives
  `eth_subscription` notifications.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import (Any, AsyncIterator, Dict, Iterable, List, Mapping,
                    Optional, Tuple, Union, cast)

import httpx  # type: ignore[import]
import websockets  # type: ignore[import]
from websockets.client import WebSocketClientProtocol  # type: ignore[import]

from .config import IndexerConfig

Json = Mapping[str, Any]
Params = Union[List[Any], Mapping[str, Any], None]


# ------------------------------ JSON-RPC names ------------------------------ #

ETH_BLOCK_NUMBER = "eth_blockNumber"
ETH_GET_BLOCK_BY_NUMBER = "eth_getBlockByNumber"
ETH_GET_BLOCK_BY_HASH = "eth_getBlockByHash"
ETH_GET_TRANSACTION_BY_HASH = "eth_getTransactionByHash"
ETH_GET_LOGS = "eth_getLogs"
ETH_BATCH = "batch"  # pseudo label used internally

ETH_SUBSCRIBE = "eth_subscribe"
ETH_SUB_NOTIFICATION = "eth_subscription"

SUB_NEW_HEADS = "newHeads"
SUB_PENDING_TXS = "newPendingTransactions"  # alias often seen as "pendingTxs"


# --------------------------------- errors ---------------------------------- #


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(
            f"RPC error {code}: {message}"
            + (f" | data={data}" if data is not None else "")
        )


# --------------------------------- helpers --------------------------------- #


def _hex_to_int(h: Union[str, int]) -> int:
    if isinstance(h, int):
        return h
    if not isinstance(h, str):
        raise TypeError(f"expected hex-str or int, got {type(h)}")
    return int(h, 16)


def _int_to_hex(n: int) -> str:
    if not isinstance(n, int):
        raise TypeError("block number must be int")
    return hex(n)


def _ok(resp_json: Json) -> Any:
    if "error" in resp_json and resp_json["error"]:
        err = cast(Json, resp_json["error"])
        raise RpcError(
            int(err.get("code", -1)),
            str(err.get("message", "unknown error")),
            err.get("data"),
        )
    if "result" not in resp_json:
        raise RpcError(-1, f"Malformed JSON-RPC response (no result): {resp_json}")
    return resp_json["result"]


# --------------------------------- client ---------------------------------- #


@dataclass
class JsonRpcClient:
    cfg: IndexerConfig
    _http: Optional[httpx.AsyncClient] = None
    _log: logging.Logger = logging.getLogger("indexer.rpc")

    async def __aenter__(self) -> "JsonRpcClient":
        self._http = httpx.AsyncClient(
            base_url=self.cfg.rpc_url, timeout=self.cfg.http_timeout_s
        )
        self._log.setLevel(getattr(logging, self.cfg.log_level.upper(), logging.INFO))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._http is not None:
            await self._http.aclose()
        self._http = None

    # --------------------------- core HTTP JSON-RPC -------------------------- #

    async def call(self, method: str, params: Params = None) -> Any:
        """
        Single JSON-RPC call with retry logic.
        """
        assert self._http is not None, "Use `async with JsonRpcClient(...):`"
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": method,
            "params": params if params is not None else [],
        }
        return await self._post_with_retries(payload)

    async def batch(self, calls: Iterable[Tuple[str, Params]]) -> List[Any]:
        """
        Batch multiple RPC calls. Returns results in the same order.
        """
        assert self._http is not None, "Use `async with JsonRpcClient(...):`"

        items: List[Dict[str, Any]] = []
        order: List[str] = []
        for method, params in calls:
            ident = uuid.uuid4().hex
            items.append(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "method": method,
                    "params": params or [],
                }
            )
            order.append(ident)

        raw = await self._post_with_retries(items)
        if not isinstance(raw, list):
            raise RpcError(-1, f"Malformed batch response: {raw!r}")

        # Map id -> result
        results: Dict[str, Any] = {}
        for entry in raw:
            rid = str(entry.get("id"))
            results[rid] = _ok(entry)

        return [results[i] for i in order]

    async def _post_with_retries(self, payload: Any) -> Any:
        """
        POST JSON with basic retry-budget on connection/reset/timeouts.
        """
        assert self._http is not None
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.cfg.http_retries + 1):
            try:
                resp = await self._http.post("", json=payload)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(payload, dict):
                    return _ok(data)
                # batch
                if isinstance(payload, list):
                    return data
                return data
            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
                httpx.WriteError,
                httpx.HTTPStatusError,
            ) as e:  # noqa: E501
                last_exc = e
                sleep_s = min(
                    self.cfg.ws_backoff_initial_s * (2 ** (attempt - 1)),
                    self.cfg.ws_backoff_max_s,
                )
                self._log.warning(
                    "RPC POST failed (attempt %d/%d): %s; sleeping %.2fs",
                    attempt,
                    self.cfg.http_retries,
                    e,
                    sleep_s,
                )  # noqa: E501
                await asyncio.sleep(sleep_s)
            except json.JSONDecodeError as e:
                raise RpcError(-1, f"Invalid JSON in response: {e}") from e

        assert last_exc is not None
        raise RpcError(
            -1, f"RPC POST failed after {self.cfg.http_retries} attempts: {last_exc}"
        )

    # ----------------------------- convenience ------------------------------ #

    async def block_number(self) -> int:
        res = await self.call(ETH_BLOCK_NUMBER)
        return _hex_to_int(res)

    async def get_block_by_number(
        self, number: Union[int, str], full_txs: bool = False
    ) -> Json:
        num = _int_to_hex(number) if isinstance(number, int) else number
        return cast(Json, await self.call(ETH_GET_BLOCK_BY_NUMBER, [num, full_txs]))

    async def get_block_by_hash(self, block_hash: str, full_txs: bool = False) -> Json:
        return cast(
            Json, await self.call(ETH_GET_BLOCK_BY_HASH, [block_hash, full_txs])
        )

    async def get_tx(self, tx_hash: str) -> Optional[Json]:
        return cast(
            Optional[Json], await self.call(ETH_GET_TRANSACTION_BY_HASH, [tx_hash])
        )

    async def get_logs(self, params: Mapping[str, Any]) -> List[Json]:
        return cast(List[Json], await self.call(ETH_GET_LOGS, [params]))

    async def get_block_range(
        self,
        start: int,
        end_inclusive: int,
        full_txs: bool = False,
        max_batch: Optional[int] = None,
    ) -> List[Json]:
        """
        Fetch a (potentially large) range of blocks using batched calls.

        Returns a list of block objects ordered by number ascending.
        """
        if start < 0 or end_inclusive < start:
            raise ValueError("invalid range")

        max_batch = max_batch or self.cfg.max_batch_size
        out: List[Json] = []
        cur = start
        while cur <= end_inclusive:
            chunk_end = min(cur + max_batch - 1, end_inclusive)
            batch_calls = [
                (ETH_GET_BLOCK_BY_NUMBER, [_int_to_hex(n), full_txs])
                for n in range(cur, chunk_end + 1)
            ]
            results = await self.batch(batch_calls)
            # batch preserves order
            out.extend(cast(List[Json], results))
            cur = chunk_end + 1
        return out

    # ----------------------------- WS streaming ----------------------------- #

    async def stream_new_heads(self) -> AsyncIterator[Json]:
        """
        Yield new head headers indefinitely (with reconnect).
        """
        async for msg in self._ws_stream(SUB_NEW_HEADS, None):
            yield msg

    async def stream_pending_txs(self) -> AsyncIterator[Union[str, Json]]:
        """
        Yield pending transaction notifications indefinitely (with reconnect).

        Depending on node config, results may be a tx hash string or a full tx object.
        """
        async for msg in self._ws_stream(SUB_PENDING_TXS, None):
            yield msg

    async def _ws_stream(
        self, topic: str, params: Optional[Union[Mapping[str, Any], List[Any]]] = None
    ) -> AsyncIterator[Any]:  # noqa: E501
        """
        Internal WS stream with backoff, heartbeat, and resubscribe.

        Yields the `result` field of `eth_subscription` notifications.
        """
        if not self.cfg.ws_url:
            raise RuntimeError("WS_URL is not configured")
        # Backoff loop
        backoff = self.cfg.ws_backoff_initial_s

        while True:
            try:
                async for result in self._ws_session(topic, params):
                    # reset backoff after a healthy message stream
                    backoff = self.cfg.ws_backoff_initial_s
                    yield result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._log.warning(
                    "WS stream error (%s). Reconnecting in %.2fs ...", e, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.cfg.ws_backoff_max_s)

    async def _ws_session(
        self, topic: str, params: Optional[Union[Mapping[str, Any], List[Any]]]
    ) -> AsyncIterator[Any]:
        """
        One WS connection lifecycle: connect -> subscribe -> receive loop.
        """
        assert self.cfg.ws_url is not None
        subscribe_params: List[Any]
        if params is None:
            subscribe_params = [topic]
        elif isinstance(params, list):
            subscribe_params = [topic, *params]
        else:
            subscribe_params = [topic, params]

        self._log.info("WS connecting to %s", self.cfg.ws_url)
        async with websockets.connect(self.cfg.ws_url) as ws:  # type: ignore[arg-type]
            await self._ws_subscribe(ws, subscribe_params)
            self._log.info("WS subscribed to %s", topic)

            # Heartbeat task: use standard WS ping frames
            hb_task = asyncio.create_task(
                self._ws_heartbeat(ws, self.cfg.ws_heartbeat_s)
            )
            try:
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)

                    # Either a response to subscribe (already handled) or notifications
                    meth = msg.get("method")
                    if meth == ETH_SUB_NOTIFICATION:
                        params = cast(Json, msg.get("params", {}))
                        result = params.get("result")
                        yield result
                    # else: ignore non-notification frames silently
            finally:
                hb_task.cancel()
                with contextlib.suppress(Exception):
                    await hb_task

    async def _ws_subscribe(
        self, ws: WebSocketClientProtocol, subscribe_params: List[Any]
    ) -> str:
        """
        Send `eth_subscribe` request and wait for the result (subscription id).
        """
        req_id = uuid.uuid4().hex
        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": ETH_SUBSCRIBE,
            "params": subscribe_params,
        }
        await ws.send(json.dumps(req))

        # Wait for the subscription confirmation matching our id
        # Some servers reply with a standard JSON-RPC response:
        #   {"jsonrpc":"2.0","id":<id>,"result":"0xSUBID"}
        # Others may first push a notification; we loop a bit defensively.
        deadline = time.monotonic() + 10.0
        while True:
            if time.monotonic() > deadline:
                raise RpcError(-1, "WS subscribe timeout")
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == req_id and "result" in msg:
                return cast(str, msg["result"])
            # If we get notifications before confirmation, ignore until we see the id

    async def _ws_heartbeat(self, ws: WebSocketClientProtocol, every_s: float) -> None:
        """
        Periodic WS ping; raises on failure so the session can reconnect.
        """
        try:
            while True:
                await asyncio.sleep(every_s)
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=max(1.0, every_s / 2.0))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Propagate to caller by closing; recv loop will exit
            try:
                await ws.close(code=1011, reason=f"heartbeat failure: {e}")
            finally:
                raise


# ------------------------------ context utils ------------------------------ #

import contextlib  # placed at end to keep imports tidy for reading

__all__ = [
    "JsonRpcClient",
    "RpcError",
    "ETH_BLOCK_NUMBER",
    "ETH_GET_BLOCK_BY_NUMBER",
    "ETH_GET_BLOCK_BY_HASH",
    "ETH_GET_TRANSACTION_BY_HASH",
    "ETH_GET_LOGS",
    "ETH_SUBSCRIBE",
    "ETH_SUB_NOTIFICATION",
    "SUB_NEW_HEADS",
    "SUB_PENDING_TXS",
]
