from __future__ import annotations

"""
WebSocket JSON-RPC client (async) with auto-reconnect and subscription helpers.

- Uses the `websockets` package.
- Correlates requests by `id` and dispatches notifications.
- Convenience `subscribe_*` methods for common streams or use generic `subscribe()`.

Example:
    import asyncio
    from omni_sdk.rpc.ws import WsClient

    async def main():
        async with WsClient("ws://localhost:8546") as ws:
            # Generic subscribe (server-defined)
            sub_id = await ws.subscribe("chain_subscribeNewHeads", [], on_event=lambda ev: print("head", ev))
            await asyncio.sleep(10)

    asyncio.run(main())
"""

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from itertools import count
from typing import (Any, Awaitable, Callable, Dict, Mapping, MutableMapping,
                    Optional, Union)

try:
    import websockets  # type: ignore
    from websockets.client import connect as ws_connect  # type: ignore
    from websockets.exceptions import ConnectionClosedError  # type: ignore
    from websockets.exceptions import ConnectionClosedOK
except Exception as e:  # pragma: no cover
    raise RuntimeError("The 'websockets' package is required for WsClient") from e

from ..errors import RpcError  # type: ignore
from ..version import __version__ as SDK_VERSION  # type: ignore

JSON = Union[dict, list, str, int, float, bool, None]
Params = Union[list, dict, None]
OnEvent = Callable[[JSON], None]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _jitter_backoff(base: float, factor: float, attempt: int, jitter: float) -> float:
    return base * (factor ** max(attempt - 1, 0)) + random.random() * jitter


