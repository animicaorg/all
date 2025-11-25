from __future__ import annotations

"""
Animica mining.vdf_worker
=========================
Asynchronous helper that computes a Wesolowski Proof-of-Exponentiation (VDF)
for optional “useful bonus” credit. It runs a *reference* prover and returns
a proof body ready to be wrapped into a `VDFProof` envelope (see
`proofs/schemas/vdf.cddl`) along with simple metrics (e.g., wall-clock seconds).

This worker is intentionally minimal and CPU-only; it offloads heavy math to
`randomness.vdf.wesolowski` if present. If that module is not available yet,
the worker will raise a clear error prompting you to enable the randomness
package. This keeps the miner side slim and lets you swap implementations.

Typical usage
-------------
    worker = VDFWorker.create_from_env()
    await worker.start()

    job = VDFJobSpec(              # usually derived from the header template
        modulus_n_hex=N_HEX,       # RSA modulus (hex), per-network parameter
        base_g_hex=G_HEX,          # base element (hex), derived from header/beacon
        iterations=200_000,        # number of squarings (t)
        label=b"animica.vdf.block\0" + mix_seed_bytes,
    )
    tid = await worker.enqueue(job)

    # later, fetch completed items
    for result in worker.pop_ready():
        # result.proof_body is compatible with proofs/schemas/vdf.cddl
        # result.metrics contains 'vdf_seconds' and 'iterations'
        ...

Environment
-----------
VDF defaults can be supplied via env vars (handy for dev/test):

  VDF_MODULUS_HEX      : 2048-bit RSA modulus (hex, no 0x)
  VDF_BASE_FROM        : "mix"|"beacon" (hint for callers; not used here)
  VDF_ITERATIONS       : default t if job.iterations is not set
  VDF_LABEL_PREFIX     : optional ASCII prefix mixed into label
  VDF_POLL_MS          : poll interval for the internal queue (default 250)
  VDF_QUEUE_LIMIT      : ready-queue capacity (default 64)

Notes
-----
- Security: use a *trusted* RSA modulus for production networks (per spec).
  This worker intentionally does not bake any modulus; pass it in the job/env.
- Performance: the reference prover is CPU-bound. For large t, prefer a native
  implementation (C/Rust) wired behind `randomness.vdf.wesolowski`.
"""

import asyncio
import os
import time
import typing as t
from dataclasses import dataclass, field

# Prefer shared hashing helper (consistent domains across modules)
try:
    from proofs.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib
    def sha3_256(b: bytes) -> bytes:
        return hashlib.sha3_256(b).digest()

# Try to import the reference prover from the randomness module
_wes: t.Any
try:
    from randomness.vdf.wesolowski import prove as wes_prove  # type: ignore
    from randomness.vdf.wesolowski import Proof as WesProof   # (y, pi, l)
    _wes = ("ok",)
except Exception as _e:  # pragma: no cover
    _wes = None
    _wes_err = _e


# ---------------- Types ----------------

@dataclass(frozen=True)
class VDFJobSpec:
    modulus_n_hex: str        # hex string (no 0x)
    base_g_hex: str           # hex string (no 0x)
    iterations: int           # t (number of squarings)
    label: bytes              # domain-binding label (bytes, included in H)
    # optional tuning
    max_seconds: float | None = None  # soft cap (to avoid runaway t in dev)


@dataclass
class VDFResult:
    task_id: str
    proof_body: dict[str, t.Any]  # matches proofs/schemas/vdf.cddl fields
    metrics: dict[str, t.Any]     # {"vdf_seconds": float, "iterations": int}


@dataclass
class _Pending:
    spec: VDFJobSpec
    enqueued_at: float
    started_at: float | None = None


# ---------------- Worker ----------------

