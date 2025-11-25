from __future__ import annotations

"""
Animica mining.ai_worker
========================

Purpose
-------
Lightweight async helper used by the miner to:
  1) enqueue small AI jobs (e.g., "score this tiny prompt") to the AI Compute
     Fund (AICF) or a local simulator backend,
  2) watch for completion,
  3) expose completed *result records* that can be transformed into on-chain
     AIProof references by higher-level adapters (see capabilities/jobs/*
     and capabilities/adapters/proofs.py).

This worker intentionally does **not** assemble the AIProof itself. It returns a
normalized "ResultRecord-like" dict containing:
    {
      "kind": "AI",
      "task_id": "...",
      "provider_id": "prov-…",         # when known
      "output_digest": b"...32bytes…", # sha3_256 of canonical output bytes
      "attestation": {...},            # opaque provider attestation bundle
      "metrics": {"ai_units": int, "qos": float, "latency_ms": int, ...},
      "completed_at": float,           # epoch seconds
    }
which is enough for the proof-building/attest-bridge layer to form a proper
AIProof envelope and nullifier (see proofs/* and capabilities/jobs/attest_bridge.py).

Backends
--------
- HttpAICFBackend: optional thin HTTP client to an AICF-compatible service.
- DevSimBackend: deterministic simulator (no network). Always available.
  It uses sha3_256(prompt||model||salt) and pretends to finish after a short delay.
  Deterministic under the same inputs.

Usage (async)
-------------
    worker = AiWorker.create_from_env()
    await worker.start()
    ticket = await worker.enqueue(model="tiny-devnet", prompt=b"Hello AI")
    # ... later ...
    ready = worker.pop_ready(max_n=8)
    for rec in ready:
        # hand to proof/adapters layer
        pass
    await worker.stop()

Config (env, optional)
----------------------
AICF_URL: base URL to AICF HTTP API (if unset, DevSimBackend is used)
AICF_API_KEY: bearer token for AICF (optional)
AI_WORKER_POLL_MS: poll interval (default: 500 ms)
AI_WORKER_QUEUE: local queue cap (default: 128)

No third-party dependencies; pure stdlib. Falls back gracefully.

"""

import asyncio
import contextlib
import json
import os
import time
import typing as t
from dataclasses import dataclass, field

# Prefer canonical sha3_256 from our stack if available
try:
    from proofs.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()

# ---------- Types ----------

@dataclass(frozen=True)
class AIJobSpec:
    model: str
    prompt: bytes
    max_tokens: int = 64
    temperature: float = 0.0
    qos_hint: t.Optional[str] = None  # e.g., "low-latency" | "best-effort"
    salt: bytes = b""  # optional extra for determinism or separation

@dataclass
class AITicket:
    task_id: str
    submitted_at: float
    status: str = "queued"  # queued|running|completed|failed|unknown
    provider_id: t.Optional[str] = None
    error: t.Optional[str] = None

@dataclass
class ResultRecord:
    kind: str  # "AI"
    task_id: str
    provider_id: t.Optional[str]
    output_digest: bytes
    attestation: t.Dict[str, t.Any]
    metrics: t.Dict[str, t.Any]
    completed_at: float


# ---------- Backend interface ----------

class AICFBackend(t.Protocol):
    async def enqueue(self, spec: AIJobSpec) -> AITicket: ...
    async def status(self, task_id: str) -> AITicket: ...
    async def fetch_result(self, task_id: str) -> ResultRecord: ...


# ---------- Dev simulator backend (always available) ----------

