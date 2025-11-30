"""
capabilities.rpc.ws
===================

WebSocket hub for Capabilities events.

- Endpoint:  /ws   (mounted under the chosen prefix, e.g. /cap/ws)
- Event(s):  "jobCompleted" — pushed when an off-chain job result becomes available.

External code (e.g. capabilities.jobs.resolver) should call
`publish_job_completed(...)` or `publish_job_completed_nowait(...)` to broadcast
an event to all connected subscribers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Iterable, Literal, Optional, TypedDict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

__all__ = [
    "router",
    "publish_job_completed",
    "publish_job_completed_nowait",
]


# ----------- Event types -----------


class JobCompletedEvent(TypedDict, total=False):
    """Wire shape for jobCompleted push messages."""

    type: Literal["jobCompleted"]
    task_id: str
    kind: Literal["AI", "Quantum"]
    height: int
    caller: str
    units: int
    result_digest: str  # hex string of result/content digest if available
    meta: Dict[str, Any]  # optional free-form metadata


# ----------- Simple in-process WS hub -----------


class _Hub:
    """Minimal broadcast hub that tracks live WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
            log.debug("cap/ws: client added; total=%d", len(self._clients))

    async def discard(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
            log.debug("cap/ws: client removed; total=%d", len(self._clients))

    async def broadcast_json(self, payload: Dict[str, Any]) -> None:
        # Snapshot to avoid holding the lock while sending
        async with self._lock:
            clients = list(self._clients)

        if not clients:
            return

        msg = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        to_drop: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(msg)
            except Exception as e:  # pragma: no cover - network race
                log.debug("cap/ws: send failed (%s); dropping client", e)
                to_drop.append(ws)

        # Clean up any dropped clients
        if to_drop:
            async with self._lock:
                for ws in to_drop:
                    self._clients.discard(ws)


_hub = _Hub()


# ----------- Public publisher API -----------


async def publish_job_completed(
    *,
    task_id: str,
    kind: Literal["AI", "Quantum"],
    height: Optional[int] = None,
    caller: Optional[str] = None,
    units: Optional[int] = None,
    result_digest: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Broadcast a 'jobCompleted' event to all connected subscribers.

    All fields are optional except task_id and kind; include what you have.
    """
    event: JobCompletedEvent = {
        "type": "jobCompleted",
        "task_id": task_id,
        "kind": kind,
    }
    if height is not None:
        event["height"] = int(height)
    if caller is not None:
        event["caller"] = caller
    if units is not None:
        event["units"] = int(units)
    if result_digest is not None:
        event["result_digest"] = result_digest
    if meta:
        event["meta"] = dict(meta)

    await _hub.broadcast_json(event)


def publish_job_completed_nowait(**kwargs: Any) -> None:
    """
    Fire-and-forget variant of publish_job_completed(). Schedules the broadcast
    on the current event loop and returns immediately.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no loop running (sync context)
        loop = asyncio.get_event_loop()
    loop.create_task(publish_job_completed(**kwargs))


# ----------- Router factory -----------


def router() -> APIRouter:
    """
    Build and return an APIRouter exposing the /ws endpoint.
    """
    r = APIRouter()

    @r.websocket("/ws")
    async def capabilities_ws(ws: WebSocket) -> None:
        # Accept immediately; this channel is push-only (server -> client).
        await ws.accept()
        await _hub.add(ws)
        log.debug("cap/ws: client connected")

        try:
            # Read loop: we don't require any messages from clients, but we
            # keep the receive loop running to detect disconnects, and we
            # respond to occasional 'ping' messages with a 'pong'.
            while True:
                msg = await ws.receive_text()
                if msg.strip().lower() == "ping":
                    await ws.send_text('{"type":"pong"}')
        except WebSocketDisconnect:
            pass
        except Exception as e:  # pragma: no cover - network race
            log.debug("cap/ws: exception on connection: %s", e)
        finally:
            await _hub.discard(ws)
            log.debug("cap/ws: client disconnected")

    return r
