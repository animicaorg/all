from __future__ import annotations

"""
Animica mining.storage_worker
============================

Purpose
-------
Async helper used by the miner to:
  1) enqueue *storage* PoSt heartbeat jobs (prove bytes under custody),
  2) optionally attach *retrieval tickets* (prove random retrievals),
  3) poll for completion,
  4) yield normalized "ResultRecord" dicts for proofs/storage to wrap into
     StorageProof envelopes.

ResultRecord (normalized) shape:
    {
      "kind": "STORAGE",
      "task_id": "stor-…",
      "provider_id": "provider-…",
      "output_digest": b"\x00…32",     # sha3_256 of canonical result bytes
      "attestation": {...},            # provider evidence or local transcript (dev)
      "metrics": {
          "storage_bytes": int,        # sectors * bytes_per_sector
          "sectors": int,
          "redundancy": float,         # replicas factor or erasure overhead
          "qos": float,                # availability/latency SLO
          "tickets_total": int,
          "tickets_ok": int,
          "latency_ms": int,
      },
      "completed_at": float,
    }

Backends
--------
- HttpStorageBackend: minimal HTTP client to a storage-verifier service.
- DevLocalBackend: deterministic local heartbeat generator (devnet).

Usage
-----
    w = StorageWorker.create_from_env()
    await w.start()
    tkt = await w.enqueue(dataset_id="devset", sectors=256, bytes_per_sector=1<<20,
                          ticket_count=8, redundancy=1.5, seed=b"\x00"*32)
    # later:
    for rec in w.pop_ready():
        ...
    await w.stop()

Optional auto-heartbeat loop (env):
    STORAGE_AUTO_DATASET   : dataset id to auto-heartbeat
    STORAGE_AUTO_SECTORS   : int sectors (default 256)
    STORAGE_AUTO_BPS       : bytes per sector (default 1<<20)
    STORAGE_AUTO_TICKETS   : retrieval tickets per heartbeat (default 0)
    STORAGE_HEARTBEAT_SEC  : cadence (default 30s)

Service (if used):
    STORAGE_URL            : base URL; if unset, use DevLocalBackend
    STORAGE_API_KEY        : optional bearer token

Other env:
    STORAGE_WORKER_POLL_MS : poll interval (default 500ms)
    STORAGE_WORKER_QUEUE   : ready-queue capacity (default 128)
    STORAGE_SIM_LAT_MS     : simulated latency for DevLocal (default 450ms)

Dependencies
------------
stdlib only. Will use proofs.utils.hash.sha3_256 if available; falls back to hashlib.

"""

import asyncio
import contextlib
import json
import math
import os
import time
import typing as t
from dataclasses import dataclass, field

# ---- hashing (prefer shared) ----
try:
    from proofs.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib
    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()

# ---------- Types ----------

@dataclass(frozen=True)
class StorageJobSpec:
    dataset_id: str
    sectors: int
    bytes_per_sector: int
    ticket_count: int = 0
    redundancy: float = 1.0
    seed: bytes = b""           # deterministic sampling seed (tickets/layout)
    qos_hint: t.Optional[str] = None
    window_id: t.Optional[int] = None  # optional explicit window index (e.g., floor(now/period))

@dataclass
class Ticket:
    task_id: str
    submitted_at: float
    status: str = "queued"      # queued|running|completed|failed|unknown
    provider_id: t.Optional[str] = None
    error: t.Optional[str] = None

@dataclass
class ResultRecord:
    kind: str                   # "STORAGE"
    task_id: str
    provider_id: t.Optional[str]
    output_digest: bytes
    attestation: t.Dict[str, t.Any]
    metrics: t.Dict[str, t.Any]
    completed_at: float


# ---------- Backend protocol ----------

class StorageBackend(t.Protocol):
    async def enqueue(self, spec: StorageJobSpec) -> Ticket: ...
    async def status(self, task_id: str) -> Ticket: ...
    async def fetch_result(self, task_id: str) -> ResultRecord: ...


# ---------- Dev local backend ----------