class DevSimBackend:
    """
    Deterministic, no-network simulator. Produces:
      - provider_id = "devsim"
      - output_digest = sha3_256(model||0x00||prompt||0x00||salt)
      - latency ~ 0.4s (configurable via env AI_SIM_LAT_MS)
      - ai_units proportional to len(prompt) and max_tokens
    """
    def __init__(self) -> None:
        self._store: dict[str, tuple[AIJobSpec, float, float]] = {}
        self._lat_ms = int(os.getenv("AI_SIM_LAT_MS", "400"))

    async def enqueue(self, spec: AIJobSpec) -> AITicket:
        now = time.time()
        task_id = self._derive_task_id(spec)
        # if already present, treat as idempotent
        if task_id not in self._store:
            done_at = now + (self._lat_ms / 1000.0)
            self._store[task_id] = (spec, now, done_at)
        return AITicket(task_id=task_id, submitted_at=now, status="queued", provider_id="devsim")

    async def status(self, task_id: str) -> AITicket:
        rec = self._store.get(task_id)
        if not rec:
            return AITicket(task_id=task_id, submitted_at=time.time(), status="unknown", provider_id="devsim")
        spec, sub, done_at = rec
        st = "completed" if time.time() >= done_at else "running"
        return AITicket(task_id=task_id, submitted_at=sub, status=st, provider_id="devsim")

    async def fetch_result(self, task_id: str) -> ResultRecord:
        rec = self._store.get(task_id)
        if not rec:
            raise RuntimeError("task not found")
        spec, sub, done_at = rec
        if time.time() < done_at:
            # simulate race (caller should only fetch after "completed")
            await asyncio.sleep(max(0.0, done_at - time.time()))
        output_digest = sha3_256(spec.model.encode() + b"\x00" + spec.prompt + b"\x00" + spec.salt)
        ai_units = max(8, int(0.05 * (len(spec.prompt) + spec.max_tokens)))
        qos = 0.99 if self._lat_ms <= 500 else 0.95
        metrics = {
            "ai_units": ai_units,
            "qos": qos,
            "latency_ms": int((time.time() - sub) * 1000),
            "temperature": spec.temperature,
            "max_tokens": spec.max_tokens,
            "model": spec.model,
        }
        att = {
            "provider": "devsim",
            "note": "deterministic simulated attestation (dev-only)",
            "inputs_hash_hex": sha3_256(b"inputs:" + spec.prompt).hex(),
        }
        return ResultRecord(
            kind="AI",
            task_id=task_id,
            provider_id="devsim",
            output_digest=output_digest,
            attestation=att,
            metrics=metrics,
            completed_at=time.time(),
        )

    def _derive_task_id(self, spec: AIJobSpec) -> str:
        # Deterministic, content-addressy ID (suitable for idempotency)
        h = sha3_256(b"animica.task.ai" + b"\x00" + spec.model.encode() + b"\x00" + spec.prompt + b"\x00" + spec.salt)
        return "ai-" + h.hex()[:32]


# ---------- Optional HTTP backend (best-effort, minimal) ----------

