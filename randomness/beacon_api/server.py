"""
randomness.beacon_api.server
============================

FastAPI app for the public Verifiable Quantum Randomness Beacon.

Endpoints (CORS-open, drand-flavoured):
  GET  /                      -> the /beacon page
  GET  /beacon                -> the /beacon page
  GET  /beacon/latest         -> latest round {round,value,prev,aggregate_commitment,...}
  GET  /beacon/round/{round}  -> a specific round (404 if unknown/pruned)
  GET  /beacon/chain?limit=   -> recent rounds (to verify the hash chain)
  GET  /beacon/info           -> params + how-to-verify + current mode
  GET  /draw?kind=&request_id=&round=&... -> a verifiable draw off a round's value
  POST /verify                -> {verified} (server recompute; the page verifies client-side)
  GET  /verify.js             -> the in-browser verifier (same math as the server)
  GET  /healthz               -> {ok:true}
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from randomness.qrng import public as P
from .driver import BeaconDriver

_STATIC = Path(__file__).resolve().parent / "static"


def _params_from_query(kind: str, q: Dict[str, Any]) -> Dict[str, Any]:
    def csv(name):
        v = q.get(name) or ""
        return [x for x in v.split(",") if x != ""]
    if kind == "lottery":
        return {"entries": csv("entries"), "k": int(q.get("k", 1))}
    if kind in ("choice", "shuffle"):
        return {"items": csv("items")}
    if kind == "weighted":
        return {"items": csv("items"), "weights": [int(x) for x in csv("weights")]}
    if kind == "range":
        return {"lo": int(q.get("lo", 0)), "hi": int(q.get("hi", 100)), "count": int(q.get("count", 1))}
    if kind == "coin":
        return {"count": int(q.get("count", 1))}
    if kind == "dice":
        return {"sides": int(q.get("sides", 6)), "count": int(q.get("count", 1))}
    if kind == "bytes":
        return {"n": int(q.get("n", 32))}
    raise HTTPException(400, f"unknown draw kind: {kind}")


def create_app(driver: Optional[BeaconDriver] = None, *, start: bool = True,
               interval_s: float = 30.0) -> FastAPI:
    drv = driver or BeaconDriver(interval_s=interval_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start:
            drv.start()
        try:
            yield
        finally:
            drv.stop()

    app = FastAPI(title="Animica Quantum Randomness Beacon", version="1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.driver = drv

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "current_round": drv.info()["current_round"]}

    @app.get("/beacon/latest")
    def latest():
        return drv.latest()

    @app.get("/beacon/round/{round_id}")
    def get_round(round_id: int):
        rec = drv.get(round_id)
        if rec is None:
            raise HTTPException(404, f"round {round_id} not available")
        return rec

    @app.get("/beacon/chain")
    def chain(limit: int = Query(50, ge=1, le=500)):
        return {"rounds": drv.chain(limit=limit)}

    @app.get("/beacon/info")
    def info():
        return drv.info()

    @app.get("/draw")
    def draw(
        kind: str = Query("dice"),
        request_id: str = Query(..., min_length=1),
        round: Optional[int] = Query(None),
        request: Request = None,  # type: ignore
    ):
        rec = drv.latest() if round is None else drv.get(round)
        if rec is None:
            raise HTTPException(404, f"round {round} not available")
        params = _params_from_query(kind, dict(request.query_params))
        try:
            beacon = bytes.fromhex(rec["value"])
            result = P.compute(kind, beacon, int(rec["round"]), request_id, params)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))
        result["verified"] = P.verify_result(result)  # the page re-verifies client-side too
        result["beacon_round"] = rec["round"]
        result["beacon_value"] = rec["value"]
        result["attested"] = rec["attested"]
        return result

    @app.post("/verify")
    async def verify(request: Request):
        body = await request.json()
        return {"verified": bool(P.verify_result(body))}

    @app.get("/verify.js")
    def verify_js():
        return FileResponse(_STATIC / "verify.js", media_type="application/javascript")

    @app.get("/")
    @app.get("/beacon")
    def page():
        return FileResponse(_STATIC / "beacon.html", media_type="text/html")

    return app


def run(host: str = "0.0.0.0", port: int = 8780, interval_s: float = 30.0) -> None:
    import uvicorn
    uvicorn.run(create_app(interval_s=interval_s), host=host, port=port)


if __name__ == "__main__":
    run()
