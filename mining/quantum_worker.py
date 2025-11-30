from __future__ import annotations

"""
Animica mining.quantum_worker
============================

Purpose
-------
Async helper used by the miner to:
  1) enqueue *quantum* trap-circuit jobs (depth/width/shots + traps) to AICF
     or to a local deterministic simulator backend,
  2) poll for completion,
  3) yield normalized "ResultRecord" dicts ready for the proofs/adapters layer
     to assemble proper QuantumProof envelopes.

Returned record shape:
    {
      "kind": "QUANTUM",
      "task_id": "...",
      "provider_id": "prov-…",
      "output_digest": b"\x00…32",      # sha3_256 of canonical output bytes
      "attestation": {...},             # provider evidence (simulator includes params hash)
      "metrics": {
          "quantum_units": int,
          "traps_ratio": float,
          "conf_low": float,
          "conf_high": float,
          "qos": float,
          "latency_ms": int,
          "width": int, "depth": int, "shots": int,
      },
      "completed_at": float,
    }

Backends
--------
- HttpAICFBackend: minimal HTTP client to an AICF-compatible service.
- DevSimBackend: deterministic trap-circuit simulator for devnet.

Usage
-----
    worker = QuantumWorker.create_from_env()
    await worker.start()
    tkt = await worker.enqueue(width=8, depth=12, shots=256, trap_fraction=0.1,
                               circuit_json=b'{"name":"ghz"}', trap_seed=b'\x01'*32)
    # later:
    for rec in worker.pop_ready():
        pass
    await worker.stop()

Env (optional)
--------------
AICF_URL              : base URL for an AICF service. If unset, DevSimBackend is used.
AICF_API_KEY          : optional bearer token.
QPU_WORKER_POLL_MS    : poll interval (default 500).
QPU_WORKER_QUEUE      : ready-queue capacity (default 128).
QPU_SIM_LAT_MS        : simulator latency (default 600).

Dependencies
------------
stdlib only; uses proofs.quantum_attest.{traps,benchmarks} if present for nicer math,
with safe fallbacks otherwise.

"""

import asyncio
import contextlib
import json
import math
import os
import time
import typing as t
from dataclasses import dataclass, field

# --- hashing (prefer shared helper) ---
try:
    from proofs.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()


# --- try to import trap math & units; provide fallbacks if missing ---
try:
    from proofs.quantum_attest.traps import wilson_interval  # type: ignore
except Exception:

    def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
        """Wilson score interval (fallback)."""
        if n <= 0:
            return (0.0, 0.0)
        p_hat = k / n
        denom = 1 + z * z / n
        centre = p_hat + z * z / (2 * n)
        rad = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * n)) / n)
        lo = max(0.0, (centre - rad) / denom)
        hi = min(1.0, (centre + rad) / denom)
        return (lo, hi)


try:
    from proofs.quantum_attest.benchmarks import units_for  # type: ignore