class VDFWorker:
    """
    Small async orchestrator:
      - enqueue(spec) → task_id
      - background task runs prove() in a thread executor
      - results land in an in-memory queue; pop_ready() retrieves them
    """
    def __init__(self, *, poll_interval_s: float = 0.25, queue_limit: int = 64) -> None:
        self._poll = float(poll_interval_s)
        self._limit = int(queue_limit)
        self._pending: dict[str, _Pending] = {}
        self._ready: asyncio.Queue[VDFResult] = asyncio.Queue(maxsize=self._limit)
        self._task: asyncio.Task | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    @classmethod
    def create_from_env(cls) -> "VDFWorker":
        poll_ms = int(os.getenv("VDF_POLL_MS", "250"))
        cap = int(os.getenv("VDF_QUEUE_LIMIT", "64"))
        return cls(poll_interval_s=poll_ms / 1000.0, queue_limit=cap)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="VDFWorker.poll")

    async def stop(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def enqueue(self, spec: VDFJobSpec) -> str:
        task_id = self._derive_task_id(spec)
        now = time.time()
        async with self._lock:
            if task_id not in self._pending:
                self._pending[task_id] = _Pending(spec=spec, enqueued_at=now)
        return task_id

    def pop_ready(self, max_n: int = 32) -> list[VDFResult]:
        out: list[VDFResult] = []
        for _ in range(max_n):
            try:
                out.append(self._ready.get_nowait())
                self._ready.task_done()
            except asyncio.QueueEmpty:
                break
        return out

    # ---------- internals ----------

    async def _run(self) -> None:
        try:
            while not self._closed:
                await self._drain_once()
                await asyncio.sleep(self._poll)
        except asyncio.CancelledError:
            return

    async def _drain_once(self) -> None:
        # pick one oldest pending job to avoid hogging
        async with self._lock:
            if not self._pending:
                return
            task_id, pend = next(iter(sorted(self._pending.items(), key=lambda kv: kv[1].enqueued_at)))
            # mark started to avoid duplicate picks
            if pend.started_at is None:
                pend.started_at = time.time()
            spec = pend.spec

        # compute proof in threadpool to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        try:
            start = time.time()
            proof_body = await loop.run_in_executor(None, _prove_and_package, spec)
            elapsed = time.time() - start
            res = VDFResult(
                task_id=task_id,
                proof_body=proof_body,
                metrics={"vdf_seconds": elapsed, "iterations": spec.iterations},
            )
            # push to ready queue (drop oldest on overflow)
            try:
                self._ready.put_nowait(res)
            except asyncio.QueueFull:
                try:
                    _ = self._ready.get_nowait()
                    self._ready.task_done()
                except asyncio.QueueEmpty:
                    pass
                self._ready.put_nowait(res)
        finally:
            async with self._lock:
                self._pending.pop(task_id, None)

    # deterministic task id tied to inputs (for idempotency)
    def _derive_task_id(self, spec: VDFJobSpec) -> str:
        h = sha3_256(
            b"animica.task.vdf\0" +
            bytes.fromhex(spec.modulus_n_hex) + b"\0" +
            bytes.fromhex(spec.base_g_hex) + b"\0" +
            spec.iterations.to_bytes(8, "big") + b"\0" +
            spec.label
        )
        return "vdf-" + h.hex()[:32]


# ---------------- Prover wrapper ----------------

def _prove_and_package(spec: VDFJobSpec) -> dict[str, t.Any]:
    """
    Run the reference Wesolowski prover (if available) and return a dict that
    matches the `VDFProof` body in `proofs/schemas/vdf.cddl`:

        {
          "n": <hex>,        # modulus (hex, lowercase, no 0x)
          "g": <hex>,        # base (hex)
          "t": <int>,        # iterations
          "l": <int>,        # challenge prime
          "y": <hex>,        # g^(2^t) mod n
          "pi": <hex>,       # proof element
          "label": <hex>,    # domain-binding label
        }
    """
    n_hex = _normalize_hex(spec.modulus_n_hex)
    g_hex = _normalize_hex(spec.base_g_hex)
    n = int(n_hex, 16)
    g = int(g_hex, 16)

    if _wes is None:
        raise RuntimeError(
            "randomness.vdf.wesolowski.prove not available. "
            "Enable the randomness module (randomness/vdf/wesolowski.py) or install the VDF dependency."
        )

    # The reference prover may accept (n, g, t, label) and return (y, pi, l)
    # We also pass an optional soft time cap to help dev runs.
    proof = wes_prove(n=n, g=g, t=int(spec.iterations), label=bytes(spec.label))
    # expected attributes: proof.y (int), proof.pi (int), proof.l (int)
    y = int(proof.y)
    pi = int(proof.pi)
    l = int(proof.l)

    return {
        "n": n_hex,
        "g": g_hex,
        "t": int(spec.iterations),
        "l": l,
        "y": f"{y:x}",
        "pi": f"{pi:x}",
        "label": spec.label.hex(),
    }


# ---------------- Utilities ----------------

def _normalize_hex(h: str) -> str:
    h = h.strip().lower()
    if h.startswith("0x"):
        h = h[2:]
    if len(h) == 0 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("bad hex input")
    # strip leading zeros for canonical form (but keep one zero if all zeros)
    h = h.lstrip("0") or "0"
    # pad to even length
    if len(h) % 2:
        h = "0" + h
    return h


# ---------------- Demo CLI ----------------

async def _demo() -> None:  # pragma: no cover
    """
    Minimal demonstration:
      - picks inputs from env or generates a small dev base
      - enqueues one VDF job
      - waits for completion and prints a short summary
    """
    # Inputs (for dev: you *must* pass realistic n,g,t in real runs)
    n_hex = os.getenv("VDF_MODULUS_HEX")
    if not n_hex:
        raise RuntimeError("Set VDF_MODULUS_HEX to a valid RSA modulus (hex).")
    # Bind g to a deterministic label; for dev we derive a base from label hash
    label_prefix = os.getenv("VDF_LABEL_PREFIX", "animica.vdf.demo:")
    mix = sha3_256(label_prefix.encode())
    g_hex = os.getenv("VDF_BASE_HEX") or mix.hex()
    t_iter = int(os.getenv("VDF_ITERATIONS", "200000"))

    job = VDFJobSpec(
        modulus_n_hex=n_hex,
        base_g_hex=g_hex,
        iterations=t_iter,
        label=b"animica.vdf.demo\0" + mix,
    )

    w = VDFWorker.create_from_env()
    await w.start()
    tid = await w.enqueue(job)
    print(f"[vdf_worker] enqueued {tid} (t={t_iter})")

    # Wait up to ~60s for the ready queue (dev)
    t_end = time.time() + 60.0
    got: list[VDFResult] = []
    while time.time() < t_end and not got:
        await asyncio.sleep(0.1)
        got.extend(w.pop_ready())

    if not got:
        print("[vdf_worker] timed out waiting for proof")
    else:
        r = got[0]
        print(f"[vdf_worker] done {r.task_id} "
              f"seconds={r.metrics['vdf_seconds']:.3f} "
              f"y[:8]={r.proof_body['y'][:8]} l={r.proof_body['l']}")

    await w.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
