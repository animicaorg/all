"""
FastAPI server for an Animica Compute Fabric (AICF) provider.

This template exposes a minimal, production-lean service for two job kinds:
  • "quantum" — executes via a simple local sampler (see quantum.py)
  • "ai"      — executes via a deterministic placeholder (demo only)

It wires request validation (Pydantic models), optional API-key auth,
CORS, structured logging, a small in-memory worker pool, and (optionally)
Prometheus /metrics if the dependency is installed.

Quickstart
----------
1) Set env (or edit a .env file your process manager will load):

    AICF_SERVICE_NAME="example-aicf-provider"
    AICF_HOST="0.0.0.0"
    AICF_PORT="8000"
    AICF_API_KEY="dev-secret"           # optional; omit to disable auth
    AICF_CORS_ALLOW_ORIGINS="*"         # or "https://your.app,https://other.app"
    AICF_WORKER_CONCURRENCY="2"
    AICF_WORKER_QUEUE_MAX="1024"
    AICF_WORKER_RESULT_TTL_S="3600"
    AICF_METRICS_ENABLED="true"         # requires prometheus_client installed

2) Run (dev):

    uvicorn aicf_provider.server:app --reload --host 0.0.0.0 --port 8000

3) Try it:

    # Health
    curl -s http://localhost:8000/healthz

    # Submit a quantum job
    curl -s -X POST http://localhost:8000/v1/jobs/quantum \
      -H 'Content-Type: application/json' \
      -H 'X-API-Key: dev-secret' \
      -d '{"kind":"quantum","n_qubits":3,"shots":128,"client_job_id":"demo-1"}'

    # Submit an AI job
    curl -s -X POST http://localhost:8000/v1/jobs/ai \
      -H 'Content-Type: application/json' \
      -H 'X-API-Key: dev-secret' \
      -d '{"kind":"ai","prompt":"Hello Animica!","max_tokens":16,"temperature":0.0}'

    # Poll a job (replace {id})
    curl -s http://localhost:8000/v1/jobs/{id} -H 'X-API-Key: dev-secret'

    # Wait for completion with a timeout (seconds)
    curl -s 'http://localhost:8000/v1/jobs/{id}/wait?timeout=5' -H 'X-API-Key: dev-secret'


Design notes
------------
- State is in-memory only (see worker.py). For production, back the job
  queue with Redis/SQS/Kafka and persist JobRecord objects to a DB.
- The "ai" execution path is a deterministic placeholder. Replace with
  your real model adapter in worker._ai_demo_generate (or plug a new one).
- API-key auth is optional: set AICF_API_KEY to enable.
- Prometheus metrics are optional: install prometheus_client and set
  AICF_METRICS_ENABLED=true.

Endpoints
---------
GET  /healthz                       — liveness
GET  /readyz                        — readiness (queue stats)
GET  /version                       — name & version
GET  /metrics                       — Prometheus (if enabled)
POST /v1/jobs/quantum               — enqueue a quantum job
POST /v1/jobs/ai                    — enqueue an AI job
GET  /v1/jobs/{job_id}              — fetch JobRecord (status/result)
GET  /v1/jobs/{job_id}/wait         — wait-until-done (long-poll), ?timeout=seconds
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import settings  # pydantic BaseSettings in config.py
from .models import (AIJobIn, AIResult, JobRecord, JobStatus, QuantumJobIn,
                     QuantumResult)
from .worker import ProviderWorker, get_worker

# -----------------------------------------------------------------------------
# Optional Prometheus
# -----------------------------------------------------------------------------

_METRICS_ENABLED = str(
    getattr(settings, "metrics_enabled", os.getenv("AICF_METRICS_ENABLED", "false"))
).lower() in {"1", "true", "yes", "on"}

try:  # optional; keep template easy to run
    if _METRICS_ENABLED:
        from prometheus_client import (CONTENT_TYPE_LATEST,  # type: ignore
                                       Counter, Gauge, Summary,
                                       generate_latest)

        REQ_COUNTER = Counter(
            "aicf_http_requests_total",
            "HTTP requests count",
            ["method", "path", "status"],
        )
        ENQUEUE_COUNTER = Counter(
            "aicf_jobs_enqueued_total", "Total enqueued jobs", ["kind"]
        )
        JOBS_RUNNING = Gauge("aicf_jobs_running", "Number of jobs running right now")
        JOBS_QUEUED = Gauge("aicf_jobs_queued", "Number of jobs queued right now")
        ENQUEUE_LATENCY = Summary(
            "aicf_enqueue_seconds", "Time to enqueue and ack a job", ["kind"]
        )
    else:
        raise ImportError("metrics disabled")
except Exception:  # pragma: no cover - graceful fallback
    _METRICS_ENABLED = False
    REQ_COUNTER = None  # type: ignore
    ENQUEUE_COUNTER = None  # type: ignore
    JOBS_RUNNING = None  # type: ignore
    JOBS_QUEUED = None  # type: ignore
    ENQUEUE_LATENCY = None  # type: ignore


# -----------------------------------------------------------------------------
# Auth dependency (optional API key)
# -----------------------------------------------------------------------------


class AuthContext(BaseModel):
    api_key_used: bool = False


async def require_api_key(
    x_api_key: Optional[str] = Header(default=None),
) -> AuthContext:
    """
    If settings.api_key is set, enforce X-API-Key header equality.
    Otherwise, allow all.
    """
    configured = getattr(settings, "api_key", None)
    if not configured:
        return AuthContext(api_key_used=False)

    if x_api_key != configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return AuthContext(api_key_used=True)


# -----------------------------------------------------------------------------
# App & lifespan
# -----------------------------------------------------------------------------


def _app_name() -> str:
    return str(getattr(settings, "service_name", "aicf-provider"))


def _app_version() -> str:
    return str(getattr(settings, "service_version", "0.1.0"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    On startup: nothing special (worker is autostarted on first enqueue).
    On shutdown: stop worker to finish tasks cleanly.
    """
    worker = get_worker()
    try:
        yield
    finally:
        # best-effort graceful stop (jobs may be in-flight)
        try:
            await worker.stop()
        except Exception:  # pragma: no cover - defensive
            pass


