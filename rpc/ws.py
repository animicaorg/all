"""
Animica RPC — WebSocket Hub
===========================

A lightweight publish/subscribe hub for:
  • newHeads    — broadcasts finalized/canonical head updates
  • pendingTxs  — broadcasts new pending transactions admitted by RPC

Protocol (JSON frames)
----------------------
Client → Server control frames:
  {"op":"sub","topics":["newHeads","pendingTxs"]}
  {"op":"unsub","topics":["pendingTxs"]}
  {"op":"ping","ts":<unix_ms>}

Server → Client:
  {"op":"hello","topics":[...],"serverTime":<unix_ms>}
  {"op":"pong","ts":<echo>}
  {"topic":"newHeads","data":{HeadView}, "ts":<unix_ms>}
  {"topic":"pendingTxs","data":{TxView}, "ts":<unix_ms>}

Notes
-----
• Backpressure-safe: each client has a bounded queue; when full, newest events
  overwrite oldest (lossy but keeps UI responsive).
• Ready for FastAPI mounting at route /ws (see rpc/server.py).
• Public helpers `publish_new_head` and `publish_pending_tx` can be called
  from RPC methods or head trackers.

Security
--------
This hub is broadcast-only; it trusts upstream components (e.g., RPC tx admit)
to validate inputs. Rate limits & CORS are handled by the main app.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Literal, Optional, Set, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

# Pydantic models used for serialization
from .models import Head, TxView  # type: ignore

log = logging.getLogger(__name__)

Topic = Literal["newHeads", "pendingTxs"]

ALLOWED_TOPICS: Set[str] = {"newHeads", "pendingTxs"}

# --------------------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)

def _model_to_dict(obj: Any) -> Dict[str, Any]:
    """
    Convert Pydantic v2/v1 models, dataclasses, or simple objects into JSON-able dicts.
    """
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")  # type: ignore[no-any-return]
        except TypeError:
            return obj.model_dump()  # type: ignore[no-any-return]
    # Pydantic v1
    if hasattr(obj, "dict"):
        return obj.dict()  # type: ignore[no-any-return]
    # Dataclass
    if is_dataclass(obj):
        return asdict(obj)
    # Fallback: assume already dict-like
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"Cannot serialize object of type {type(obj)}")

# --------------------------------------------------------------------------------------
# Client & Hub
# --------------------------------------------------------------------------------------

class _Client:
    """
    Per-connection state. Each client owns a bounded queue and a sender task.
    """
    __slots__ = ("ws", "topics", "queue", "sender_task", "peer")

    def __init__(self, ws: WebSocket, topics: Iterable[Topic], queue_size: int = 256) -> None:
        self.ws: WebSocket = ws
        self.topics: Set[Topic] = set(topics)
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=queue_size)
        self.sender_task: Optional[asyncio.Task] = None
        try:
            scope = getattr(ws, "scope", {})
            addr = scope.get("client")
            self.peer = f"{addr[0]}:{addr[1]}" if addr else "unknown"
        except Exception:
            self.peer = "unknown"

    async def start(self) -> None:
        self.sender_task = asyncio.create_task(self._sender_loop(), name=f"ws-sender-{self.peer}")

    async def stop(self) -> None:
        if self.sender_task and not self.sender_task.done():
            self.sender_task.cancel()
            try:
                await self.sender_task
            except asyncio.CancelledError:
                pass

    async def _sender_loop(self) -> None:
        try:
            while True:
                msg = await self.queue.get()
                await self.ws.send_text(json.dumps(msg))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug("WS sender loop ended for %s: %s", self.peer, e)

    async def enqueue(self, frame: Dict[str, Any]) -> None:
        """
        Non-blocking-ish enqueue with drop-oldest policy under pressure.
        """
        if self.queue.full():
            try:
                _ = self.queue.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Should be rare after drop; skip silently
            pass


class WebSocketHub:
    """
    In-process pub/sub hub.
    """

    def __init__(self) -> None:
        self._clients: Set[_Client] = set()
        # topic -> clients
        self._subs: Dict[str, Set[_Client]] = {t: set() for t in ALLOWED_TOPICS}
        self._lock = asyncio.Lock()

    # --------------------------
    # Connection handling
    # --------------------------

    async def handle_connection(self, websocket: WebSocket, topics_qs: Optional[str] = None) -> None:
        """
        Accept a client and process control frames until disconnect.
        """
        await websocket.accept()
        topics: List[Topic] = self._parse_topics_qs(topics_qs)

        client = _Client(websocket, topics)
        await self._register(client)

        # Send hello
        await client.enqueue({"op": "hello", "topics": list(client.topics), "serverTime": _now_ms()})

        try:
            await client.start()
            # Control loop: sub/unsub/ping; ignore others
            while True:
                msg = await websocket.receive_text()
                try:
                    obj = json.loads(msg)
                except Exception:
                    continue
                op = obj.get("op")
                if op == "ping":
                    await client.enqueue({"op": "pong", "ts": obj.get("ts", _now_ms())})
                elif op == "sub":
                    await self._update_topics(client, obj.get("topics", []), add=True)
                elif op == "unsub":
                    await self._update_topics(client, obj.get("topics", []), add=False)
                else:
                    # Unknown op: ignore (protocol is simple by design)
                    pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("WS connection error: %s", e)
        finally:
            await self._unregister(client)

    def _parse_topics_qs(self, topics_qs: Optional[str]) -> List[Topic]:
        if not topics_qs:
            return []
        out: List[Topic] = []
        for t in topics_qs.split(","):
            t = t.strip()
            if t in ALLOWED_TOPICS:
                out.append(t)  # type: ignore[arg-type]
        return out

    async def _register(self, c: _Client) -> None:
        async with self._lock:
            self._clients.add(c)
            for t in c.topics:
                self._subs[t].add(c)
        log.debug("WS connected %s; topics=%s; total=%d", c.peer, c.topics, len(self._clients))

    async def _unregister(self, c: _Client) -> None:
        async with self._lock:
            self._clients.discard(c)
            for s in self._subs.values():
                s.discard(c)
        await c.stop()
        try:
            await c.ws.close()
        except Exception:
            pass
        log.debug("WS disconnected %s; total=%d", c.peer, len(self._clients))

    async def _update_topics(self, c: _Client, topics: Iterable[str], add: bool) -> None:
        valid = [t for t in topics if t in ALLOWED_TOPICS]
        async with self._lock:
            if add:
                for t in valid:
                    self._subs[t].add(c)
                    c.topics.add(t)  # type: ignore[arg-type]
            else:
                for t in valid:
                    self._subs[t].discard(c)
                    c.topics.discard(t)  # type: ignore[arg-type]
        # Acknowledge with current topic set
        await c.enqueue({"op": "hello", "topics": list(c.topics), "serverTime": _now_ms()})

    # --------------------------
    # Broadcast API
    # --------------------------

    async def publish_new_head(self, head: Head) -> int:
        """
        Broadcast a new head view. Returns number of clients enqueued.
        """
        payload = _model_to_dict(head)
        frame = {"topic": "newHeads", "data": payload, "ts": _now_ms()}
        count = 0
        async with self._lock:
            for c in list(self._subs["newHeads"]):
                await c.enqueue(frame)
                count += 1
        return count

    async def publish_pending_tx(self, tx: TxView) -> int:
        """
        Broadcast a new pending tx admitted by RPC.
        """
        payload = _model_to_dict(tx)
        frame = {"topic": "pendingTxs", "data": payload, "ts": _now_ms()}
        count = 0
        async with self._lock:
            for c in list(self._subs["pendingTxs"]):
                await c.enqueue(frame)
                count += 1
        return count

    # --------------------------
    # Introspection (optional)
    # --------------------------

    async def stats(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "clients": len(self._clients),
                "subs": {t: len(s) for t, s in self._subs.items()},
            }


# Singleton hub & FastAPI router
hub = WebSocketHub()
router = APIRouter()

@router.websocket("/")
async def ws_route(websocket: WebSocket, topics: Optional[str] = Query(default=None, description="Comma-separated topics: newHeads,pendingTxs")):
    """
    WebSocket endpoint. Mount this router at prefix /ws in rpc/server.py:

        app.include_router(ws.router, prefix="/ws", tags=["ws"])
    """
    await hub.handle_connection(websocket, topics_qs=topics)


# Convenience re-exports for other modules
publish_new_head = hub.publish_new_head
publish_pending_tx = hub.publish_pending_tx