except Exception:

    def units_for(width: int, depth: int, shots: int) -> int:
        # Rough fallback scaling; tuned to devnet demos.
        return max(1, (width * max(1, depth) * max(1, shots)) // 128)


# ---------- Types ----------


@dataclass(frozen=True)
class QuantumJobSpec:
    width: int
    depth: int
    shots: int
    trap_fraction: float  # e.g., 0.05 .. 0.25
    circuit_json: bytes  # canonical JSON bytes
    trap_seed: bytes = b""  # deterministic simulation & trap layout salt
    qos_hint: t.Optional[str] = None


@dataclass
class QPUTicket:
    task_id: str
    submitted_at: float
    status: str = "queued"  # queued|running|completed|failed|unknown
    provider_id: t.Optional[str] = None
    error: t.Optional[str] = None


@dataclass
class ResultRecord:
    kind: str  # "QUANTUM"
    task_id: str
    provider_id: t.Optional[str]
    output_digest: bytes
    attestation: t.Dict[str, t.Any]
    metrics: t.Dict[str, t.Any]
    completed_at: float


# ---------- Backend protocol ----------


class AICFBackend(t.Protocol):
    async def enqueue(self, spec: QuantumJobSpec) -> QPUTicket: ...
    async def status(self, task_id: str) -> QPUTicket: ...
    async def fetch_result(self, task_id: str) -> ResultRecord: ...


# ---------- Dev simulator backend ----------


class DevSimBackend:
    """
    Deterministic QPU trap-circuit simulator:
      - Derives task_id from spec hash.
      - Uses trap_seed to generate Bernoulli outcomes for trap shots.
      - Computes traps_ratio = traps_pass / traps_total and Wilson CI.
      - output_digest = sha3_256("qpu-output"||circuit_json||seed||width||depth||shots).
      - quantum_units from (width,depth,shots).

    Latency controlled via QPU_SIM_LAT_MS (default 600ms).
    """

    def __init__(self) -> None:
        self._lat_ms = int(os.getenv("QPU_SIM_LAT_MS", "600"))
        self._store: dict[str, tuple[QuantumJobSpec, float, float]] = {}

    async def enqueue(self, spec: QuantumJobSpec) -> QPUTicket:
        now = time.time()
        self._validate(spec)
        task_id = self._derive_task_id(spec)
        if task_id not in self._store:
            done_at = now + (self._lat_ms / 1000.0)
            self._store[task_id] = (spec, now, done_at)
        return QPUTicket(
            task_id=task_id, submitted_at=now, status="queued", provider_id="devsim-qpu"
        )

    async def status(self, task_id: str) -> QPUTicket:
        rec = self._store.get(task_id)
        if not rec:
            return QPUTicket(
                task_id=task_id,
                submitted_at=time.time(),
                status="unknown",
                provider_id="devsim-qpu",
            )
        _, sub, done_at = rec
        st = "completed" if time.time() >= done_at else "running"
        return QPUTicket(
            task_id=task_id, submitted_at=sub, status=st, provider_id="devsim-qpu"
        )

    async def fetch_result(self, task_id: str) -> ResultRecord:
        rec = self._store.get(task_id)
        if not rec:
            raise RuntimeError("task not found")
        spec, sub, done_at = rec
        if time.time() < done_at:
            await asyncio.sleep(max(0.0, done_at - time.time()))
        # simulate traps
        traps_total = max(1, int(spec.shots * max(0.0, min(1.0, spec.trap_fraction))))
        traps_pass = self._simulate_traps(
            seed=spec.trap_seed or self._seed_from_spec(spec), n=traps_total
        )
        lo, hi = wilson_interval(traps_pass, traps_total)
        t_ratio = traps_pass / traps_total
        q_units = units_for(spec.width, spec.depth, spec.shots)
        out_digest = self._output_digest(spec)
        metrics = {
            "quantum_units": int(q_units),
            "traps_ratio": float(t_ratio),
            "conf_low": float(lo),
            "conf_high": float(hi),
            "qos": 0.99 if self._lat_ms <= 800 else 0.95,
            "latency_ms": int((time.time() - sub) * 1000),
            "width": spec.width,
            "depth": spec.depth,
            "shots": spec.shots,
        }
        att = {
            "provider": "devsim-qpu",
            "note": "deterministic simulated quantum attestation (dev-only)",
            "trap_seed_hex": (spec.trap_seed or self._seed_from_spec(spec)).hex(),
            "circuit_hash_hex": sha3_256(spec.circuit_json).hex(),
            "traps": {"passed": traps_pass, "total": traps_total},
        }
        return ResultRecord(
            kind="QUANTUM",
            task_id=task_id,
            provider_id="devsim-qpu",
            output_digest=out_digest,
            attestation=att,
            metrics=metrics,
            completed_at=time.time(),
        )

    # ---- helpers ----

    def _derive_task_id(self, spec: QuantumJobSpec) -> str:
        h = sha3_256(
            b"animica.task.qpu"
            + b"\x00"
            + spec.circuit_json
            + b"\x00"
            + spec.trap_seed
            + b"\x00"
            + spec.width.to_bytes(4, "big")
            + spec.depth.to_bytes(4, "big")
            + spec.shots.to_bytes(4, "big")
            + struct_pack_f64(spec.trap_fraction)
        )
        return "qpu-" + h.hex()[:32]

    def _output_digest(self, spec: QuantumJobSpec) -> bytes:
        return sha3_256(
            b"qpu-output"
            + b"\x00"
            + spec.circuit_json
            + b"\x00"
            + spec.trap_seed
            + spec.width.to_bytes(4, "big")
            + spec.depth.to_bytes(4, "big")
            + spec.shots.to_bytes(4, "big")
        )

    def _seed_from_spec(self, spec: QuantumJobSpec) -> bytes:
        return sha3_256(
            b"trap-seed"
            + spec.circuit_json
            + spec.width.to_bytes(4, "big")
            + spec.depth.to_bytes(4, "big")
            + spec.shots.to_bytes(4, "big")
        )

    def _simulate_traps(self, *, seed: bytes, n: int) -> int:
        """Deterministic pseudo-random trap pass/fail using seed → stream."""
        # Choose a target pass prob around 0.985 for devnet, but vary with seed for entropy.
        bias = int.from_bytes(sha3_256(b"bias" + seed)[:2], "big") / 65535.0  # 0..1
        p_pass = 0.97 + 0.02 * (bias - 0.5)  # ~[0.96, 0.98]
        # Produce Bernoulli(n, p_pass) via a hash stream.
        passed = 0
        counter = 0
        buf = b""
        while passed < n and counter < n:
            if len(buf) < 32:
                buf += sha3_256(seed + counter.to_bytes(8, "big"))
            byte, buf = buf[0], buf[1:]
            thresh = int(p_pass * 255.0)
            if byte <= thresh:
                passed += 1
            counter += 1
        # Clamp in case of loop fall-through differences
        return min(passed, n)

    def _validate(self, spec: QuantumJobSpec) -> None:
        if not (
            1 <= spec.width <= 128
            and 1 <= spec.depth <= 10_000
            and 1 <= spec.shots <= 1_000_000
        ):
            raise ValueError("spec out of bounds")
        if not (0.0 <= spec.trap_fraction <= 0.5):
            raise ValueError("trap_fraction must be within [0, 0.5]")
        if len(spec.circuit_json) > 1_000_000:
            raise ValueError("circuit too large")


# ---------- HTTP backend (AICF-like) ----------


class HttpAICFBackend:
    """
    Minimal JSON API client.

    POST {base}/jobs/quantum
        { width, depth, shots, trap_fraction, circuit_json_b64, trap_seed_b64, qos }
        -> { task_id }

    GET {base}/jobs/{task_id}
        -> { status, provider_id? }

    GET {base}/jobs/{task_id}/result
        -> {
              provider_id,
              output_digest_hex,
              attestation: {...},
              metrics: {
                 quantum_units, traps_ratio, conf_low, conf_high, qos, latency_ms,
                 width, depth, shots
              },
              completed_at
           }
    """

    def __init__(
        self, base_url: str, api_key: str | None = None, timeout_s: float = 15.0
    ) -> None:
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout_s

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.key:
            h["authorization"] = f"Bearer {self.key}"
        return h

    async def enqueue(self, spec: QuantumJobSpec) -> QPUTicket:
        body = {
            "width": spec.width,
            "depth": spec.depth,
            "shots": spec.shots,
            "trap_fraction": spec.trap_fraction,
            "circuit_json_b64": _b64(spec.circuit_json),
            "trap_seed_b64": _b64(spec.trap_seed),
            "qos": spec.qos_hint,
        }
        data = await _http_json(
            "POST",
            f"{self.base}/jobs/quantum",
            headers=self._headers(),
            json_body=body,
            timeout=self.timeout,
        )
        task_id = t.cast(str, data.get("task_id", ""))
        if not task_id:
            raise RuntimeError("AICF enqueue: missing task_id")
        return QPUTicket(task_id=task_id, submitted_at=time.time(), status="queued")

    async def status(self, task_id: str) -> QPUTicket:
        data = await _http_json(
            "GET",
            f"{self.base}/jobs/{task_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        st = t.cast(str, data.get("status", "unknown"))
        prov = t.cast(t.Optional[str], data.get("provider_id"))
        return QPUTicket(
            task_id=task_id, submitted_at=time.time(), status=st, provider_id=prov
        )

    async def fetch_result(self, task_id: str) -> ResultRecord:
        data = await _http_json(
            "GET",
            f"{self.base}/jobs/{task_id}/result",
            headers=self._headers(),
            timeout=self.timeout,
        )
        out_hex = t.cast(str, data.get("output_digest_hex", ""))
        if len(out_hex) != 64:
            raise RuntimeError("AICF result: bad output_digest_hex")
        return ResultRecord(
            kind="QUANTUM",
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
    spec: QuantumJobSpec
    ticket: QPUTicket
    last_polled: float = field(default_factory=lambda: 0.0)


class QuantumWorker:
    """
    - enqueue(spec) -> ticket
    - background poller updates tickets and retrieves results
    - pop_ready(max_n) -> completed ResultRecord items
    """

    def __init__(
        self, backend: AICFBackend, poll_interval_s: float = 0.5, queue_limit: int = 128
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
    def create_from_env(cls) -> "QuantumWorker":
        base = os.getenv("AICF_URL")
        key = os.getenv("AICF_API_KEY")
        poll_ms = int(os.getenv("QPU_WORKER_POLL_MS", "500"))
        cap = int(os.getenv("QPU_WORKER_QUEUE", "128"))
        if base:
            backend: AICFBackend = HttpAICFBackend(base_url=base, api_key=key)
        else:
            backend = DevSimBackend()
        return cls(backend=backend, poll_interval_s=poll_ms / 1000.0, queue_limit=cap)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="QuantumWorker.poller")

    async def stop(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._pending.clear()

    async def enqueue(
        self,
        *,
        width: int,
        depth: int,
        shots: int,
        trap_fraction: float,
        circuit_json: bytes,
        trap_seed: bytes = b"",
        qos_hint: str | None = None,
    ) -> QPUTicket:
        spec = QuantumJobSpec(
            width=width,
            depth=depth,
            shots=shots,
            trap_fraction=float(trap_fraction),
            circuit_json=bytes(circuit_json),
            trap_seed=bytes(trap_seed),
            qos_hint=qos_hint,
        )
        async with self._lock:
            if len(self._pending) >= self._queue_limit:
                raise RuntimeError("quantum_worker queue full")
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

    # ---- internals ----

    async def _run(self) -> None:
        try:
            while not self._closed:
                await self._poll_once()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            return

    async def _poll_once(self) -> None:
        async with self._lock:
            ids = list(self._pending.keys())
        for task_id in ids:
            pn = self._pending.get(task_id)
            if pn is None:
                continue
            now = time.time()
            if now - pn.last_polled < self._poll_interval * 0.8:
                continue
            pn.last_polled = now
            try:
                st = await self._backend.status(task_id)
                pn.ticket = st
            except Exception as e:
                pn.ticket.status = "unknown"
                pn.ticket.error = str(e)
                continue

            if st.status == "completed":
                try:
                    rec = await self._backend.fetch_result(task_id)
                    await self._offer_ready(rec)
                    async with self._lock:
                        self._pending.pop(task_id, None)
                except Exception as e:
                    pn.ticket.status = "failed"
                    pn.ticket.error = f"fetch_result: {e!r}"
            elif st.status in ("failed", "unknown"):
                async with self._lock:
                    self._pending.pop(task_id, None)

    async def _offer_ready(self, rec: ResultRecord) -> None:
        try:
            self._ready.put_nowait(rec)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = self._ready.get_nowait()
                self._ready.task_done()
            self._ready.put_nowait(rec)


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
    import urllib.error
    import urllib.request

    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
    req = urllib.request.Request(
        url=url, method=method.upper(), data=data, headers=headers or {}
    )
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
            raise RuntimeError(
                f"http {e.code}: {e.read().decode(errors='ignore')[:256]}"
            ) from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"http error: {e.reason}") from None

    return await loop.run_in_executor(None, _do)


# ---------- CLI (dev) ----------


async def _demo() -> None:  # pragma: no cover
    print("[quantum_worker] demo starting…")
    w = QuantumWorker.create_from_env()
    await w.start()
    circuits = [b'{"name":"bell"}', b'{"name":"qft","n":5}', b'{"name":"random"}']
    for i, c in enumerate(circuits):
        tkt = await w.enqueue(
            width=5 + i,
            depth=10 + 2 * i,
            shots=256,
            trap_fraction=0.1,
            circuit_json=c,
            trap_seed=sha3_256(b"seed" + bytes([i])),
        )
        print(" enqueued:", tkt.task_id)
    t_end = time.time() + 4.0
    got: list[ResultRecord] = []
    while time.time() < t_end and len(got) < len(circuits):
        got.extend(w.pop_ready())
        await asyncio.sleep(0.1)
    for r in got:
        print(
            " completed:",
            r.task_id,
            "digest=",
            r.output_digest.hex()[:16],
            "traps=",
            f"{r.metrics['traps_ratio']:.4f}",
            "units=",
            r.metrics["quantum_units"],
        )
    await w.stop()
    print("[quantum_worker] demo done.")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