@dataclass
class WsClient:
    url: str
    headers: Optional[Mapping[str, str]] = None
    connect_timeout: float = 15.0
    request_timeout: float = 30.0
    ping_interval: Optional[float] = 20.0
    max_retries: int = 10
    backoff_base: float = 0.25
    backoff_factor: float = 1.8
    backoff_jitter: float = 0.25
    _id_counter: Any = field(default_factory=lambda: count(start=_now_ms()))
    _ws: Optional[websockets.WebSocketClientProtocol] = field(init=False, default=None)  # type: ignore
    _reader_task: Optional[asyncio.Task] = field(init=False, default=None)
    _pending: Dict[int, asyncio.Future] = field(init=False, default_factory=dict)
    _sub_handlers: Dict[str, OnEvent] = field(init=False, default_factory=dict)
    _method_handlers: Dict[str, OnEvent] = field(init=False, default_factory=dict)
    _resubscribe: Dict[str, tuple[str, Params, OnEvent]] = field(
        init=False, default_factory=dict
    )
    _closing: bool = field(init=False, default=False)

    # ------------- context manager -------------

    async def __aenter__(self) -> "WsClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.close()

    # ------------- lifecycle -------------------

    async def connect(self) -> None:
        """Establish a WebSocket connection and start reader loop."""
        self._closing = False
        ua = f"omni-sdk-python/{SDK_VERSION}"
        hdrs = {"User-Agent": ua}
        if self.headers:
            hdrs.update(dict(self.headers))

        attempt = 0
        while True:
            attempt += 1
            try:
                self._ws = await asyncio.wait_for(
                    ws_connect(
                        self.url, extra_headers=hdrs, ping_interval=self.ping_interval
                    ),
                    timeout=self.connect_timeout,
                )
                # Start reader
                self._reader_task = asyncio.create_task(
                    self._reader_loop(), name="WsClient.reader"
                )
                # Resubscribe streams if reconnecting
                if self._resubscribe:
                    await self._restore_subscriptions()
                return
            except Exception as e:
                if attempt > self.max_retries:
                    raise RpcError(
                        code=-32098, message="WS connect failed", data=str(e)
                    )
                await asyncio.sleep(
                    _jitter_backoff(
                        self.backoff_base,
                        self.backoff_factor,
                        attempt,
                        self.backoff_jitter,
                    )
                )

    async def close(self) -> None:
        """Close the WebSocket and cancel tasks."""
        self._closing = True
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(Exception):  # type: ignore[name-defined]
                await self._reader_task
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        # Fail all pending requests
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(RpcError(code=-32098, message="WS closed", data=None))
        self._pending.clear()

    # ------------- RPC primitives --------------

    async def request(
        self, method: str, params: Params = None, *, id: Optional[int] = None
    ) -> JSON:
        """Send a JSON-RPC request and await the response."""
        if self._ws is None:
            await self.connect()

        assert self._ws is not None
        if id is None:
            id = next(self._id_counter)

        if params is None:
            params = []
        elif isinstance(params, dict):
            params = dict(params)
        elif isinstance(params, list):
            params = list(params)
        else:
            params = [params]  # type: ignore[list-item]

        payload = {"jsonrpc": "2.0", "id": id, "method": method, "params": params}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[id] = fut

        try:
            await asyncio.wait_for(
                self._ws.send(json.dumps(payload, separators=(",", ":"))),
                timeout=self.request_timeout,
            )
        except Exception as e:
            self._pending.pop(id, None)
            raise RpcError(code=-32098, message="WS send failed", data=str(e))

        try:
            return await asyncio.wait_for(fut, timeout=self.request_timeout)
        finally:
            self._pending.pop(id, None)

    # ------------- Subscriptions ----------------

    async def subscribe(
        self, method: str, params: Params = None, *, on_event: OnEvent
    ) -> str:
        """
        Generic subscription helper.

        Expects the server to return a subscription id in `result`.
        Notifications can be either:
          - {"jsonrpc":"2.0","method":"<someMethod>","params":{"subscription":"<id>","result":<event>}}
          - or {"jsonrpc":"2.0","method":"<id>","params":{...}} (less common)
        """
        res = await self.request(method, params or [])
        # Extract subscription id from common patterns
        if isinstance(res, dict) and "subscription" in res:
            sub_id = str(res["subscription"])
        else:
            sub_id = str(res)
        self._sub_handlers[sub_id] = on_event
        # Track for re-subscribe on reconnect
        self._resubscribe[sub_id] = (method, params or [], on_event)
        return sub_id

    async def unsubscribe(self, method: str, sub_id: str) -> bool:
        """Unsubscribe via server method and remove handler."""
        try:
            ok = await self.request(method, [sub_id])
        except RpcError:
            ok = False
        self._sub_handlers.pop(sub_id, None)
        self._resubscribe.pop(sub_id, None)
        return bool(ok)

    # Convenience wrappers (names are examples; adjust to your node API)
    async def subscribe_new_heads(self, on_event: OnEvent) -> str:
        # Common patterns seen: "chain_subscribeNewHeads" or "subscribe_newHeads"
        try_methods = (
            "chain_subscribeNewHeads",
            "subscribe_newHeads",
            "ws_subscribeNewHeads",
        )
        last_err: Optional[Exception] = None
        for m in try_methods:
            try:
                return await self.subscribe(m, [], on_event=on_event)
            except Exception as e:
                last_err = e
        raise RpcError(
            code=-32601,
            message="No supported newHeads subscription method",
            data=str(last_err),
        )

    async def subscribe_pending_txs(self, on_event: OnEvent) -> str:
        try_methods = ("mempool_subscribePendingTxs", "subscribe_pendingTxs")
        last_err: Optional[Exception] = None
        for m in try_methods:
            try:
                return await self.subscribe(m, [], on_event=on_event)
            except Exception as e:
                last_err = e
        raise RpcError(
            code=-32601,
            message="No supported pendingTxs subscription method",
            data=str(last_err),
        )

    # Arbitrary method notifications (not subscription-id keyed)
    def on(self, method: str, handler: OnEvent) -> None:
        """Register a handler for notifications with a specific JSON-RPC `method`."""
        self._method_handlers[method] = handler

    # ------------- internals --------------------

    async def _reader_loop(self) -> None:
        """Continuously read frames and dispatch to pending futures or handlers."""
        assert self._ws is not None
        while True:
            try:
                msg = await self._ws.recv()
            except asyncio.CancelledError:
                return
            except (ConnectionClosedError, ConnectionClosedOK):
                if self._closing:
                    return
                # Auto-reconnect
                await self._handle_reconnect()
                return
            except Exception as e:
                if self._closing:
                    return
                # transient error -> reconnect
                await self._handle_reconnect()
                return

            try:
                data = json.loads(msg)
            except Exception:
                # Ignore garbage frames
                continue

            # Response to a request
            if isinstance(data, dict) and "id" in data:
                rid = data.get("id")
                fut = (
                    self._pending.get(int(rid))
                    if isinstance(rid, int) or (isinstance(rid, str) and rid.isdigit())
                    else None
                )
                # Allow str ids as well
                if fut is None and isinstance(rid, str):
                    # Search pending with matching rid string (rare)
                    for k, v in list(self._pending.items()):
                        if str(k) == rid:
                            fut = v
                            break
                if fut is not None and not fut.done():
                    if "error" in data and data["error"] is not None:
                        err = data["error"]
                        fut.set_exception(
                            RpcError(
                                code=err.get("code", -32603),
                                message=err.get("message", "Unknown error"),
                                data=err.get("data"),
                            )
                        )
                    else:
                        fut.set_result(data.get("result"))
                continue

            # Notification / subscription event
            if isinstance(data, dict) and "method" in data:
                method = str(data["method"])
                params = data.get("params")
                # Try subscription-style dispatch
                if isinstance(params, dict) and "subscription" in params:
                    sub_id = str(params["subscription"])
                    event = params.get("result")
                    handler = self._sub_handlers.get(sub_id)
                    if handler:
                        try:
                            handler(event)
                        except Exception:
                            pass
                    continue
                # Fallback: method-based handler
                handler = self._method_handlers.get(method)
                if handler:
                    try:
                        handler(params)
                    except Exception:
                        pass
                continue

            # Unknown frame kinds are ignored

    async def _handle_reconnect(self) -> None:
        """Try to reconnect and restart reader."""
        if self._closing:
            return
        # Notify all pending requests that the transport dropped
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(
                    RpcError(code=-32098, message="WS disconnected", data=None)
                )
        self._pending.clear()

        # Attempt reconnection with backoff
        attempt = 0
        while not self._closing:
            attempt += 1
            try:
                await self.connect()
                return
            except RpcError:
                if attempt > self.max_retries:
                    return
                await asyncio.sleep(
                    _jitter_backoff(
                        self.backoff_base,
                        self.backoff_factor,
                        attempt,
                        self.backoff_jitter,
                    )
                )

    async def _restore_subscriptions(self) -> None:
        """Re-subscribe active streams after reconnect."""
        # Keep a snapshot to avoid mutation while iterating
        snapshot = list(self._resubscribe.items())
        self._sub_handlers.clear()
        for old_id, (method, params, handler) in snapshot:
            try:
                new_id = await self.subscribe(method, params, on_event=handler)
                # Drop the old mapping if present
                if old_id in self._resubscribe and old_id != new_id:
                    self._resubscribe.pop(old_id, None)
            except Exception:
                # Best effort; continue with others
                pass


# Lazy import guard for contextlib used in close()
import contextlib  # placed at end to keep top minimal

__all__ = ["WsClient", "JSON", "Params", "OnEvent"]
