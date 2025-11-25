"""
randomness.adapters.rpc_mount
-----------------------------

Mount HTTP/JSON-RPC + WebSocket endpoints for the randomness beacon:
- REST (prefix `/rand` by default):
    GET  /status                → scheduler & round info
    GET  /beacon                → latest beacon
    GET  /beacon/{round_id}     → beacon for a specific round
    GET  /light_proof/{round_id}→ compact light-client proof for a round
    GET  /history               → paginated recent beacons
    POST /commit                → submit commit (commit–reveal, current round)
    POST /reveal                → submit reveal for a round
    POST /vdf_proof             → submit VDF proof for a round

- WS:
    /ws/randomness              → server-sent events (beaconFinalized, commitReceived,
                                  revealReceived, vdfAccepted). The concrete event stream
                                  is provided by the host via an EventSource.

- JSON-RPC (optional, if a registry/dispatcher is provided):
    rand.getStatus
    rand.getBeacon
    rand.getLightProof
    rand.getHistory
    rand.submitCommit
    rand.submitReveal
    rand.submitVdfProof

This module is transport glue only; concrete logic lives behind the injected
`RandomnessService` interface below.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional, Protocol

from fastapi import APIRouter, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Service & Event Protocols
# --------------------------------------------------------------------------------------

class RandomnessService(Protocol):
    """Abstracts the randomness beacon & commit–reveal/VDF plumbing."""

    # Reads
    async def get_status(self) -> dict: ...
    async def get_beacon(self, round_id: Optional[int] = None) -> Optional[dict]: ...
    async def get_light_proof(self, round_id: int) -> Optional[dict]: ...
    async def get_history(self, *, offset: int, limit: int) -> list[dict]: ...

    # Writes
    async def submit_commit(self, *, address: str, salt_hex: str, payload_hex: str) -> dict: ...
    async def submit_reveal(self, *, round_id: int, address: str, salt_hex: str, payload_hex: str) -> dict: ...
    async def submit_vdf_proof(self, *, round_id: int, y_hex: str, pi_hex: str, worker_id: Optional[str]) -> dict: ...


class EventSource(Protocol):
    """Produces server-side events as dicts suitable for JSON-serializing to clients."""

    def subscribe(self) -> AsyncIterator[dict]: ...


# --------------------------------------------------------------------------------------
# Pydantic request/response models (minimal; service returns plain dicts already)
# --------------------------------------------------------------------------------------

class CommitReq(BaseModel):
    address: str = Field(..., description="Bech32/hex address string")
    salt: str = Field(..., description="0x-prefixed hex salt")
    payload: str = Field(..., description="0x-prefixed hex payload (committed preimage)")


class RevealReq(BaseModel):
    round_id: int = Field(..., ge=0)
    address: str
    salt: str
    payload: str


class VdfProofReq(BaseModel):
    round_id: int = Field(..., ge=0)
    y: str = Field(..., description="0x-prefixed VDF output")
    pi: str = Field(..., description="0x-prefixed VDF proof")
    worker_id: Optional[str] = Field(None, description="Optional worker/rig id")


# --------------------------------------------------------------------------------------
# REST router
# --------------------------------------------------------------------------------------

def get_router(service: RandomnessService) -> APIRouter:
    r = APIRouter(prefix="/rand", tags=["randomness"])

    @r.get("/status")
    async def status() -> dict:
        return await service.get_status()

    @r.get("/beacon")
    async def latest_beacon() -> dict:
        b = await service.get_beacon(None)
        if b is None:
            raise HTTPException(status_code=404, detail="no beacon yet")
        return b

    @r.get("/beacon/{round_id}")
    async def beacon_by_round(round_id: int) -> dict:
        b = await service.get_beacon(round_id)
        if b is None:
            raise HTTPException(status_code=404, detail="beacon not found")
        return b

    @r.get("/light_proof/{round_id}")
    async def light_proof(round_id: int) -> dict:
        p = await service.get_light_proof(round_id)
        if p is None:
            raise HTTPException(status_code=404, detail="light proof not found")
        return p

    @r.get("/history")
    async def history(
        offset: int = Query(0, ge=0),
        limit: int = Query(32, ge=1, le=256),
    ) -> list[dict]:
        return await service.get_history(offset=offset, limit=limit)

    @r.post("/commit")
    async def post_commit(req: CommitReq) -> dict:
        return await service.submit_commit(address=req.address, salt_hex=req.salt, payload_hex=req.payload)

    @r.post("/reveal")
    async def post_reveal(req: RevealReq) -> dict:
        return await service.submit_reveal(
            round_id=req.round_id, address=req.address, salt_hex=req.salt, payload_hex=req.payload
        )

    @r.post("/vdf_proof")
    async def post_vdf(req: VdfProofReq) -> dict:
        return await service.submit_vdf_proof(
            round_id=req.round_id, y_hex=req.y, pi_hex=req.pi, worker_id=req.worker_id
        )

    return r


# --------------------------------------------------------------------------------------
# WebSocket (server-sent event stream)
# --------------------------------------------------------------------------------------

async def ws_randomness(websocket: WebSocket, events: Optional[EventSource]) -> None:
    await websocket.accept()
    try:
        if events is None:
            # Heartbeat-only fallback if no event source is provided
            while True:
                await websocket.send_json({"type": "heartbeat"})
                await asyncio.sleep(10.0)
        else:
            async for ev in events.subscribe():
                # Ensure the message is JSON-serializable
                try:
                    # Fast path: already JSON-safe (dict of primitives)
                    await websocket.send_text(json.dumps(ev))
                except (TypeError, ValueError):
                    # Best-effort stringification fallback
                    await websocket.send_text(json.dumps({"type": "event", "payload": str(ev)}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("randomness WS error: %s", e)
        try:
            await websocket.close()
        except Exception:
            pass


# --------------------------------------------------------------------------------------
# JSON-RPC registration helpers
# --------------------------------------------------------------------------------------

def _rpc_register(registry: Any, name: str, fn: Any) -> None:
    """
    Try a few common JSON-RPC registries:
      - .add_method(name, fn)
      - .add(name, fn)
      - .method(name)(fn)
      - .register(name, fn)
    """
    for attr in ("add_method", "add", "register"):
        if hasattr(registry, attr):
            getattr(registry, attr)(name, fn)  # type: ignore[misc]
            return
    # Decorator-style (aiohttp-json-rpc style)
    if hasattr(registry, "method"):
        decorator = getattr(registry, "method")
        try:
            decorator(name)(fn)  # type: ignore[misc]
            return
        except Exception:
            pass
    raise TypeError("Unsupported JSON-RPC registry; expected add_method/add/register/method")


def _bind_jsonrpc(service: RandomnessService, rpc_registry: Any) -> None:
    # Wrap all handlers as async callables with predictable param shapes
    async def _get_status(**_params: Any) -> dict:
        return await service.get_status()

    async def _get_beacon(round_id: Optional[int] = None, **_params: Any) -> Optional[dict]:
        return await service.get_beacon(round_id)

    async def _get_light_proof(round_id: int, **_params: Any) -> Optional[dict]:
        return await service.get_light_proof(round_id)

    async def _get_history(offset: int = 0, limit: int = 32, **_params: Any) -> list[dict]:
        return await service.get_history(offset=offset, limit=limit)

    async def _submit_commit(address: str, salt: str, payload: str, **_params: Any) -> dict:
        return await service.submit_commit(address=address, salt_hex=salt, payload_hex=payload)

    async def _submit_reveal(round_id: int, address: str, salt: str, payload: str, **_params: Any) -> dict:
        return await service.submit_reveal(round_id=round_id, address=address, salt_hex=salt, payload_hex=payload)

    async def _submit_vdf_proof(round_id: int, y: str, pi: str, worker_id: Optional[str] = None, **_p: Any) -> dict:
        return await service.submit_vdf_proof(round_id=round_id, y_hex=y, pi_hex=pi, worker_id=worker_id)

    _rpc_register(rpc_registry, "rand.getStatus", _get_status)
    _rpc_register(rpc_registry, "rand.getBeacon", _get_beacon)
    _rpc_register(rpc_registry, "rand.getLightProof", _get_light_proof)
    _rpc_register(rpc_registry, "rand.getHistory", _get_history)
    _rpc_register(rpc_registry, "rand.submitCommit", _submit_commit)
    _rpc_register(rpc_registry, "rand.submitReveal", _submit_reveal)
    _rpc_register(rpc_registry, "rand.submitVdfProof", _submit_vdf_proof)


# --------------------------------------------------------------------------------------
# Mount helper
# --------------------------------------------------------------------------------------

def mount_randomness_rpc(
    app: FastAPI,
    *,
    service: RandomnessService,
    rpc_registry: Optional[Any] = None,
    events: Optional[EventSource] = None,
    rest_prefix: str = "/rand",
    ws_path: str = "/ws/randomness",
) -> None:
    """
    Mount REST, optional JSON-RPC methods, and a WebSocket stream on the given FastAPI app.

    Parameters
    ----------
    app : FastAPI
        The main application instance.
    service : RandomnessService
        Implementation of beacon/commit–reveal/VDF operations.
    rpc_registry : Optional[Any]
        If provided, we register the JSON-RPC methods listed above via a duck-typed
        `.add_method/.add/.register/.method` API.
    events : Optional[EventSource]
        Event stream producer for the WS endpoint; if None, a heartbeat-only WS is mounted.
    rest_prefix : str
        Prefix for REST endpoints (default: '/rand').
    ws_path : str
        Path for the WebSocket endpoint (default: '/ws/randomness').
    """
    # REST
    router = get_router(service)
    # If a custom prefix is requested, override it here by remounting routes under a new prefix.
    if rest_prefix != "/rand":
        # Rebuild a router with the requested prefix
        router = APIRouter(prefix=rest_prefix, tags=["randomness"])
        # Copy endpoints from a fresh router made by get_router (keeps handlers)
        base = get_router(service)
        for route in base.routes:
            router.routes.append(route)
    app.include_router(router)

    # WS
    app.add_api_websocket_route(ws_path, lambda ws: ws_randomness(ws, events))

    # JSON-RPC
    if rpc_registry is not None:
        _bind_jsonrpc(service, rpc_registry)


__all__ = ["mount_randomness_rpc", "get_router", "RandomnessService", "EventSource"]
