from __future__ import annotations

"""
Animica miner orchestrator.

Responsibilities
- Poll/refresh block templates (from local node RPC or in-proc builder).
- Drive device backend (CPU/GPU) scanning against the active template.
- Collect found HashShare proofs into a queue and submit them via RPC.
- Optionally run useful-work helpers (AI/Quantum/Storage/VDF) in parallel.
- Broadcast fresh templates to WS getwork hub when available.

This file deliberately "duck-types" dependencies so it can work with the
implementations provided in mining/*.py without creating tight coupling:
- template provider: any object exposing `async def current_template()->dict|None`
                      and optionally `async def refresh()->dict|None`.
- submitter:         any object exposing `async def submit(params: dict)->dict`
- scanner:           either:
                       * module mining.hash_search exposing `class Scanner(...)`
                         with `async def run(self, template_iter, out_queue, stop_evt)`
                         OR
                       * module mining.hash_search exposing
                           `async def scan_forever(template_iter, out_queue, stop_evt, **kw)`
- WS hub (optional): mining.ws_getwork.get_hub() which exposes `broadcast_new_work(template)`

If a preferred symbol is not present, we fall back to safe/slow implementations.

This orchestrator is used by mining/cli/miner.py.
"""

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

JSON = Dict[str, Any]


# --------------------------- Logging & Metrics ---------------------------

log = logging.getLogger("mining.orchestrator")

try:
    # Optional Prometheus metrics (mining/metrics.py)
    from .metrics import (
        MINER_FOUND_SHARES,
        MINER_SUBMIT_OK,
        MINER_SUBMIT_REJECT,
        MINER_SUBMIT_LATENCY_SEC,
        MINER_ACTIVE_TEMPLATE_AGE_SEC,
        MINER_SCANNER_HASHRATE_ABS,  # abstract shares/s
    )
except Exception:
    # No-op fallbacks
    class _Counter:
        def inc(self, *a, **k): ...
        def observe(self, *a, **k): ...
        def set(self, *a, **k): ...

    MINER_FOUND_SHARES = _Counter()
    MINER_SUBMIT_OK = _Counter()
    MINER_SUBMIT_REJECT = _Counter()
    MINER_SUBMIT_LATENCY_SEC = _Counter()
    MINER_ACTIVE_TEMPLATE_AGE_SEC = _Counter()
    MINER_SCANNER_HASHRATE_ABS = _Counter()


# --------------------------- Adapters (duck-typed) ---------------------------

def _try_import_hash_scanner():
    """
    Tries to import a scanner implementation from mining.hash_search.
    Supports either:
      - class Scanner(...).run(template_iter, out_q, stop_evt)
      - async def scan_forever(template_iter, out_q, stop_evt, **kw)
    """
    try:
        from . import hash_search as _hs  # type: ignore
    except Exception as e:  # pragma: no cover
        log.warning("hash_search module not available, falling back to naive scanner: %s", e)
        return None, None

    scanner_cls = getattr(_hs, "Scanner", None)
    scan_forever = getattr(_hs, "scan_forever", None)
    return scanner_cls, scan_forever


def _try_import_ws_hub():
    try:
        from .ws_getwork import get_hub  # type: ignore
        return get_hub()
    except Exception:
        return None


# --------------------------- Orchestrator Config ---------------------------

@dataclass
class OrchestratorConfig:
    # Template polling
    template_interval_sec: float = float(os.getenv("ANIMICA_MINER_TEMPLATE_INTERVAL", "1.0"))
    template_stale_after_sec: float = float(os.getenv("ANIMICA_MINER_TEMPLATE_STALE_AFTER", "20.0"))

    # Submission
    submit_max_concurrency: int = int(os.getenv("ANIMICA_MINER_SUBMIT_CONCURRENCY", "4"))
    submit_backoff_initial: float = float(os.getenv("ANIMICA_MINER_SUBMIT_BACKOFF_INITIAL", "0.25"))
    submit_backoff_max: float = float(os.getenv("ANIMICA_MINER_SUBMIT_BACKOFF_MAX", "3.0"))

    # Scanner
    device_kind: str = os.getenv("ANIMICA_MINER_DEVICE", "cpu")  # cpu|cuda|rocm|opencl|metal
    threads: int = int(os.getenv("ANIMICA_MINER_THREADS", "0"))  # 0 => auto (per backend)

    # Useful-work workers
    run_ai_worker: bool = os.getenv("ANIMICA_MINER_AI_WORKER", "1") != "0"
    run_quantum_worker: bool = os.getenv("ANIMICA_MINER_QUANTUM_WORKER", "1") != "0"
    run_storage_worker: bool = os.getenv("ANIMICA_MINER_STORAGE_WORKER", "1") != "0"
    run_vdf_worker: bool = os.getenv("ANIMICA_MINER_VDF_WORKER", "1") != "0"

    # WS getwork hub broadcasting
    broadcast_new_work: bool = os.getenv("ANIMICA_MINER_BROADCAST_WS", "1") != "0"