class HttpAICFBackend:
    """
    Extremely small HTTP client for an AICF-like service.

    Expected endpoints (JSON):
      POST {base}/jobs/ai          -> { task_id }
      GET  {base}/jobs/{task_id}   -> { status, provider_id? }
      GET  {base}/jobs/{task_id}/result
                                  -> { provider_id, output_digest_hex,
                                       attestation, metrics, completed_at }
    Authentication:
      - If AICF_API_KEY is set, adds Authorization: Bearer <key>
    """
    def __init__(self, base_url: str, api_key: str | None = None, timeout_s: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout_s

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.key:
            h["authorization"] = f"Bearer {self.key}"
        return h

    async def enqueue(self, spec: AIJobSpec) -> AITicket:
        body = {
            "model": spec.model,
            "prompt_b64": _b64(spec.prompt),
            "max_tokens": spec.max_tokens,
            "temperature": spec.temperature,
            "qos": spec.qos_hint,
            "salt_b64": _b64(spec.salt),
        }
        data = await _http_json("POST", f"{self.base}/jobs/ai", headers=self._headers(), json_body=body, timeout=self.timeout)
        task_id = t.cast(str, data.get("task_id", ""))
        if not task_id:
            raise RuntimeError("AICF enqueue: missing task_id")
        return AITicket(task_id=task_id, submitted_at=time.time(), status="queued")

    async def status(self, task_id: str) -> AITicket:
        data = await _http_json("GET", f"{self.base}/jobs/{task_id}", headers=self._headers(), timeout=self.timeout)
        st = t.cast(str, data.get("status", "unknown"))
        prov = t.cast(t.Optional[str], data.get("provider_id"))
        return AITicket(task_id=task_id, submitted_at=time.time(), status=st, provider_id=prov)

    async def fetch_result(self, task_id: str) -> ResultRecord:
        data = await _http_json("GET", f"{self.base}/jobs/{task_id}/result", headers=self._headers(), timeout=self.timeout)
        out_hex = t.cast(str, data.get("output_digest_hex", ""))
        if len(out_hex) != 64:
            raise RuntimeError("AICF result: bad output_digest_hex")
        return ResultRecord(
            kind="AI",
            task_id=task_id,
            provider_id=t.cast(t.Optional[str], data.get("provider_id")),
            output_digest=bytes.fromhex(out_hex),
            attestation=t.cast(dict, data.get("attestation", {})),
            metrics=t.cast(dict, data.get("metrics", {})),
            completed_at=float(data.get("completed_at", time.time())),
        )


# ---------- Worker ----------

@dataclass
class _Pending:
    spec: AIJobSpec
    ticket: AITicket
    last_polled: float = field(default_factory=lambda: 0.0)

class AiWorker:
    """
    Orchestrates AI job lifecycle for the miner.

    - enqueue(spec) -> returns ticket
    - background poller updates tickets and pulls finished results
    - pop_ready(max_n) -> returns completed ResultRecord items (FIFO)
    """
    def __init__(
        self,
        backend: AICFBackend,
        poll_interval_s: float = 0.5,
        queue_limit: int = 128,
    ) -> None:
        self._backend = backend
        self._poll_interval = float(poll_interval_s)
        self._queue_limit = int(queue_limit)

        self._pending: dict[str, _Pending] = {}
        self._ready: asyncio.Queue[ResultRecord] = asyncio.Queue(maxsize=queue_limit)
        self._task: asyncio.Task | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    @classmethod
    def create_from_env(cls) -> "AiWorker":
        base = os.getenv("AICF_URL")
        key = os.getenv("AICF_API_KEY")
        poll_ms = int(os.getenv("AI_WORKER_POLL_MS", "500"))
        cap = int(os.getenv("AI_WORKER_QUEUE", "128"))
        if base:
            backend: AICFBackend = HttpAICFBackend(base_url=base, api_key=key)
        else:
            backend = DevSimBackend()
        return cls(backend=backend, poll_interval_s=poll_ms / 1000.0, queue_limit=cap)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="AiWorker.poller")

    async def stop(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        # drain (best effort)
        self._pending.clear()

    async def enqueue(self, *, model: str, prompt: bytes, max_tokens: int = 64,
                      temperature: float = 0.0, qos_hint: str | None = None,
                      salt: bytes = b"") -> AITicket:
        spec = AIJobSpec(model=model, prompt=bytes(prompt), max_tokens=max_tokens,
                         temperature=temperature, qos_hint=qos_hint, salt=salt)
        async with self._lock:
            if len(self._pending) >= self._queue_limit:
                raise RuntimeError("ai_worker queue full")
            ticket = await self._backend.enqueue(spec)
            self._pending[ticket.task_id] = _Pending(spec=spec, ticket=ticket)
            return ticket

    def pop_ready(self, max_n: int = 32) -> list[ResultRecord]:
        items: list[ResultRecord] = []
        for _ in range(max_n):
            try:
                items.append(self._ready.get_nowait())
                self._ready.task_done()
            except asyncio.QueueEmpty:
                break
        return items

    # ------------- internals -------------

    async def _run(self) -> None:
        try:
            while not self._closed:
                await self._poll_once()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:  # normal shutdown
            return

    async def _poll_once(self) -> None:
        # snapshot to avoid holding lock across awaits unnecessarily
        async with self._lock:
            ids = list(self._pending.keys())

        for task_id in ids:
            pn = self._pending.get(task_id)
            if pn is None:
                continue
            now = time.time()
            # backoff local polling per ticket to avoid spamming
            if now - pn.last_polled < self._poll_interval * 0.8:
                continue
            pn.last_polled = now
            # query status
            try:
                st = await self._backend.status(task_id)
                pn.ticket = st
            except Exception as e:  # transient error => keep and retry
                pn.ticket.status = "unknown"
                pn.ticket.error = str(e)
                continue

            if st.status == "completed":
                # fetch result
                try:
                    rec = await self._backend.fetch_result(task_id)
                    await self._offer_ready(rec)
                    # remove from pending
                    async with self._lock:
                        self._pending.pop(task_id, None)
                except Exception as e:
                    pn.ticket.status = "failed"
                    pn.ticket.error = f"fetch_result: {e!r}"
            elif st.status in ("failed", "unknown"):
                # drop it (caller may re-enqueue)
                async with self._lock:
                    self._pending.pop(task_id, None)

    async def _offer_ready(self, rec: ResultRecord) -> None:
        try:
            self._ready.put_nowait(rec)
        except asyncio.QueueFull:
            # If the consumer isn't draining, drop the oldest element to make room
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = self._ready.get_nowait()
                self._ready.task_done()
            self._ready.put_nowait(rec)


# ---------- tiny HTTP helper (stdlib only) ----------

async def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, t.Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, t.Any]:
    import urllib.request
    import urllib.error

    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
    req = urllib.request.Request(url=url, method=method.upper(), data=data, headers=headers or {})
    if data is not None and "content-type" not in req.headers:
        req.add_header("content-type", "application/json")
    loop = asyncio.get_running_loop()

    def _do() -> dict[str, t.Any]:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                return t.cast(dict[str, t.Any], json.loads(raw.decode()))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"http {e.code}: {e.read().decode(errors='ignore')[:256]}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"http error: {e.reason}") from None

    return await loop.run_in_executor(None, _do)


# ---------- misc utils ----------

def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode()


# ---------- CLI (dev) ----------

async def _demo() -> None:  # pragma: no cover
    print("[ai_worker] demo starting…")
    w = AiWorker.create_from_env()
    await w.start()
    prompts = [b"hello world", b"animica useful-work", b"poies acceptance"]
    tickets: list[AITicket] = []
    for p in prompts:
        tkt = await w.enqueue(model="tiny-devnet", prompt=p, max_tokens=32)
        print(" enqueued:", tkt.task_id)
        tickets.append(tkt)
    # wait for completions
    t_end = time.time() + 3.0
    ready: list[ResultRecord] = []
    while time.time() < t_end and len(ready) < len(prompts):
        ready.extend(w.pop_ready(10))
        await asyncio.sleep(0.1)
    for r in ready:
        print(" completed:", r.task_id, "digest=", r.output_digest.hex()[:16], "units=", r.metrics.get("ai_units"))
    await w.stop()
    print("[ai_worker] demo done.")

if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
