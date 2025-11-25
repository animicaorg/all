from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

try:
    # Prefer the repo's structured logger if available
    from core.logging import get_logger  # type: ignore
except Exception:  # pragma: no cover
    import logging as _logging

    def get_logger(name: str) -> _logging.Logger:
        _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        return _logging.getLogger(name)

try:
    # These imports are only needed when mounted into the main FastAPI app
    from fastapi import APIRouter, WebSocket, WebSocketDisconnect  # type: ignore
    from fastapi.websockets import WebSocketState  # type: ignore
except Exception:  # pragma: no cover
    # Allow importing this module for type checking without FastAPI present
    APIRouter = None  # type: ignore
    WebSocket = object  # type: ignore
    WebSocketDisconnect = Exception  # type: ignore
    WebSocketState = object  # type: ignore

log = get_logger("mining.ws_getwork")

JSON = Dict[str, Any]
SubmitCallback = Callable[[JSON], Awaitable[JSON]]  # params → submit-result JSON


@dataclass
class SubmitResult:
    accepted: bool
    is_block: bool = False
    reason: Optional[str] = None
    new_head: Optional[JSON] = None

    def to_json(self) -> JSON:
        out: JSON = {"accepted": self.accepted, "isBlock": self.is_block}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.new_head is not None:
            out["newHead"] = self.new_head
        return out


class WSGetWorkHub:
    """
    Hub that manages WS clients and broadcasts new work. It also exposes
    a simple JSON-RPC surface over WebSockets:

      → {"jsonrpc":"2.0","id":1,"method":"miner.getWork"}
      ← {"jsonrpc":"2.0","id":1,"result": { <template> }}

      → {"jsonrpc":"2.0","id":2,"method":"miner.submitShare",
          "params":{"jobId":"...","hashshare":{...},"proofs":[...],"txs":[...]}}
      ← {"jsonrpc":"2.0","id":2,"result":{"accepted":true,"isBlock":false}}

    Notifications:
      ← {"jsonrpc":"2.0","method":"miner.newWork","params":{ <template> }}
    """

    def __init__(self, submit_cb: Optional[SubmitCallback] = None) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._submit_cb: SubmitCallback = submit_cb or self._default_submit_cb
        self._template: Optional[JSON] = None
        self._template_update_evt = asyncio.Event()
        self._template_seq = 0
        self._shutdown = asyncio.Event()

    # ----------------- template management -----------------

    def set_work_template(self, template: JSON) -> None:
        """
        Install a fresh work template and broadcast to all connected clients.
        The template object should be JSON-serializable and typically contains:
          {
            "jobId": "abc123",
            "header": {...},        # compact header view
            "proofCaps": {...},     # policy snapshot (optional)
            "shareTarget": 0.001,   # suggested target for shares
            "thetaMicro": 1200000,  # current Θ in µ-nats
            "deadlineMs": 5000      # soft expiry for the template
          }
        """
        self._template = dict(template)
        self._template_seq += 1
        self._template_update_evt.set()
        # Event will be cleared by broadcaster task

    def get_work_template(self) -> Optional[JSON]:
        return self._template

    # ----------------- client lifecycle -----------------

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
            log.info(f"[ws] client connected; total={len(self._clients)}")

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._clients:
                self._clients.remove(ws)
                log.info(f"[ws] client disconnected; total={len(self._clients)}")

    async def broadcast(self, obj: JSON) -> None:
        """
        Fire-and-forget broadcast. Dead sockets are cleaned up on send errors.
        """
        msg = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        dead: List[WebSocket] = []
        async with self._lock:
            for ws in list(self._clients):
                try:
                    if getattr(ws, "application_state", None) is WebSocketState.CONNECTED:  # type: ignore
                        await ws.send_text(msg)
                    else:
                        dead.append(ws)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    await ws.close()
                except Exception:
                    pass
                self._clients.discard(ws)

    async def run_broadcaster(self) -> None:
        """
        Background task: whenever a new template is installed via set_work_template,
        push a miner.newWork notification to all clients.
        """
        while not self._shutdown.is_set():
            await self._template_update_evt.wait()
            self._template_update_evt.clear()
            if self._template is None:
                continue
            await self.broadcast({"jsonrpc": "2.0", "method": "miner.newWork", "params": self._template})

    async def shutdown(self) -> None:
        self._shutdown.set()
        await asyncio.sleep(0)

    # ----------------- submission -----------------

    async def submit_share(self, params: JSON) -> JSON:
        """
        Forward a submission to the configured callback; normalize response shape.
        """
        res = await self._submit_cb(params)
        # If callback returned a SubmitResult dataclass, convert; else pass-through JSON.
        if isinstance(res, SubmitResult):  # type: ignore
            return res.to_json()
        return res

    async def _default_submit_cb(self, params: JSON) -> JSON:  # pragma: no cover
        # Conservative default: reject unless a real bridge wires this in.
        return SubmitResult(accepted=False, reason="No submit bridge configured").to_json()

    # ----------------- WebSocket session -----------------

    async def serve_ws(self, ws: WebSocket) -> None:
        """
        Handle a single WebSocket session with text JSON frames.
        """
        await ws.accept()
        await self.register(ws)
        try:
            # On connect, optionally push the current work
            if self._template:
                await ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "miner.newWork", "params": self._template}))

            while True:
                raw = await ws.receive_text()
                try:
                    obj = json.loads(raw)
                except Exception:
                    await ws.send_text(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}))
                    continue

                # Basic JSON-RPC 2.0 handling
                jrpc = obj.get("jsonrpc")
                if jrpc != "2.0":
                    await ws.send_text(json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}}))
                    continue

                method = obj.get("method")
                req_id = obj.get("id")
                params = obj.get("params") or {}

                if method == "miner.getWork":
                    tpl = self.get_work_template()
                    if tpl is None:
                        # No work yet — return a clear shape so clients can retry/backoff
                        result = {"available": False, "retryAfterMs": 1000}
                    else:
                        result = dict(tpl)
                        result["available"] = True
                        result.setdefault("serverTimeMs", int(time.time() * 1000))
                    await ws.send_text(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}))

                elif method == "miner.submitShare":
                    try:
                        submit_res = await self.submit_share(params)
                        await ws.send_text(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": submit_res}))
                    except Exception as e:
                        log.warning(f"[ws] submit error: {e}")
                        await ws.send_text(json.dumps({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32000, "message": "Submit failed", "data": str(e)},
                        }))

                elif method == "ping":
                    await ws.send_text(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"pong": True}}))

                else:
                    await ws.send_text(json.dumps({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }))
        except WebSocketDisconnect:  # pragma: no cover
            pass
        except Exception as e:  # pragma: no cover
            log.warning(f"[ws] session error: {e}")
        finally:
            await self.unregister(ws)
            try:
                await ws.close()
            except Exception:
                pass