class DevLocalBackend:
    """
    Deterministic PoSt heartbeat generator with optional retrieval-ticket sampling.

    - task_id = H("animica.task.storage" || dataset_id || window_id || sectors || bps || tickets || redundancy || seed)[:32]
    - tickets_ok sampled via hash-stream Bernoulli with success ~ 0.985 +/- small seed jitter
    - qos ~= 0.99 for normal simulated latency; slightly lower if STORAGE_SIM_LAT_MS > 750
    - output_digest = H("storage-output" || canonical_result_bytes)
    """
    def __init__(self) -> None:
        self._lat_ms = int(os.getenv("STORAGE_SIM_LAT_MS", "450"))
        self._store: dict[str, tuple[StorageJobSpec, float, float]] = {}

    async def enqueue(self, spec: StorageJobSpec) -> Ticket:
        self._validate(spec)
        now = time.time()
        task_id = self._derive_task_id(spec)
        if task_id not in self._store:
            done = now + (self._lat_ms / 1000.0)
            self._store[task_id] = (spec, now, done)
        return Ticket(task_id=task_id, submitted_at=now, status="queued", provider_id="devsim-storage")

    async def status(self, task_id: str) -> Ticket:
        rec = self._store.get(task_id)
        if not rec:
            return Ticket(task_id=task_id, submitted_at=time.time(), status="unknown", provider_id="devsim-storage")
        _, sub, done = rec
        st = "completed" if time.time() >= done else "running"
        return Ticket(task_id=task_id, submitted_at=sub, status=st, provider_id="devsim-storage")

    async def fetch_result(self, task_id: str) -> ResultRecord:
        rec = self._store.get(task_id)
        if not rec:
            raise RuntimeError("task not found")
        spec, sub, done = rec
        if time.time() < done:
            await asyncio.sleep(max(0.0, done - time.time()))

        tickets_ok = self._simulate_tickets(spec)
        qos = 0.99 if self._lat_ms <= 750 else 0.97
        storage_bytes = spec.sectors * spec.bytes_per_sector
        metrics = {
            "storage_bytes": int(storage_bytes),
            "sectors": int(spec.sectors),
            "redundancy": float(spec.redundancy),
            "qos": float(qos),
            "tickets_total": int(spec.ticket_count),
            "tickets_ok": int(tickets_ok),
            "latency_ms": int((time.time() - sub) * 1000),
        }
        att = {
            "provider": "devsim-storage",
            "dataset_id": spec.dataset_id,
            "window_id": self._window_id(spec),
            "seed_hex": (spec.seed or self._seed_from_spec(spec)).hex(),
            "layout_hash_hex": sha3_256(self._layout_bytes(spec)).hex(),
            "tickets": {"ok": tickets_ok, "total": spec.ticket_count},
        }
        out_digest = sha3_256(b"storage-output\0" + self._canonical_result_bytes(spec, metrics, att))
        return ResultRecord(
            kind="STORAGE",
            task_id=task_id,
            provider_id="devsim-storage",
            output_digest=out_digest,
            attestation=att,
            metrics=metrics,
            completed_at=time.time(),
        )

    # ---- helpers ----

    def _validate(self, spec: StorageJobSpec) -> None:
        if not spec.dataset_id or len(spec.dataset_id) > 128:
            raise ValueError("bad dataset_id")
        if not (1 <= spec.sectors <= 10_000_000):
            raise ValueError("sectors out of bounds")
        if not (1 << 10) <= spec.bytes_per_sector <= (1 << 30):
            raise ValueError("bytes_per_sector out of bounds")
        if not (0 <= spec.ticket_count <= 10_000):
            raise ValueError("ticket_count out of bounds")
        if not (0.5 <= spec.redundancy <= 10.0):
            raise ValueError("redundancy out of bounds")

    def _window_id(self, spec: StorageJobSpec) -> int:
        if spec.window_id is not None:
            return int(spec.window_id)
        period = max(1, int(os.getenv("STORAGE_HEARTBEAT_SEC", "30")))
        return int(time.time() // period)

    def _derive_task_id(self, spec: StorageJobSpec) -> str:
        w = self._window_id(spec)
        h = sha3_256(
            b"animica.task.storage\0" +
            spec.dataset_id.encode() + b"\0" +
            w.to_bytes(8, "big") +
            spec.sectors.to_bytes(8, "big") +
            spec.bytes_per_sector.to_bytes(8, "big") +
            spec.ticket_count.to_bytes(4, "big") +
            struct_pack_f64(spec.redundancy) +
            (spec.seed or self._seed_from_spec(spec))
        )
        return "stor-" + h.hex()[:32]

    def _seed_from_spec(self, spec: StorageJobSpec) -> bytes:
        return sha3_256(
            b"stor-seed\0" + spec.dataset_id.encode() +
            spec.sectors.to_bytes(8,"big") + spec.bytes_per_sector.to_bytes(8,"big")
        )

    def _layout_bytes(self, spec: StorageJobSpec) -> bytes:
        return (
            spec.dataset_id.encode() + b"\0" +
            spec.sectors.to_bytes(8,"big") + spec.bytes_per_sector.to_bytes(8,"big") +
            (spec.seed or self._seed_from_spec(spec))
        )

    def _simulate_tickets(self, spec: StorageJobSpec) -> int:
        n = int(spec.ticket_count)
        if n <= 0:
            return 0
        seed = spec.seed or self._seed_from_spec(spec)
        # success probability around 0.985 with tiny jitter by seed & redundancy
        base = 0.985 * min(1.0, spec.redundancy / 1.0)
        jitter = int.from_bytes(sha3_256(b"stor-jit"+seed)[:1], "big") / 255.0
        p_ok = max(0.95, min(0.999, base + 0.01*(jitter - 0.5)))
        ok = 0
        stream = b""
        idx = 0
        while idx < n:
            if len(stream) < 32:
                stream += sha3_256(seed + idx.to_bytes(8,"big"))
            b0, stream = stream[0], stream[1:]
            if b0 <= int(p_ok*255):
                ok += 1
            idx += 1
        return min(ok, n)

    def _canonical_result_bytes(self, spec: StorageJobSpec, metrics: dict, att: dict) -> bytes:
        # Compact canonical bytes (without JSON encoding ambiguities)
        def enc_str(s: str) -> bytes:
            b = s.encode()
            return len(b).to_bytes(2, "big") + b
        return (
            enc_str(spec.dataset_id) +
            self._window_id(spec).to_bytes(8,"big") +
            spec.sectors.to_bytes(8,"big") +
            spec.bytes_per_sector.to_bytes(8,"big") +
            int(metrics["tickets_total"]).to_bytes(4,"big") +
            int(metrics["tickets_ok"]).to_bytes(4,"big") +
            struct_pack_f64(float(metrics["redundancy"])) +
            struct_pack_f64(float(metrics["qos"])) +
            bytes.fromhex(att["layout_hash_hex"])
        )


# ---------- HTTP backend ----------

class HttpStorageBackend:
    """
    Minimal REST interface:

    POST {base}/storage/heartbeat
        { dataset_id, sectors, bytes_per_sector, ticket_count, redundancy, seed_b64, qos }
        -> { task_id }

    GET {base}/storage/{task_id}
        -> { status, provider_id? }

    GET {base}/storage/{task_id}/result
        -> {
             provider_id,
             output_digest_hex,
             attestation: {...},
             metrics: {...},
             completed_at
           }
    """
    def __init__(self, base_url: str, api_key: str | None = None, timeout_s: float = 15.0) -> None:
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout_s

    def _headers(self) -> dict[str,str]:
        h = {"content-type": "application/json"}
        if self.key:
            h["authorization"] = f"Bearer {self.key}"
        return h

    async def enqueue(self, spec: StorageJobSpec) -> Ticket:
        body = {
            "dataset_id": spec.dataset_id,
            "sectors": spec.sectors,
            "bytes_per_sector": spec.bytes_per_sector,
            "ticket_count": spec.ticket_count,
            "redundancy": spec.redundancy,
            "seed_b64": _b64(spec.seed),
            "qos": spec.qos_hint,
            "window_id": spec.window_id,
        }
        data = await _http_json("POST", f"{self.base}/storage/heartbeat",
                                headers=self._headers(), json_body=body, timeout=self.timeout)
        tid = t.cast(str, data.get("task_id",""))
        if not tid:
            raise RuntimeError("storage enqueue: missing task_id")
        return Ticket(task_id=tid, submitted_at=time.time(), status="queued")

    async def status(self, task_id: str) -> Ticket:
        data = await _http_json("GET", f"{self.base}/storage/{task_id}",
                                headers=self._headers(), timeout=self.timeout)
        st = t.cast(str, data.get("status","unknown"))
        prov = t.cast(t.Optional[str], data.get("provider_id"))
        return Ticket(task_id=task_id, submitted_at=time.time(), status=st, provider_id=prov)

    async def fetch_result(self, task_id: str) -> ResultRecord:
        data = await _http_json("GET", f"{self.base}/storage/{task_id}/result",
                                headers=self._headers(), timeout=self.timeout)
        out_hex = t.cast(str, data.get("output_digest_hex",""))
        if len(out_hex) != 64:
            raise RuntimeError("storage result: bad output_digest_hex")
        return ResultRecord(
            kind="STORAGE",
            task_id=task_id,
            provider_id=t.cast(t.Optional[str], data.get("provider_id")),
            output_digest=bytes.fromhex(out_hex),
            attestation=t.cast(dict, data.get("attestation", {})),
            metrics=t.cast(dict, data.get("metrics", {})),
            completed_at=float(data.get("completed_at", time.time())),
        )


# ---------- Worker orchestrator ----------

@dataclass
class _Pending:
    spec: StorageJobSpec
    ticket: Ticket
    last_polled: float = field(default_factory=lambda: 0.0)

class StorageWorker:
    """
    - enqueue(spec) -> Ticket
    - background poller updates tickets and retrieves results
    - optional auto-heartbeat loop (env)
    - pop_ready(max_n) -> completed ResultRecord items
    """
    def __init__(self, backend: StorageBackend, poll_interval_s: float = 0.5, queue_limit: int = 128) -> None:
        self._backend = backend
        self._poll_interval = float(poll_interval_s)
        self._queue_limit = int(queue_limit)

        self._pending: dict[str, _Pending] = {}
        self._ready: asyncio.Queue[ResultRecord] = asyncio.Queue(maxsize=queue_limit)
        self._poll_task: asyncio.Task | None = None
        self._auto_task: asyncio.Task | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    @classmethod
    def create_from_env(cls) -> "StorageWorker":
        base = os.getenv("STORAGE_URL")
        key  = os.getenv("STORAGE_API_KEY")
        poll_ms = int(os.getenv("STORAGE_WORKER_POLL_MS", "500"))
        cap = int(os.getenv("STORAGE_WORKER_QUEUE", "128"))
        backend: StorageBackend = HttpStorageBackend(base, key) if base else DevLocalBackend()
        return cls(backend=backend, poll_interval_s=poll_ms / 1000.0, queue_limit=cap)

    async def start(self) -> None:
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._run_poll(), name="StorageWorker.poller")
        # optional auto-heartbeat
        auto_ds = os.getenv("STORAGE_AUTO_DATASET")
        if auto_ds and self._auto_task is None:
            self._auto_task = asyncio.create_task(self._run_auto(auto_ds), name="StorageWorker.auto")

    async def stop(self) -> None:
        self._closed = True
        for task in (self._auto_task, self._poll_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._pending.clear()

    async def enqueue(
        self, *,
        dataset_id: str,
        sectors: int,
        bytes_per_sector: int,
        ticket_count: int = 0,
        redundancy: float = 1.0,
        seed: bytes = b"",
        qos_hint: str | None = None,
        window_id: int | None = None,
    ) -> Ticket:
        spec = StorageJobSpec(dataset_id=dataset_id, sectors=int(sectors),
                              bytes_per_sector=int(bytes_per_sector),
                              ticket_count=int(ticket_count), redundancy=float(redundancy),
                              seed=bytes(seed), qos_hint=qos_hint, window_id=window_id)
        async with self._lock:
            if len(self._pending) >= self._queue_limit:
                raise RuntimeError("storage_worker queue full")
            tkt = await self._backend.enqueue(spec)
            self._pending[tkt.task_id] = _Pending(spec=spec, ticket=tkt)
            return tkt

    def pop_ready(self, max_n: int = 32) -> list[ResultRecord]:
        out: list[ResultRecord] = []
        for _ in range(max_n):
            try:
                out.append(self._ready.get_nowait())
                self._ready.task_done()
            except asyncio.QueueEmpty:
                break
        return out

    # ---- polling loop ----

    async def _run_poll(self) -> None:
        try:
            while not self._closed:
                await self._poll_once()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            return

    async def _poll_once(self) -> None:
        async with self._lock:
            ids = list(self._pending.keys())
        for tid in ids:
            pn = self._pending.get(tid)
            if pn is None:
                continue
            now = time.time()
            if now - pn.last_polled < self._poll_interval * 0.8:
                continue
            pn.last_polled = now
            try:
                st = await self._backend.status(tid)
                pn.ticket = st
            except Exception as e:
                pn.ticket.status = "unknown"
                pn.ticket.error = str(e)
                continue

            if st.status == "completed":
                try:
                    rec = await self._backend.fetch_result(tid)
                    await self._offer_ready(rec)
                    async with self._lock:
                        self._pending.pop(tid, None)
                except Exception as e:
                    pn.ticket.status = "failed"
                    pn.ticket.error = f"fetch_result: {e!r}"
            elif st.status in ("failed", "unknown"):
                async with self._lock:
                    self._pending.pop(tid, None)

    async def _offer_ready(self, rec: ResultRecord) -> None:
        try:
            self._ready.put_nowait(rec)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = self._ready.get_nowait()
                self._ready.task_done()
            self._ready.put_nowait(rec)

    # ---- auto-heartbeat loop (optional) ----

    async def _run_auto(self, dataset_id: str) -> None:
        period = max(5, int(os.getenv("STORAGE_HEARTBEAT_SEC", "30")))
        sectors = int(os.getenv("STORAGE_AUTO_SECTORS", "256"))
        bps = int(os.getenv("STORAGE_AUTO_BPS", str(1<<20)))
        tickets = int(os.getenv("STORAGE_AUTO_TICKETS", "0"))
        redundancy = float(os.getenv("STORAGE_AUTO_REDUNDANCY", "1.0"))

        try:
            while not self._closed:
                win = int(time.time() // period)
                seed = sha3_256(b"auto\0"+dataset_id.encode()+win.to_bytes(8,"big"))
                try:
                    await self.enqueue(dataset_id=dataset_id, sectors=sectors, bytes_per_sector=bps,
                                       ticket_count=tickets, redundancy=redundancy, seed=seed, window_id=win)
                except Exception:
                    # swallow; next cycle will try again
                    pass
                await asyncio.sleep(period)
        except asyncio.CancelledError:
            return


# ---------- helpers ----------

def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode()

def struct_pack_f64(x: float) -> bytes:
    import struct
    return struct.pack("!d", float(x))


# ---------- tiny HTTP helper (stdlib) ----------

async def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, t.Any] | None = None,
    timeout: float = 15.0,
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


# ---------- CLI demo ----------

async def _demo() -> None:  # pragma: no cover
    print("[storage_worker] demo starting…")
    w = StorageWorker.create_from_env()
    await w.start()
    # one-shot enqueue
    tkt = await w.enqueue(dataset_id=os.getenv("STORAGE_AUTO_DATASET","devset"),
                          sectors=256, bytes_per_sector=1<<20,
                          ticket_count=8, redundancy=1.5,
                          seed=sha3_256(b"demo-seed"))
    print(" enqueued:", tkt.task_id)
    # wait for completion
    t_end = time.time() + 3.0
    got: list[ResultRecord] = []
    while time.time() < t_end and not got:
        got.extend(w.pop_ready())
        await asyncio.sleep(0.1)
    for r in got:
        print(" completed:", r.task_id, "digest=", r.output_digest.hex()[:16],
              "tickets=", f"{r.metrics['tickets_ok']}/{r.metrics['tickets_total']}",
              "bytes=", r.metrics["storage_bytes"])
    await w.stop()
    print("[storage_worker] demo done.")

if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
