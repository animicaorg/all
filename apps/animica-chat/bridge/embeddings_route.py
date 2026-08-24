"""POST /v1/embeddings — OpenAI-compatible embeddings served by the worker swarm.

The request is sliced into ≤64-input jobs and submitted to the AICF queue as
``kind: "embed"`` jobs (K=1: same model ⇒ same vector, so there is nothing to
race). Workers that advertise ``hardware.kinds: ["embed"]`` — the browser
worker, the Animica Serve app, the Termux lane — run bge-small-en-v1.5 and
return one line::

    EMB1 <model> <dims> f16 <base64 little-endian float16 N×dims> <sha256 hex>

which is verified (byte length, sha256, row count) before a single vector is
trusted. Anything else — no embed-capable worker online, a garbage submit from
a chat-only worker, a timeout — falls back to the local MiniLM service on
:4630, and the response's ``model`` field always names the model that really
produced the vectors (the two are NOT in the same vector space).

Payments: this route owns its own DistributedAICFProvider (own wallet object,
own nonce sequence) and serialises sign+submit under a lock so concurrent
embedding requests can't race the ML-DSA payment signature the way a shared
provider would.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import threading
import time
from typing import Any, Optional, Union

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger("bridge.embeddings")

EMBED_MODEL = os.environ.get("BRIDGE_EMBED_MODEL", "bge-small-en-v1.5")
EMBED_DIMS = int(os.environ.get("BRIDGE_EMBED_DIMS", "384"))
EMBED_ALIASES = {"animica-embed", "anm-embed", "text-embedding-3-small", EMBED_MODEL, ""}
LOCAL_EMBED_URL = os.environ.get("BRIDGE_EMBED_LOCAL_URL", "http://127.0.0.1:4630/embed")
LOCAL_EMBED_MODEL = "all-MiniLM-L6-v2"
SWARM_ENABLED = os.environ.get("BRIDGE_EMBED_SWARM", "1").strip().lower() in {"1", "true", "on"}
SWARM_TIMEOUT_S = float(os.environ.get("BRIDGE_EMBED_SWARM_TIMEOUT_S", "45"))
SWARM_TIER = os.environ.get("BRIDGE_EMBED_TIER", "standard")
SLICE = 64
MAX_INPUTS = int(os.environ.get("BRIDGE_EMBED_MAX_INPUTS", "256"))
MAX_CHARS = 8000
# Circuit breaker: after this many consecutive swarm failures, go local-only
# for BREAKER_S so a dead swarm doesn't add a 45 s stall to every request.
BREAKER_FAILS = 3
BREAKER_S = 60.0


class EmbeddingsRequest(BaseModel):
    input: Union[str, list[str]]
    model: Optional[str] = None
    encoding_format: Optional[str] = "float"
    user: Optional[str] = None


class _Breaker:
    def __init__(self) -> None:
        self.fails = 0
        self.open_until = 0.0
        self.lock = threading.Lock()

    def ok(self) -> bool:
        return time.monotonic() >= self.open_until

    def success(self) -> None:
        with self.lock:
            self.fails = 0

    def failure(self) -> None:
        with self.lock:
            self.fails += 1
            if self.fails >= BREAKER_FAILS:
                self.open_until = time.monotonic() + BREAKER_S
                self.fails = 0
                log.warning("embeddings: swarm breaker OPEN for %ss", BREAKER_S)


_breaker = _Breaker()
_provider = None
_provider_lock = threading.Lock()
_submit_lock = threading.Lock()


def _get_provider():
    """Own provider instance (own wallet object) — never the chat or ping one."""
    global _provider
    if _provider is not None:
        return _provider
    with _provider_lock:
        if _provider is not None:
            return _provider
        from agent_runtime.config import load_config
        from agent_runtime.providers import DistributedAICFProvider
        cfg = load_config()
        network = os.environ.get("ANIMICA_NETWORK", "mainnet")
        rpc_url = (os.environ.get("ANIMICA_RPC_URL")
                   or cfg.integration["aicf"]["endpoint"].get(network)
                   or cfg.integration["aicf"]["endpoint"]["mainnet"])
        cfg.integration["aicf"]["job_submit"]["timeout_sec"] = SWARM_TIMEOUT_S
        _provider = DistributedAICFProvider(
            cfg=cfg,
            rpc_url=rpc_url,
            wallet_path=os.environ.get("ANIMICA_BRIDGE_WALLET_PATH") or None,
            wallet_label=os.environ.get("ANIMICA_BRIDGE_WALLET_LABEL", "aicf"),
        )
        return _provider


# ── wire format ───────────────────────────────────────────────────────────

def decode_emb1(text: str, n_expected: int) -> tuple[str, list[list[float]]]:
    """Parse + verify an EMB1 line. Raises ValueError on any inconsistency."""
    parts = (text or "").strip().split()
    if len(parts) != 6 or parts[0] != "EMB1" or parts[3] != "f16":
        raise ValueError("not an EMB1 line")
    model, dims_s, b64, sha = parts[1], parts[2], parts[4], parts[5]
    dims = int(dims_s)
    raw = base64.b64decode(b64, validate=True)
    if hashlib.sha256(raw).hexdigest() != sha.lower():
        raise ValueError("EMB1 sha256 mismatch")
    if dims <= 0 or len(raw) != n_expected * dims * 2:
        raise ValueError(f"EMB1 size mismatch: {len(raw)} bytes for {n_expected}x{dims}")
    flat = struct.unpack(f"<{n_expected * dims}e", raw)
    vecs = [list(flat[i * dims:(i + 1) * dims]) for i in range(n_expected)]
    for v in vecs:
        if not all(x == x for x in v):   # NaN guard
            raise ValueError("EMB1 contains NaN")
    return model, vecs


def _approx_tokens(texts: list[str]) -> int:
    return max(1, sum(len(t) for t in texts) // 4)


# ── swarm path (blocking; run in a thread) ────────────────────────────────

def _swarm_embed_slice(texts: list[str]) -> tuple[str, list[list[float]]]:
    from agent_runtime.wallet import get_next_nonce, sign_payment
    prov = _get_provider()
    client = prov.client
    wi = prov._wallet_info()
    digest = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest()
    spec: dict[str, Any] = {
        "kind": "embed",
        "model": EMBED_MODEL,
        "inputs": texts,
        "dims": EMBED_DIMS,
        "normalize": True,
        # The prompt carries the inputs too: the per-job credit is derived
        # from prompt tokens, so the worker is paid for the text it embeds,
        # and a worker on an older page can still find the inputs.
        "prompt": "[embed] " + digest[:16] + "\n" + json.dumps(texts, ensure_ascii=False),
        "tier_preferred": SWARM_TIER,
        "job_kind": "AI",
        "max_output_tokens": 1,
        "temperature": 0.0,
        "top_p": 1.0,
        "stop": [],
        "metadata": {"embed": True, "n": len(texts)},
        "mode": "race",
        "replicas": 1,
    }
    quote = client._rpc("aicf.estimateJobCost", {
        "prompt_tokens": _approx_tokens(texts),
        "max_output_tokens": 1,
        "tier_preferred": SWARM_TIER,
        "job_kind": "AI",
    })
    cost = float((quote or {}).get("cost_animica", 0.0))
    recipient = ""
    try:
        tres = client._rpc("aicf.getTreasuryAddress", {})
        if isinstance(tres, dict):
            recipient = str(tres.get("treasury_address") or "")
    except Exception:  # noqa: BLE001
        recipient = ""
    if not recipient:
        recipient = str(prov.cfg.integration["aicf"].get("treasury_address", "aicf-treasury"))
    with _submit_lock:
        nonce = get_next_nonce(prov.rpc_url, wi.address)
        signed = sign_payment(
            wi, amount_animica=cost, recipient=recipient, chain_id=wi.chain_id,
            nonce=nonce, rpc_url=prov.rpc_url,
            job_metadata={"job_kind": "AI", "tier": SWARM_TIER},
        )
        sub = client._rpc("aicf.submitInferenceJob", {"spec": spec, "payment": signed.__dict__})
    job_id = str((sub or {}).get("job_id") or "")
    if not job_id:
        raise RuntimeError("submitInferenceJob returned no job_id")
    deadline = time.monotonic() + SWARM_TIMEOUT_S
    while time.monotonic() < deadline:
        st = client._rpc("aicf.jobStatus", {"job_id": job_id}) or {}
        state = str(st.get("state") or "")
        if state == "completed":
            model, vecs = decode_emb1(str(st.get("text") or ""), len(texts))
            return model, vecs
        if state == "failed":
            raise RuntimeError(f"embed job failed: {st.get('error')}")
        time.sleep(0.4)
    raise TimeoutError(f"embed job {job_id[:10]} not completed in {SWARM_TIMEOUT_S}s")


async def _local_embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60.0) as hc:
        for i in range(0, len(texts), SLICE):
            r = await hc.post(LOCAL_EMBED_URL, json={"texts": texts[i:i + SLICE]})
            r.raise_for_status()
            out.extend(r.json()["vectors"])
    return out


class _TextsRequest(BaseModel):
    texts: list[str]


def install_embeddings_route(app: FastAPI) -> None:
    @app.get("/v1/healthz")
    async def embed_healthz() -> JSONResponse:
        """What the x402 embed product probes before advertising itself
        (`${embedUrl}/healthz`). Healthy when either backend can answer."""
        local_ok = False
        try:
            async with httpx.AsyncClient(timeout=3.0) as hc:
                r = await hc.get(LOCAL_EMBED_URL.rsplit("/", 1)[0] + "/healthz")
                local_ok = r.status_code == 200
        except Exception:  # noqa: BLE001
            local_ok = False
        swarm_ok = SWARM_ENABLED and _breaker.ok()
        body = {"ok": local_ok or swarm_ok, "local": local_ok, "swarm": swarm_ok,
                "model": EMBED_MODEL, "fallback_model": LOCAL_EMBED_MODEL, "dims": EMBED_DIMS}
        return JSONResponse(body, status_code=200 if body["ok"] else 503)

    @app.post("/v1/embed")
    async def embed_compat(req: _TextsRequest, request: Request) -> JSONResponse:
        """Drop-in for the deploy indexer's ``POST /embed`` ({texts} → {vectors})
        so the x402 ``/x402/embed`` product moves to the swarm by changing one
        URL. Adds ``model`` so callers can tell bge from MiniLM vectors."""
        inner = await embeddings(EmbeddingsRequest(input=req.texts), request)
        body = json.loads(bytes(inner.body))
        return JSONResponse(
            {"vectors": [d["embedding"] for d in body["data"]], "model": body["model"]},
            headers=dict(inner.headers),
        )

    @app.post("/v1/embeddings")
    async def embeddings(req: EmbeddingsRequest, request: Request) -> JSONResponse:
        texts = [req.input] if isinstance(req.input, str) else list(req.input)
        if not texts or len(texts) > MAX_INPUTS:
            raise HTTPException(400, f"input must be 1..{MAX_INPUTS} strings")
        if any((not isinstance(t, str)) or not t.strip() or len(t) > MAX_CHARS for t in texts):
            raise HTTPException(400, f"each input must be a non-empty string ≤ {MAX_CHARS} chars")
        want = (req.model or "").strip()
        if want and want not in EMBED_ALIASES and want != LOCAL_EMBED_MODEL:
            raise HTTPException(404, f"unknown embedding model {want!r}; use {EMBED_MODEL}")

        source = "local"
        model = LOCAL_EMBED_MODEL
        vectors: Optional[list[list[float]]] = None
        t0 = time.monotonic()
        if SWARM_ENABLED and want != LOCAL_EMBED_MODEL and _breaker.ok():
            slices = [texts[i:i + SLICE] for i in range(0, len(texts), SLICE)]
            try:
                results = await asyncio.gather(
                    *[asyncio.to_thread(_swarm_embed_slice, s) for s in slices])
                models = {m for m, _ in results}
                vectors = [v for _, vecs in results for v in vecs]
                model = models.pop() if len(models) == 1 else EMBED_MODEL
                source = "swarm"
                _breaker.success()
            except Exception as exc:  # noqa: BLE001
                _breaker.failure()
                log.warning("embeddings: swarm path failed (%s) — local fallback", str(exc)[:160])
                vectors = None
        if vectors is None:
            try:
                vectors = await _local_embed(texts)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(503, f"no embedding backend available: {exc}") from exc

        if (req.encoding_format or "float") == "base64":
            def enc(v: list[float]) -> str:
                return base64.b64encode(struct.pack(f"<{len(v)}f", *v)).decode()
            data = [{"object": "embedding", "index": i, "embedding": enc(v)} for i, v in enumerate(vectors)]
        else:
            data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
        ntok = _approx_tokens(texts)
        body = {
            "object": "list",
            "data": data,
            "model": model,
            "usage": {"prompt_tokens": ntok, "total_tokens": ntok},
        }
        return JSONResponse(body, headers={
            "x-animica-embed-source": source,
            "x-animica-embed-ms": str(int((time.monotonic() - t0) * 1000)),
        })