# --------------------------- FastAPI router ---------------------------

# The router can be mounted by mining/bridge_rpc.py into the main RPC FastAPI app.
router = APIRouter() if APIRouter is not None else None  # type: ignore
_hub = WSGetWorkHub()  # module-level singleton hub


def get_hub() -> WSGetWorkHub:
    """
    Accessor used by bridge code (and tests) to push new templates or set submit callback.
    """
    return _hub


if router is not None:
    @router.websocket("/ws/getwork")
    async def ws_getwork_endpoint(ws: WebSocket) -> None:  # type: ignore
        """
        WebSocket endpoint. Exposes:

          - miner.getWork
          - miner.submitShare
          - notifications: miner.newWork
          - ping

        Clients send/receive text JSON frames (JSON-RPC 2.0).
        """
        await _hub.serve_ws(ws)

# --------------------------- Bridge helpers ---------------------------

async def start_background_broadcaster(loop: Optional[asyncio.AbstractEventLoop] = None) -> asyncio.Task:
    """
    Launch the hub's broadcaster task. Call this once during app startup.
    """
    loop = loop or asyncio.get_event_loop()
    return loop.create_task(_hub.run_broadcaster())


def wire_submit_callback(cb: SubmitCallback) -> None:
    """
    Install a real submit callback that takes params and returns a SubmitResult JSON.
    Typical bridge will call mining.share_submitter.ShareSubmitter.
    """
    # Rebind by wrapping hub method
    _hub._submit_cb = cb


def install_template_provider(provider: Callable[[], Awaitable[Optional[JSON]]],
                              interval_sec: float = 1.0) -> asyncio.Task:
    """
    Periodically poll a coroutine provider() → template (or None) and install when it changes.
    Useful for wiring the mining.templates.TemplateBuilder or the orchestrator.
    """
    async def _runner() -> None:
        last_id: Optional[str] = None
        while True:
            try:
                tpl = await provider()
                if tpl:
                    jid = str(tpl.get("jobId") or "")
                    if jid and jid != last_id:
                        last_id = jid
                        _hub.set_work_template(tpl)
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[ws] template provider error: {e}")
                await asyncio.sleep(max(2 * interval_sec, 1.0))

    loop = asyncio.get_event_loop()
    return loop.create_task(_runner())


# --------------------------- Local dev main ---------------------------

if __name__ == "__main__":  # pragma: no cover
    # Tiny self-host for local dev: `python -m mining.ws_getwork`
    import uvicorn  # type: ignore
    from fastapi import FastAPI  # type: ignore

    app = FastAPI()
    if router is not None:
        app.include_router(router)

    @app.on_event("startup")
    async def _startup() -> None:
        await asyncio.sleep(0)
        # Kick off broadcaster
        asyncio.create_task(_hub.run_broadcaster())

        # Demo: rotate a fake template every 5s (development only)
        async def demo_provider() -> Optional[JSON]:
            ts = int(time.time())
            job_id = f"demo-{ts // 5}"
            return {
                "jobId": job_id,
                "header": {"parentHash": "0x" + "00"*32, "height": ts // 5},
                "shareTarget": 0.0015,
                "thetaMicro": 1_250_000,
                "deadlineMs": 5000,
                "serverTimeMs": int(time.time() * 1000),
            }

        install_template_provider(demo_provider, interval_sec=1.0)

        async def demo_submit(params: JSON) -> JSON:
            # Accept everything in demo mode
            return SubmitResult(accepted=True, is_block=False).to_json()

        wire_submit_callback(demo_submit)

    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="info")
