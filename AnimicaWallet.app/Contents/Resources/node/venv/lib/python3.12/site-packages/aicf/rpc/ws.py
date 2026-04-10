from __future__ import annotations

"""
aicf.rpc.ws
-----------

WebSocket hub for AICF events:

- aicf.jobAssigned
- aicf.jobCompleted
- aicf.providerSlashed
- aicf.epochSettled

Clients connect to the WS endpoint and optionally pass a comma-separated list
of `topics` query params. If omitted, they subscribe to all AICF topics.

Example client (browser):
    const ws = new WebSocket("wss://host/aicf/ws?topics=aicf.jobAssigned,aicf.jobCompleted");
    ws.onmessage = (e) => console.log(JSON.parse(e.data));

Server wiring (FastAPI):
    from fastapi import FastAPI
    from aicf.rpc.ws import build_ws_router, publish_job_assigned
    app = FastAPI()
    app.include_router(build_ws_router(), prefix="/aicf")

Publishing:
    await publish_job_assigned({...})
    await publish_job_completed({...})
    await publish_provider_slashed({...})
    await publish_epoch_settled({...})
"""

import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, MutableMapping, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

# Canonical topic strings
JOB_ASSIGNED = "aicf.jobAssigned"
JOB_COMPLETED = "aicf.jobCompleted"
PROVIDER_SLASHED = "aicf.providerSlashed"
EPOCH_SETTLED = "aicf.epochSettled"

ALL_TOPICS = (JOB_ASSIGNED, JOB_COMPLETED, PROVIDER_SLASHED, EPOCH_SETTLED)


class AICFWebSocketHub:
    """
    Minimal topic hub that tracks WebSocket subscribers and broadcasts JSON messages.
    """

    def __init__(self) -> None:
        self._topics: MutableMapping[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, ws: WebSocket, topics: Iterable[str]) -> None:
        async with self._lock:
            for t in topics:
                if t in ALL_TOPICS:
                    self._topics[t].add(ws)

    async def unsubscribe(self, ws: WebSocket) -> None:
        async with self._lock:
            for t in list(self._topics.keys()):
                self._topics[t].discard(ws)
                if not self._topics[t]:
                    self._topics.pop(t, None)

    async def emit(self, topic: str, data: Dict[str, Any]) -> None:
        """
        Broadcast a message to all subscribers of `topic`.

        The envelope is:
            {"event": "<topic>", "ts": <unix_sec>, "data": <payload>}
        """
        if topic not in ALL_TOPICS:
            return
        envelope = {"event": topic, "ts": time.time(), "data": data}
        msg = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)

        # Take a snapshot to avoid holding the lock while sending
        async with self._lock:
            targets = list(self._topics.get(topic, set()))
        if not targets:
            return

        stale: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(msg)
            except Exception:
                stale.append(ws)

        if stale:
            async with self._lock:
                for ws in stale:
                    for t in ALL_TOPICS:
                        self._topics[t].discard(ws)


# Global hub used by router + publishers
_hub = AICFWebSocketHub()


def build_ws_router() -> APIRouter:
    """
    Return a FastAPI APIRouter that serves the AICF WS at `/ws`.

    Query params:
      - topics: comma-separated list, defaults to all topics
    """
    router = APIRouter()

    @router.websocket("/ws")
    async def aicf_ws(
        websocket: WebSocket,
        topics: str = Query(default=",".join(ALL_TOPICS)),
    ) -> None:
        wanted = [t.strip() for t in topics.split(",") if t.strip()]
        await websocket.accept()
        await _hub.subscribe(websocket, wanted or ALL_TOPICS)
        try:
            # Simple receive loop (keep-alive & client pings). Ignore messages.
            while True:
                try:
                    _ = await websocket.receive_text()
                    # Optionally echo pings:
                    if _ == "ping":
                        await websocket.send_text(
                            json.dumps({"event": "pong", "ts": time.time()})
                        )
                except WebSocketDisconnect:
                    break
        finally:
            await _hub.unsubscribe(websocket)

    return router


# ---- Publisher helpers ----------------------------------------------------- #
# Async publishers (preferred)
async def publish_job_assigned(payload: Dict[str, Any]) -> None:
    await _hub.emit(JOB_ASSIGNED, payload)


async def publish_job_completed(payload: Dict[str, Any]) -> None:
    await _hub.emit(JOB_COMPLETED, payload)


async def publish_provider_slashed(payload: Dict[str, Any]) -> None:
    await _hub.emit(PROVIDER_SLASHED, payload)


async def publish_epoch_settled(payload: Dict[str, Any]) -> None:
    await _hub.emit(EPOCH_SETTLED, payload)


# Fire-and-forget convenience wrappers (when you can't `await`)
def _spawn(coro: "asyncio.coroutines.Coroutine[Any, Any, Any]") -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        # No running loop (e.g., CLI/tests) — run to completion.
        asyncio.run(coro)


def publish_job_assigned_nowait(payload: Dict[str, Any]) -> None:
    _spawn(publish_job_assigned(payload))


def publish_job_completed_nowait(payload: Dict[str, Any]) -> None:
    _spawn(publish_job_completed(payload))


def publish_provider_slashed_nowait(payload: Dict[str, Any]) -> None:
    _spawn(publish_provider_slashed(payload))


def publish_epoch_settled_nowait(payload: Dict[str, Any]) -> None:
    _spawn(publish_epoch_settled(payload))


__all__ = [
    "build_ws_router",
    "publish_job_assigned",
    "publish_job_completed",
    "publish_provider_slashed",
    "publish_epoch_settled",
    "publish_job_assigned_nowait",
    "publish_job_completed_nowait",
    "publish_provider_slashed_nowait",
    "publish_epoch_settled_nowait",
    "JOB_ASSIGNED",
    "JOB_COMPLETED",
    "PROVIDER_SLASHED",
    "EPOCH_SETTLED",
    "ALL_TOPICS",
]