# --------------------------- Template Iterator ---------------------------

class TemplateFeeder:
    """
    Async iterator that yields the latest template whenever it changes.
    """

    def __init__(
        self,
        provider: Any,  # duck-typed: .current_template() and optional .refresh()
        *,
        interval_sec: float,
        ws_hub: Any | None,
        stale_after_sec: float,
    ) -> None:
        self._provider = provider
        self._interval = interval_sec
        self._stale_after = stale_after_sec
        self._stop = asyncio.Event()
        self._last_job_id: Optional[str] = None
        self._last_ts: float = 0.0
        self._ws_hub = ws_hub

    def stop(self) -> None:
        self._stop.set()

    async def _get_current(self) -> Optional[JSON]:
        cur = await _maybe_await(self._provider, "current_template")
        return cur

    async def _refresh(self) -> Optional[JSON]:
        if hasattr(self._provider, "refresh"):
            return await _maybe_await(self._provider, "refresh")
        return await self._get_current()

    def __aiter__(self) -> AsyncIterator[JSON]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[JSON]:
        while not self._stop.is_set():
            try:
                tpl = await self._refresh()
                if tpl and isinstance(tpl, dict):
                    job_id = str(tpl.get("jobId") or tpl.get("job_id") or tpl.get("headerHash") or "")
                    # Detect changes
                    if job_id and job_id != self._last_job_id:
                        self._last_job_id = job_id
                        self._last_ts = time.time()
                        if self._ws_hub is not None:
                            try:
                                await self._ws_hub.broadcast_new_work(tpl)  # type: ignore
                            except Exception:
                                log.debug("WS hub broadcast_new_work failed", exc_info=True)
                        yield tpl
                    else:
                        # Track staleness
                        if self._last_ts:
                            age = max(0.0, time.time() - self._last_ts)
                            MINER_ACTIVE_TEMPLATE_AGE_SEC.set(age)  # type: ignore
                        # If we never yielded, still yield the first template anyway
                        if self._last_job_id is None:
                            self._last_job_id = job_id or "genesis"
                            self._last_ts = time.time()
                            if tpl:
                                yield tpl
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                # keep looping
                pass
            except Exception:
                log.warning("TemplateFeeder refresh failed", exc_info=True)
                await asyncio.sleep(self._interval)


# --------------------------- Scanner Adapter ---------------------------

class ScannerAdapter:
    """
    Wraps whichever scanner implementation is present.
    """

    def __init__(self, *, device_kind: str, threads: int) -> None:
        scanner_cls, scan_forever = _try_import_hash_scanner()
        self._scanner_cls = scanner_cls
        self._scan_forever = scan_forever
        self._device_kind = device_kind
        self._threads = threads

    async def run(
        self,
        template_iter: AsyncIterator[JSON],
        out_queue: "asyncio.Queue[JSON]",
        stop_evt: asyncio.Event,
    ) -> None:
        if self._scanner_cls is not None:
            log.info("Starting scanner via mining.hash_search.Scanner (device=%s, threads=%s)",
                     self._device_kind, self._threads or "auto")
            scanner = self._scanner_cls(device=self._device_kind, threads=self._threads)  # type: ignore
            await scanner.run(template_iter, out_queue, stop_evt)  # type: ignore
            return

        if self._scan_forever is not None:
            log.info("Starting scanner via mining.hash_search.scan_forever (device=%s, threads=%s)",
                     self._device_kind, self._threads or "auto")
            await self._scan_forever(  # type: ignore
                template_iter=template_iter,
                out_queue=out_queue,
                stop_evt=stop_evt,
                device=self._device_kind,
                threads=self._threads,
            )
            return

        # Fallback: naive CPU scan with very low throughput (dev-only)
        log.warning("Falling back to naive CPU scanner (dev-only)")
        await _naive_cpu_scanner(template_iter, out_queue, stop_evt)


# --------------------------- Submission Pipe ---------------------------

