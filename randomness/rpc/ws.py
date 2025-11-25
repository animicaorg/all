"""
randomness.rpc.ws
-----------------

WebSocket broadcaster for randomness/beacon lifecycle events.

Emits three event kinds to all connected clients:

- "roundOpened":    when a new round opens for commits
- "roundClosed":    when the round's reveal window closes
- "beaconFinalized":when the beacon output is finalized (after VDF, optional QRNG mix)

Message shape (always JSON):
  {
    "event": "<one of: roundOpened|roundClosed|beaconFinalized>",
    "data": { ... event-specific fields ... }
  }

Mount example (in your FastAPI app):
    from randomness.rpc.ws import router as rand_ws_router, publisher as rand_ws_publisher
    app.include_router(rand_ws_router)

Publish example (from beacon code / adapters):
    await rand_ws_publisher.a_round_opened({...})
    # or fire-and-forget:
    rand_ws_publisher.round_opened({...})
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Set, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, validator

router = APIRouter()


# -------------------- Event payload models --------------------

class RoundOpened(BaseModel):
    round_id: int = Field(..., description="Opened round id.")
    commit_open: int = Field(..., description="Unix timestamp (s) when commit window opened.")
    commit_close: int = Field(..., description="Unix timestamp (s) when commit window closes.")
    reveal_open: int = Field(..., description="Unix timestamp (s) when reveal window opens.")
    reveal_close: int = Field(..., description="Unix timestamp (s) when reveal window closes.")
    now: Optional[int] = Field(None, description="Server timestamp (s) at emit time.")


class RoundClosed(BaseModel):
    round_id: int = Field(..., description="Closed round id.")
    closed_at: int = Field(..., description="Unix timestamp (s) when reveal window closed.")
    reason: str = Field("reveal_closed", description="Closure reason label.")


class BeaconFinalized(BaseModel):
    round_id: int = Field(..., description="Round id associated with this beacon.")
    beacon: str = Field(..., description="0x-hex beacon output.")
    aggregate: Optional[str] = Field(None, description="0x-hex pre-VDF aggregate of reveals.")
    vdf_input: Optional[str] = Field(None, description="0x-hex VDF input.")
    vdf_proof: Optional[str] = Field(None, description="0x-hex VDF proof (Wesolowski).")
    mixed_with_qrng: bool = Field(False, description="True if QRNG mixing applied.")
    timestamp: int = Field(..., description="Unix timestamp (s) of finalization.")

    @validator("beacon", "aggregate", "vdf_input", "vdf_proof", pre=True, always=True)
    def _hex_prefix(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return v if (isinstance(v, str) and (v.startswith("0x") or v.startswith("0X"))) else f"0x{v}"


# -------------------- Connection manager --------------------

class _ConnectionManager:
    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast_json(self, message: Dict[str, Any]) -> None:
        if not self.active:
            return
        dead: Set[WebSocket] = set()
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = _ConnectionManager()


# -------------------- Publisher --------------------

class RandomnessWSPublisher:
    """
    Async-capable broadcaster with convenience fire-and-forget wrappers.
    """

    def __init__(self, mgr: _ConnectionManager) -> None:
        self._mgr = mgr

    # ---- generic ----
    async def a_emit(self, event: str, data: Dict[str, Any]) -> None:
        await self._mgr.broadcast_json({"event": event, "data": data})

    def emit(self, event: str, data: Dict[str, Any]) -> None:
        # Fire-and-forget variant (assumes running loop)
        asyncio.create_task(self.a_emit(event, data))

    # ---- typed helpers ----
    async def a_round_opened(self, payload: Dict[str, Any] | RoundOpened) -> None:
        model = payload if isinstance(payload, dict) else payload.dict()
        await self.a_emit("roundOpened", model)

    def round_opened(self, payload: Dict[str, Any] | RoundOpened) -> None:
        asyncio.create_task(self.a_round_opened(payload))  # fire-and-forget

    async def a_round_closed(self, payload: Dict[str, Any] | RoundClosed) -> None:
        model = payload if isinstance(payload, dict) else payload.dict()
        await self.a_emit("roundClosed", model)

    def round_closed(self, payload: Dict[str, Any] | RoundClosed) -> None:
        asyncio.create_task(self.a_round_closed(payload))

    async def a_beacon_finalized(self, payload: Dict[str, Any] | BeaconFinalized) -> None:
        model = payload if isinstance(payload, dict) else payload.dict()
        await self.a_emit("beaconFinalized", model)

    def beacon_finalized(self, payload: Dict[str, Any] | BeaconFinalized) -> None:
        asyncio.create_task(self.a_beacon_finalized(payload))


publisher = RandomnessWSPublisher(_manager)


# -------------------- WS endpoint --------------------

@router.websocket("/ws/rand")
async def randomness_ws(ws: WebSocket) -> None:
    """
    Simple broadcast-only channel.
    - Client may optionally send "ping" (text) to receive "pong".
    - Any other inbound frames are ignored.
    """
    await _manager.connect(ws)
    try:
        # Lightweight keepalive / no backpressure; ignore payloads.
        while True:
            try:
                msg = await ws.receive_text()
                if msg.strip().lower() == "ping":
                    await ws.send_text("pong")
            except WebSocketDisconnect:
                break
            except Exception:
                # Swallow unexpected frame types / errors; keep connection alive if possible.
                await asyncio.sleep(0)
    finally:
        _manager.disconnect(ws)


__all__ = [
    "router",
    "publisher",
    "RoundOpened",
    "RoundClosed",
    "BeaconFinalized",
    "RandomnessWSPublisher",
]