app = FastAPI(
    title=_app_name(),
    version=_app_version(),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
_cors: List[str]
_env_cors = os.getenv("AICF_CORS_ALLOW_ORIGINS")
if _env_cors:
    _cors = [o.strip() for o in _env_cors.split(",") if o.strip()]
else:
    _cors = getattr(settings, "cors_allow_origins", ["*"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging
logging.getLogger("uvicorn.error").setLevel(getattr(settings, "log_level", "INFO"))
logging.getLogger("uvicorn.access").setLevel(getattr(settings, "log_level", "INFO"))
log = logging.getLogger("aicf_provider.server")
log.setLevel(getattr(settings, "log_level", "INFO"))


# -----------------------------------------------------------------------------
# Schemas (responses)
# -----------------------------------------------------------------------------


class EnqueueResponse(BaseModel):
    job_id: str = Field(..., description="Server-generated job identifier")
    kind: str = Field(..., description='"quantum" or "ai"')
    status_url: str = Field(..., description="Where to poll this job")
    submitted_at: float = Field(..., description="Unix time (seconds, fractional)")


class ReadyzResponse(BaseModel):
    ok: bool
    queue_max: int
    queued: int
    running: int
    worker_concurrency: int


# -----------------------------------------------------------------------------
# Middleware (metrics)
# -----------------------------------------------------------------------------


@app.middleware("http")
async def _metrics_middleware(request, call_next):  # type: ignore[no-untyped-def]
    if not _METRICS_ENABLED:
        return await call_next(request)

    response = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        try:
            path = request.url.path
            method = request.method
            code = str(status_code if response is not None else 500)
            REQ_COUNTER.labels(method=method, path=path, status=code).inc()  # type: ignore[attr-defined]
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Utility
# -----------------------------------------------------------------------------


def _status_url(job_id: str) -> str:
    base = os.getenv("AICF_PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return f"{base}/v1/jobs/{job_id}"
    return f"/v1/jobs/{job_id}"


# -----------------------------------------------------------------------------
# Liveness / readiness / version / metrics
# -----------------------------------------------------------------------------


@app.get("/healthz", tags=["meta"])
async def healthz():
    return {"ok": True}


@app.get("/readyz", response_model=ReadyzResponse, tags=["meta"])
async def readyz():
    w: ProviderWorker = get_worker()
    # These attributes are internal; if changed, defaults will still render something sane.
    queued = getattr(w, "_q").qsize() if hasattr(w, "_q") else 0
    running = sum(1 for t in getattr(w, "_workers", []) if not t.done())
    maxsize = getattr(getattr(w, "_q", None), "maxsize", 0)
    conc = getattr(w, "concurrency", 0)

    if _METRICS_ENABLED:
        try:
            JOBS_RUNNING.set(running)  # type: ignore[union-attr]
            JOBS_QUEUED.set(queued)  # type: ignore[union-attr]
        except Exception:
            pass

    return ReadyzResponse(
        ok=True,
        queue_max=maxsize,
        queued=queued,
        running=running,
        worker_concurrency=conc,
    )


@app.get("/version", tags=["meta"])
async def version():
    return {
        "name": _app_name(),
        "version": _app_version(),
    }


@app.get("/metrics", tags=["meta"])
async def metrics():
    if not _METRICS_ENABLED:
        raise HTTPException(status_code=404, detail="metrics not enabled")
    try:
        body = generate_latest()  # type: ignore[call-arg]
        return Response(content=body, media_type=CONTENT_TYPE_LATEST)  # type: ignore[name-defined]
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"metrics error: {exc}") from exc


# -----------------------------------------------------------------------------
# Job endpoints
# -----------------------------------------------------------------------------


@app.post(
    "/v1/jobs/quantum",
    response_model=EnqueueResponse,
    status_code=201,
    tags=["jobs"],
)
async def enqueue_quantum(
    job: QuantumJobIn, _auth: AuthContext = Depends(require_api_key)
):
    """
    Enqueue a quantum job.

    Returns a job_id immediately; poll `/v1/jobs/{job_id}` for status/result.
    """
    worker = get_worker()
    try:
        if _METRICS_ENABLED:
            timer = ENQUEUE_LATENCY.labels(kind="quantum").time()  # type: ignore[union-attr]
        else:

            class _Nop:
                def __enter__(self):
                    return None

                def __exit__(self, *a):
                    return False

            timer = _Nop()

        with timer:  # type: ignore[assignment]
            job_id = await asyncio.wait_for(
                worker.enqueue(job),
                timeout=float(getattr(settings, "request_timeout_s", 15.0)),
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="queue is saturated (timeout)")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"enqueue failed: {exc}") from exc

    if _METRICS_ENABLED:
        try:
            ENQUEUE_COUNTER.labels(kind="quantum").inc()  # type: ignore[union-attr]
        except Exception:
            pass

    rec = worker.get(job_id)
    return EnqueueResponse(
        job_id=job_id,
        kind="quantum",
        status_url=_status_url(job_id),
        submitted_at=rec.submitted_at,
    )


@app.post(
    "/v1/jobs/ai",
    response_model=EnqueueResponse,
    status_code=201,
    tags=["jobs"],
)
async def enqueue_ai(job: AIJobIn, _auth: AuthContext = Depends(require_api_key)):
    """
    Enqueue an AI job (demo placeholder model).

    Returns a job_id immediately; poll `/v1/jobs/{job_id}` for status/result.
    """
    worker = get_worker()
    try:
        if _METRICS_ENABLED:
            timer = ENQUEUE_LATENCY.labels(kind="ai").time()  # type: ignore[union-attr]
        else:

            class _Nop:
                def __enter__(self):
                    return None

                def __exit__(self, *a):
                    return False

            timer = _Nop()

        with timer:  # type: ignore[assignment]
            job_id = await asyncio.wait_for(
                worker.enqueue(job),
                timeout=float(getattr(settings, "request_timeout_s", 15.0)),
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="queue is saturated (timeout)")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"enqueue failed: {exc}") from exc

    if _METRICS_ENABLED:
        try:
            ENQUEUE_COUNTER.labels(kind="ai").inc()  # type: ignore[union-attr]
        except Exception:
            pass

    rec = worker.get(job_id)
    return EnqueueResponse(
        job_id=job_id,
        kind="ai",
        status_url=_status_url(job_id),
        submitted_at=rec.submitted_at,
    )


@app.get(
    "/v1/jobs/{job_id}",
    response_model=JobRecord,
    tags=["jobs"],
)
async def get_job(job_id: str, _auth: AuthContext = Depends(require_api_key)):
    """
    Fetch the JobRecord (status/result/error).
    """
    worker = get_worker()
    try:
        return worker.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")


@app.get(
    "/v1/jobs/{job_id}/wait",
    response_model=JobRecord,
    tags=["jobs"],
)
async def wait_job(
    job_id: str,
    timeout: Optional[float] = None,
    _auth: AuthContext = Depends(require_api_key),
):
    """
    Long-poll until the job finishes or the timeout elapses.

    Query params:
      - timeout (float, seconds): if omitted, waits indefinitely.
    """
    worker = get_worker()
    try:
        rec = await worker.wait(job_id, timeout=timeout)
        return rec
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")
    except TimeoutError:
        # Return the *current* record for visibility if it exists
        try:
            return worker.get(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None


# -----------------------------------------------------------------------------
# __main__ (local dev convenience)
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Allows: python -m aicf_provider.server
    import uvicorn

    host = str(getattr(settings, "host", os.getenv("AICF_HOST", "0.0.0.0")))
    port = int(getattr(settings, "port", os.getenv("AICF_PORT", "8000")))
    reload = str(os.getenv("AICF_RELOAD", "false")).lower() in {"1", "true", "yes"}

    uvicorn.run(
        "aicf_provider.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level=str(getattr(settings, "log_level", "info")).lower(),
    )


__all__ = ["app"]