class SubmitPipe:
    """
    Concurrent submitter that pulls shares from a queue and submits over RPC.
    """

    def __init__(
        self,
        submitter: Any,  # exposes async def submit(params)->dict
        *,
        max_concurrency: int,
        backoff_initial: float,
        backoff_max: float,
    ) -> None:
        self._submitter = submitter
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._b0 = max(0.01, backoff_initial)
        self._bmax = max(self._b0, backoff_max)

    async def run(self, in_queue: "asyncio.Queue[JSON]", stop_evt: asyncio.Event) -> None:
        async def _worker():
            while not stop_evt.is_set():
                try:
                    share = await asyncio.wait_for(in_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                await self._handle_one(share)

        # Spawn N workers
        tasks = [asyncio.create_task(_worker()) for _ in range(max(1, self._sem._value))]  # type: ignore
        await stop_evt.wait()
        # Drain gracefully
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_one(self, share: JSON) -> None:
        backoff = self._b0
        while True:
            t0 = time.perf_counter()
            try:
                async with self._sem:
                    res = await _maybe_await(self._submitter, "submit", share)
                dt = time.perf_counter() - t0
                MINER_SUBMIT_LATENCY_SEC.observe(dt)  # type: no cover
                if res.get("accepted"):
                    MINER_SUBMIT_OK.inc()  # type: ignore
                else:
                    MINER_SUBMIT_REJECT.inc()  # type: ignore
                    reason = res.get("reason", "unknown")
                    log.info("Share rejected: %s", reason)
                return
            except Exception as e:
                dt = time.perf_counter() - t0
                MINER_SUBMIT_LATENCY_SEC.observe(dt)  # type: ignore
                log.warning("Submit failed (%s). Retrying in %.2fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(self._bmax, backoff * 2.0)


# --------------------------- Workers (optional) ---------------------------

async def _maybe_run_worker(module_name: str, fn_name: str, stop_evt: asyncio.Event) -> None:
    """
    Import mining.{module_name} and run its coroutine function `main` or `run`.
    """
    try:
        mod = __import__(f"mining.{module_name}", fromlist=["*"])
        fn = getattr(mod, fn_name, None) or getattr(mod, "main", None) or getattr(mod, "run", None)
        if fn is None:
            log.debug("Worker %s has no runnable entrypoint", module_name)
            return
        await fn(stop_evt=stop_evt)  # type: ignore
    except Exception:
        log.warning("Worker %s crashed", module_name, exc_info=True)


# --------------------------- Orchestrator ---------------------------

@dataclass
class MinerOrchestrator:
    template_provider: Any
    submitter: Any
    config: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    ws_hub: Any | None = field(default_factory=_try_import_ws_hub)

    # internals
    _stop: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _tasks: list[asyncio.Task] = field(default_factory=list, init=False)
    _share_queue: "asyncio.Queue[JSON]" = field(default_factory=lambda: asyncio.Queue(maxsize=2048), init=False)
    _scanner: ScannerAdapter = field(default=None, init=False)  # type: ignore
    _feeder: TemplateFeeder = field(default=None, init=False)  # type: ignore

    def __post_init__(self) -> None:
        self._scanner = ScannerAdapter(
            device_kind=self.config.device_kind,
            threads=self.config.threads,
        )
        self._feeder = TemplateFeeder(
            provider=self.template_provider,
            interval_sec=self.config.template_interval_sec,
            ws_hub=self.ws_hub if self.config.broadcast_new_work else None,
            stale_after_sec=self.config.template_stale_after_sec,
        )

    async def start(self) -> None:
        log.info("MinerOrchestrator starting (device=%s, threads=%s)",
                 self.config.device_kind, self.config.threads or "auto")
        # Scanner
        self._tasks.append(asyncio.create_task(
            self._scanner.run(self._feeder.__aiter__(), self._share_queue, self._stop),
            name="scanner",
        ))
        # Submitter
        submit_pipe = SubmitPipe(
            self.submitter,
            max_concurrency=self.config.submit_max_concurrency,
            backoff_initial=self.config.submit_backoff_initial,
            backoff_max=self.config.submit_backoff_max,
        )
        self._tasks.append(asyncio.create_task(
            submit_pipe.run(self._share_queue, self._stop),
            name="submitter",
        ))
        # Optional workers
        if self.config.run_ai_worker:
            self._tasks.append(asyncio.create_task(
                _maybe_run_worker("ai_worker", "run", self._stop),
                name="ai-worker",
            ))
        if self.config.run_quantum_worker:
            self._tasks.append(asyncio.create_task(
                _maybe_run_worker("quantum_worker", "run", self._stop),
                name="quantum-worker",
            ))
        if self.config.run_storage_worker:
            self._tasks.append(asyncio.create_task(
                _maybe_run_worker("storage_worker", "run", self._stop),
                name="storage-worker",
            ))
        if self.config.run_vdf_worker:
            self._tasks.append(asyncio.create_task(
                _maybe_run_worker("vdf_worker", "run", self._stop),
                name="vdf-worker",
            ))

        # Signal handlers (posix)
        _install_signal_handlers(self.stop)

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stop.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self._stop.is_set():
            return
        log.info("MinerOrchestrator stopping...")
        self._stop.set()
        self._feeder.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # drain queue (best-effort)
        try:
            while not self._share_queue.empty():
                _ = self._share_queue.get_nowait()
        except Exception:
            pass
        log.info("MinerOrchestrator stopped.")


# --------------------------- Helpers ---------------------------

def _install_signal_handlers(stop_coro: Callable[[], Awaitable[None]]) -> None:
    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue

        def _handler(*_a, **_k):
            asyncio.create_task(stop_coro())

        try:
            loop.add_signal_handler(sig, _handler)
        except NotImplementedError:
            # Windows or non-main thread
            pass


async def _maybe_await(obj: Any, attr: str, *args, **kwargs):
    fn = getattr(obj, attr, None)
    if fn is None:
        raise AttributeError(f"{obj!r} has no attribute {attr!r}")
    res = fn(*args, **kwargs)
    if asyncio.iscoroutine(res):
        return await res
    return res


# --------------------------- Naive CPU Scanner (fallback) ---------------------------

async def _naive_cpu_scanner(
    template_iter: AsyncIterator[JSON],
    out_queue: "asyncio.Queue[JSON]",
    stop_evt: asyncio.Event,
) -> None:
    """
    Extremely slow reference scanner that:
      - takes a template {header: hex, targetBits: int, jobId: str}
      - increments nonce and checks a trivial threshold on sha3(header||nonce)
    This is *not* the production HashShare search; it's a developer fallback.
    """
    import hashlib
    import secrets

    cur_tpl: Optional[JSON] = None
    nonce = 0
    last_job = ""

    async def _template_pump():
        nonlocal cur_tpl, nonce, last_job
        async for tpl in template_iter:
            cur_tpl = tpl
            last_job = str(tpl.get("jobId", ""))
            nonce = secrets.randbits(32)

    pump_task = asyncio.create_task(_template_pump(), name="template-pump")

    try:
        while not stop_evt.is_set():
            if not cur_tpl:
                await asyncio.sleep(0.05)
                continue

            header = bytes.fromhex(cur_tpl.get("header", ""))
            target_bits = int(cur_tpl.get("targetBits", 12))  # small => easy dev mining
            # quick "difficulty": require that hash starts with target_bits zero bits
            # compute 256-bit hash = sha3_256(header || nonce_le)
            nonce_bytes = nonce.to_bytes(8, "little")
            h = hashlib.sha3_256(header + nonce_bytes).digest()
            # leading zeros count
            lz = _leading_zero_bits(h)
            if lz >= target_bits:
                share = {
                    "kind": "HashShare",
                    "jobId": last_job,
                    "nonce": int(nonce),
                    "mixHash": h.hex(),
                    "targetBits": target_bits,
                }
                MINER_FOUND_SHARES.inc()  # type: ignore
                # non-blocking put with small wait to avoid deadlocks
                try:
                    await asyncio.wait_for(out_queue.put(share), timeout=0.5)
                except asyncio.TimeoutError:
                    log.debug("share queue full; dropping share")

            nonce = (nonce + 1) & 0xFFFFFFFF
            # very gentle loop
            await asyncio.sleep(0)

    except asyncio.CancelledError:
        pass
    finally:
        pump_task.cancel()
        await asyncio.gather(pump_task, return_exceptions=True)


def _leading_zero_bits(b: bytes) -> int:
    # Count leading zero bits in byte-string
    n = 0
    for byte in b:
        if byte == 0:
            n += 8
            continue
        # first nonzero byte
        for i in range(7, -1, -1):
            if (byte >> i) & 1:
                return n + (7 - i)
        return n + 8
    return n


# --------------------------- Convenience factory ---------------------------

async def run_orchestrator(
    *,
    template_provider: Any,
    submitter: Any,
    config: Optional[OrchestratorConfig] = None,
) -> None:
    """
    Fire-and-forget convenience. Example:

        from mining.templates import TemplateBuilder
        from mining.share_submitter import ShareSubmitter

        prov = TemplateBuilder(rpc_url="http://127.0.0.1:8547")
        subm = ShareSubmitter(rpc_url="http://127.0.0.1:8547")

        await run_orchestrator(template_provider=prov, submitter=subm)
    """
    orch = MinerOrchestrator(
        template_provider=template_provider,
        submitter=submitter,
        config=config or OrchestratorConfig(),
    )
    await orch.run_forever()


__all__ = [
    "OrchestratorConfig",
    "MinerOrchestrator",
    "run_orchestrator",
]
