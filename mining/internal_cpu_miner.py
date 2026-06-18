from __future__ import annotations

"""
Reference Stratum miner used for tests, devnets, and the
`animica miner mine-blocks --pool-stratum` CLI path.

This helper wires ``StratumClient`` to a device-aware scanner (CUDA/ROCm/
OpenCL/Metal when available, CPU fallback) so the same code path drives
both GPU-equipped rigs and the pure-Python test environment. The class is
intentionally simple so tests can assert on specific nonces.
"""

import asyncio
import logging
import os
import secrets
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

from .hash_search import HashScanner, h_micro_from_digest, micro_threshold_to_target256
from .stratum_client import StratumClient
from .template_block import hash_candidate_header, header_from_template_view

log = logging.getLogger("mining.stratum_miner")

# Nonce band scanned by the torch GPU header-template path. Mirrors
# mining.gpu_torch_sha3.NONCE_BAND_{LO,HI}; duplicated here so this module
# imports cleanly on CPU-only boxes (gpu_torch_sha3 imports torch at module
# load, so it is imported lazily — only when a GPU is actually present).
_GPU_NONCE_BAND_LO = 1 << 16
_GPU_NONCE_BAND_HI = 1 << 32


@dataclass
class CpuMinerResult:
    job_id: str
    nonce: int
    h_micro: int
    accepted: bool
    is_block: bool
    reason: Optional[str]


def _select_device_backend(
    device: str = "auto", *, threads: int = 0, batch_size: int = 0
) -> tuple[Optional[Any], str]:
    """
    Resolve a `MiningDevice` instance from `mining.device` for the
    requested backend. Falls back through preference order on failure.

    Returns (device_backend_or_None, effective_device_name). If the
    returned backend is None the caller should use the pure-Python
    HashScanner path.
    """
    try:
        from . import device as _device_mod
    except Exception as exc:  # pragma: no cover - mining package always present
        log.warning("device module unavailable: %s; falling back to CPU scanner", exc)
        return None, "cpu"

    requested = (device or "auto").strip().lower()
    if requested == "auto":
        try:
            requested = _device_mod.auto_detect_device()
        except Exception as exc:
            log.warning("auto-detect failed: %s; falling back to CPU", exc)
            requested = "cpu"

    # Build a preference chain so we never silently fail to start mining.
    if requested == "cpu":
        order = ["cpu"]
    else:
        order = [requested, "cpu"]

    last_err: Optional[BaseException] = None
    for backend in order:
        try:
            dev = _device_mod.create(backend, threads=threads, batch_size=batch_size)
            return dev, backend
        except Exception as exc:
            last_err = exc
            log.info("device backend %s unavailable: %s", backend, exc)
            continue

    if last_err is not None:
        log.warning(
            "no device backend could be instantiated (last error: %s); "
            "falling back to pure-Python HashScanner",
            last_err,
        )
    return None, "cpu"


def _proc_scan_header_template(
    header_view: dict,
    theta_micro: int,
    share_ratio: float,
    start_nonce: int,
    scan_window: int,
) -> Optional[tuple]:
    """Pure-function CPU scan, designed to run inside a ProcessPoolExecutor
    worker so the Python GIL doesn't serialize scans across cores.

    Returns ``(nonce, h_micro)`` on first hit or ``None`` if the window
    finishes with no share. Module-level + positional-args so the
    function is picklable on every Python start method (fork/spawn).
    """
    target_int = micro_threshold_to_target256(
        max(1, int(theta_micro * max(share_ratio, 1e-9)))
    )
    header = header_from_template_view(header_view, nonce=0)
    end = start_nonce + scan_window
    for nonce in range(start_nonce, end):
        candidate_hash = hash_candidate_header(header, nonce=nonce)
        if candidate_hash.digest_int > target_int:
            continue
        return (nonce, h_micro_from_digest(candidate_hash.digest))
    return None


class CpuStratumMiner:
    """
    Stratum miner that scans for one share per notify and submits it.

    Despite the class name (kept for backwards compatibility with tests),
    this miner selects the best available hardware backend at construction
    time via `mining.device.auto_detect_device` and uses its `scan()`
    method. When no GPU backend is available, or `device='cpu'` is
    requested explicitly, the pure-Python `HashScanner` is used so the
    miner still works in environments without GPU drivers.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 23454,
        agent: str = "animica-cpu-miner/0.1",
        worker: str = "cpu.worker",
        address: str = "anim1qqq",
        scan_window: int = 10_000,
        device: str = "auto",
        threads: int = 0,
        batch_size: int = 0,
        tls: bool = False,
        tls_verify: bool = True,
    ) -> None:
        self._client = StratumClient(
            host, port, agent=agent, tls=tls, tls_verify=tls_verify
        )
        self._worker = worker
        self._address = address
        self._scanner = HashScanner()
        self._scan_window = scan_window

        # Device backend (GPU-aware). May be None when no hardware
        # backend is usable — in that case the legacy pure-Python
        # HashScanner path runs.
        self._device, self._device_name = _select_device_backend(
            device, threads=threads, batch_size=batch_size
        )
        if self._device is not None:
            try:
                info = self._device.info()
                log.info(
                    "stratum miner: device=%s name=%s vendor=%s",
                    self._device_name,
                    getattr(info, "name", "?"),
                    getattr(info, "vendor", "?"),
                )
            except Exception:
                log.info("stratum miner: device=%s", self._device_name)

        # Header-template share search — the path the live PoIES pool drives —
        # can run on a torch GPU (CUDA/MPS) when one is present. This is
        # independent of `self._device` (the pycuda u-draw backend, used only by
        # the legacy prefix path) and needs no pycuda: `mining.gpu_torch_sha3`
        # hashes the canonical Header digest on-GPU and host-re-verifies every
        # candidate, so a GPU bug can never surface an invalid share.
        self._torch_gpu_device: Optional[str] = None
        requested = (device or "auto").strip().lower()
        if requested not in ("cpu", ""):
            try:
                from .gpu_torch_sha3 import solo_device_available
                self._torch_gpu_device = solo_device_available(requested)
            except Exception as exc:  # torch missing / CPU-only build
                log.info("torch GPU scanner unavailable: %s", exc)
            if self._torch_gpu_device is not None:
                log.info(
                    "stratum miner: torch GPU header-template scanner active on %s",
                    self._torch_gpu_device,
                )

        self._share_target: float = 0.0
        self._theta_micro: int = 0
        self._stop = asyncio.Event()
        # The currently-running mine task per active notify. Cancelling
        # this when a new notify arrives prevents stale work from racing
        # with the new job.
        self._mine_task: Optional[asyncio.Task] = None
        # Auto-reconnect state. The pool can reset the TCP session mid-stream
        # (server restart, load-balancer recycle, NAT idle timeout). Rather
        # than stranding the miner, _handle_disconnect re-establishes the same
        # client with exponential backoff. _reconnecting guards against the
        # several close() callbacks a flapping connection can emit so only one
        # backoff loop runs at a time.
        self._reconnecting = False
        self._reconnect_min_backoff = 1.0
        self._reconnect_max_backoff = 60.0
        # Threads count used to fan out parallel CPU scans across worker
        # processes. 0 means "use all CPUs minus one" (the default in
        # resolve_worker_count). Stored for the continuous-scan loop.
        self._threads = int(threads)
        # Lazily-created ProcessPoolExecutor for pure-Python CPU mining.
        # We use processes (not just threads) because the per-nonce
        # Python overhead in _scan_header_template (dataclass replace,
        # serialize_header, dict access) holds the GIL — N threads stay
        # serialized on that overhead even though sha3 itself releases
        # the GIL. With processes, each worker gets its own interpreter
        # and pins a separate core. Initialised on first mining round
        # so simply constructing the miner doesn't fork workers.
        self._proc_pool: Optional[ProcessPoolExecutor] = None

    def _get_proc_pool(self) -> ProcessPoolExecutor:
        if self._proc_pool is None:
            n = self._threads if self._threads > 0 else (os.cpu_count() or 1)
            n = max(1, n)
            self._proc_pool = ProcessPoolExecutor(max_workers=n)
            log.info("[cpu-miner] mining process pool started with %d workers", n)
        return self._proc_pool

    @property
    def device_name(self) -> str:
        """Effective device backend in use (e.g. 'cuda', 'opencl', 'cpu')."""
        return self._device_name

    @staticmethod
    def _header_template_from_job(job: dict) -> Optional[dict]:
        header = job.get("header") or {}
        if not isinstance(header, dict):
            return None
        required = (
            "parentHash",
            "stateRoot",
            "txsRoot",
            "receiptsRoot",
            "proofsRoot",
            "daRoot",
            "mixSeed",
            "poiesPolicyRoot",
            "pqAlgPolicyRoot",
            "timestamp",
        )
        if not all(key in header for key in required):
            return None
        return header

    def _scan_header_template(
        self,
        header_view: dict,
        *,
        theta_micro: int,
        share_ratio: float,
        start_nonce: int = 0,
    ) -> Optional[CpuMinerResult]:
        target_int = micro_threshold_to_target256(
            max(1, int(theta_micro * max(share_ratio, 1e-9)))
        )
        header = header_from_template_view(header_view, nonce=0)
        end = start_nonce + self._scan_window
        for nonce in range(start_nonce, end):
            candidate_hash = hash_candidate_header(header, nonce=nonce)
            if candidate_hash.digest_int > target_int:
                continue
            h_micro = h_micro_from_digest(candidate_hash.digest)
            return CpuMinerResult(
                job_id=str(header_view.get("hash") or "unknown"),
                nonce=nonce,
                h_micro=h_micro,
                accepted=False,
                is_block=False,
                reason=None,
            )
        return None

    def _scan_header_template_gpu(
        self,
        header_template: dict,
        *,
        theta_micro: int,
        share_ratio: float,
        start_nonce: int,
        iterations: int,
    ) -> Optional[CpuMinerResult]:
        """GPU twin of `_scan_header_template`.

        Offloads the canonical ``Header.hash()`` search to the batched torch
        Keccak in ``mining.gpu_torch_sha3``. ``scan_solo`` host-re-verifies each
        GPU candidate against ``replace(header, nonce).hash()`` — byte-identical
        to ``hash_candidate_header(...).digest`` — so the share is always
        node-acceptable. Returns the first sub-share-target nonce, or None.
        """
        from .gpu_torch_sha3 import scan_solo

        target_int = micro_threshold_to_target256(
            max(1, int(theta_micro * max(share_ratio, 1e-9)))
        )
        header = header_from_template_view(header_template, nonce=0)
        nonce, digest = scan_solo(
            header,
            target_int,
            start_nonce=int(start_nonce),
            iterations=int(iterations),
            device=self._torch_gpu_device or "cuda",
        )
        if nonce is None or digest is None:
            return None
        return CpuMinerResult(
            job_id=str(header_template.get("hash") or "unknown"),
            nonce=int(nonce),
            h_micro=h_micro_from_digest(bytes(digest)),
            accepted=False,
            is_block=False,
            reason=None,
        )

    async def start(self) -> None:
        self._client.on_notify = self._on_notify
        self._client.on_set_difficulty = self._on_set_difficulty
        self._client.on_close = self._handle_disconnect
        await self._client.connect()
        await self._client.subscribe()
        await self._client.authorize(worker=self._worker, address=self._address)

    async def _handle_disconnect(self) -> None:
        """Reconnect the same client after an unexpected drop, then resume.

        Fired by ``StratumClient.close()`` (the rx loop calls it on EOF /
        reset). We re-run ``connect()`` + ``subscribe()`` + ``authorize()`` on
        the *existing* client on purpose — the CLI layer monkey-patches
        ``submit_share`` / ``on_notify`` / AICF handlers onto it after
        ``start()``, and a fresh client would silently drop all of them. The
        pool's first post-reconnect ``mining.notify`` spawns a new mine task
        via ``on_notify``, so PoW resumes on its own; we don't restart it here.
        """
        # Deliberate shutdown (stop()) also routes through close(); don't
        # fight it by reconnecting.
        if self._stop.is_set():
            return
        # A flapping connection can fire close() several times; keep a single
        # backoff loop.
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            # The mine task for the dead job is stale — cancel it so it stops
            # burning CPU on a template the pool will replace on reconnect.
            task = self._mine_task
            if task is not None and not task.done():
                task.cancel()
            backoff = self._reconnect_min_backoff
            attempt = 0
            while not self._stop.is_set():
                attempt += 1
                # Full jitter so a fleet of rigs doesn't reconnect in lockstep
                # after a pool restart.
                delay = backoff * (0.5 + secrets.randbelow(1000) / 1000.0)
                log.warning(
                    "[stratum-miner] connection lost; reconnect attempt %d in %.1fs",
                    attempt,
                    delay,
                )
                await asyncio.sleep(delay)
                if self._stop.is_set():
                    return
                try:
                    self._client.prepare_reconnect()
                    await self._client.connect()
                    await self._client.subscribe()
                    await self._client.authorize(
                        worker=self._worker, address=self._address
                    )
                    log.warning(
                        "[stratum-miner] reconnected after %d attempt(s); resuming",
                        attempt,
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning(
                        "[stratum-miner] reconnect attempt %d failed: %s",
                        attempt,
                        exc,
                    )
                    backoff = min(backoff * 2.0, self._reconnect_max_backoff)
        finally:
            self._reconnecting = False

    async def stop(self) -> None:
        self._stop.set()
        task = self._mine_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):    # noqa: BLE001
                pass
        if self._proc_pool is not None:
            # cancel_futures drops queued work; running workers finish
            # their current 10K-nonce scan (a few ms) and then exit.
            self._proc_pool.shutdown(wait=False, cancel_futures=True)
            self._proc_pool = None
        await self._client.close()

    async def _on_set_difficulty(self, share_target: float, theta_micro: int) -> None:
        self._share_target = float(share_target)
        self._theta_micro = int(theta_micro)

    async def _on_notify(self, job: dict) -> None:
        # Cancel any in-flight mine task for the previous notify before
        # starting fresh work. The previous task's nonce range may already
        # be invalid (different parent hash / target), so finishing it is
        # pure wasted compute. Spawn the new task on the event loop so the
        # client's rx loop stays free to receive submit responses.
        if self._stop.is_set():
            return
        prev = self._mine_task
        if prev is not None and not prev.done():
            prev.cancel()
        self._mine_task = asyncio.create_task(
            self._mine_continuously(dict(job)),
            name=f"stratum-mine:{job.get('jobId', '?')}",
        )

    async def _mine_continuously(self, job: dict) -> None:
        """Keep scanning new nonce windows for `job` until cancelled or stopped.

        Replaces the legacy one-shot-per-notify scan that left the CPU/GPU
        idle between notifies. Each iteration scans `scan_window` nonces
        starting from a fresh stride; found shares are submitted and the
        loop continues so we keep submitting shares (and any block found)
        until the pool sends a new notify, which cancels this task.
        """
        if self._stop.is_set():
            return
        header = job.get("header") or {}
        theta_micro = self._theta_micro or int(
            job.get("thetaMicro")
            or job.get("thetaTargetMicro")
            or job.get("theta_micro")
            or 0
        )
        if theta_micro <= 0:
            log.warning(
                "[cpu-miner] missing thetaMicro; cannot mine job %s",
                job.get("jobId"),
            )
            return
        share_ratio = float(job.get("shareTarget") or self._share_target or 0.0)
        if share_ratio <= 0.0:
            share_ratio = 1.0

        header_template = self._header_template_from_job(job)
        sign_hex = header.get("signBytes") if isinstance(header, dict) else None
        prefix: Optional[bytes] = None
        if (
            header_template is None
            and isinstance(sign_hex, str)
            and sign_hex.startswith("0x")
        ):
            try:
                prefix = bytes.fromhex(sign_hex[2:])
            except ValueError:
                prefix = None
        if header_template is None and prefix is None:
            log.warning(
                "[cpu-miner] missing usable header template; cannot mine job %s",
                job.get("jobId"),
            )
            return

        t_share_micro = max(1, int(theta_micro * share_ratio))
        mix_seed: bytes = b"\x00" * 32
        if isinstance(header, dict):
            mix_hex = (
                header.get("mixSeed")
                or (job.get("hints") or {}).get("mixSeed")
                or ""
            )
            if isinstance(mix_hex, str) and mix_hex.startswith("0x"):
                try:
                    mix_seed = bytes.fromhex(mix_hex[2:])
                except ValueError:
                    pass

        # Random starting nonce per worker keeps two miners on the same
        # template from racing the same nonce range. The pool also rotates
        # extranonce2 server-side, but starting random adds defense in depth.
        next_start = secrets.randbelow(2**32)
        windows = 0
        # Fan-out width: how many scan windows run in parallel per tick.
        # Honour --threads N; 0 means "all cores".
        n_workers = self._threads if self._threads > 0 else (os.cpu_count() or 1)
        n_workers = max(1, n_workers)
        # If we can run the pure-Python header-template scan, dispatch it
        # into a ProcessPoolExecutor so each worker pins a separate core.
        # Threads alone don't help here: the per-nonce Python overhead
        # (dataclass replace, serialize_header, dict access) holds the GIL
        # and serialises all threads even though sha3 itself releases it.
        # The fallback path (device backend / HashScanner) stays on
        # asyncio.to_thread because the device may not be picklable.
        # Prefer the torch GPU for the header-template path when available: the
        # kernel batches the whole nonce window on-device, so we run one wide
        # scan per tick instead of fanning out N CPU windows. The pycuda device
        # backend / HashScanner path (header_template is None) is unchanged.
        use_gpu_template = header_template is not None and self._torch_gpu_device is not None
        use_proc_pool = header_template is not None and not use_gpu_template
        proc_pool = self._get_proc_pool() if use_proc_pool else None
        loop = asyncio.get_running_loop()
        # GPU scans a wide window per tick (the kernel parallelizes internally);
        # at least ~1M nonces keeps the device busy between event-loop yields.
        gpu_window = max(self._scan_window, 1 << 20)
        try:
            while not self._stop.is_set():
                if use_gpu_template:
                    band = _GPU_NONCE_BAND_HI - _GPU_NONCE_BAND_LO
                    gpu_start = _GPU_NONCE_BAND_LO + (next_start % band)
                    try:
                        share = await asyncio.to_thread(
                            self._scan_header_template_gpu,
                            header_template,
                            theta_micro=theta_micro,
                            share_ratio=share_ratio,
                            start_nonce=gpu_start,
                            iterations=gpu_window,
                        )
                    except Exception as exc:
                        # A GPU failure must never stop mining: drop to the CPU
                        # process pool for the rest of this run and carry on.
                        log.warning(
                            "[stratum-miner] torch GPU scan failed (%s); "
                            "falling back to CPU process pool", exc)
                        self._torch_gpu_device = None
                        use_gpu_template = False
                        use_proc_pool = True
                        proc_pool = self._get_proc_pool()
                        continue
                    windows += 1
                    if share is not None:
                        try:
                            await self._submit_found_share(job, share)
                        except (OSError, RuntimeError) as exc:
                            log.warning(
                                "[stratum-miner] share submit failed "
                                "(connection down): %s", exc)
                            return
                    next_start = (next_start + gpu_window) & 0xFFFFFFFFFFFFFFFF
                    # Yield so the rx loop can service notify / submit responses.
                    await asyncio.sleep(0)
                    continue

                # Carve N disjoint nonce ranges and scan them in parallel.
                # First worker to find a share wins; we let in-flight ones
                # finish (cheap, a few ms each) so we don't throw away
                # their work.
                tasks: list[asyncio.Future] = []
                if use_proc_pool and proc_pool is not None:
                    for i in range(n_workers):
                        s = (next_start + i * self._scan_window) & 0xFFFFFFFFFFFFFFFF
                        tasks.append(loop.run_in_executor(
                            proc_pool,
                            _proc_scan_header_template,
                            header_template,
                            theta_micro,
                            share_ratio,
                            s,
                            self._scan_window,
                        ))
                else:
                    for i in range(n_workers):
                        s = (next_start + i * self._scan_window) & 0xFFFFFFFFFFFFFFFF
                        tasks.append(asyncio.ensure_future(self._scan_one_window(
                            job=job,
                            header_template=header_template,
                            prefix=prefix,
                            mix_seed=mix_seed,
                            theta_micro=theta_micro,
                            share_ratio=share_ratio,
                            t_share_micro=t_share_micro,
                            start_nonce=s,
                        )))
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                share: Optional[CpuMinerResult] = None

                def _adopt(result: Any) -> Optional[CpuMinerResult]:
                    if isinstance(result, CpuMinerResult):
                        return result
                    if isinstance(result, tuple) and len(result) == 2:
                        nonce_val, h_micro_val = result
                        return CpuMinerResult(
                            job_id=str(job.get("jobId") or "unknown"),
                            nonce=int(nonce_val),
                            h_micro=int(h_micro_val),
                            accepted=False,
                            is_block=False,
                            reason=None,
                        )
                    return None

                for t in done:
                    try:
                        r = t.result()
                    except Exception:
                        r = None
                    cand = _adopt(r)
                    if cand is not None and share is None:
                        share = cand
                if pending:
                    # Always await the rest — process-pool futures can't
                    # be cancelled mid-scan anyway, and a worker that
                    # already found a share shouldn't be discarded.
                    rest = await asyncio.gather(*pending, return_exceptions=True)
                    for r in rest:
                        cand = _adopt(r)
                        if cand is not None and share is None:
                            share = cand
                windows += n_workers
                if share is not None:
                    try:
                        await self._submit_found_share(job, share)
                    except (OSError, RuntimeError) as exc:
                        # Socket died between finding the share and submitting
                        # it: ConnectionResetError from writer.drain(), or the
                        # "connection closed" RuntimeError from a pending
                        # future the client rejected on EOF. The client's rx
                        # loop fires on_close -> _handle_disconnect, which
                        # reconnects; this job is now stale, so end the task
                        # cleanly instead of letting it die as an unretrieved
                        # task exception.
                        log.warning(
                            "[stratum-miner] share submit failed (connection down): %s",
                            exc,
                        )
                        return
                # Slide the search window forward; wrap at 64-bit nonce
                # space to avoid overflow during long mining runs.
                next_start = (next_start + n_workers * self._scan_window) & 0xFFFFFFFFFFFFFFFF
                # Yield to the event loop so the rx loop can run and
                # process incoming notify / submit responses; without this
                # await, a CPU-only scan_batch call would starve everything
                # else on the loop.
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            log.debug(
                "[stratum-miner] mining task cancelled for job %s after %d window(s)",
                job.get("jobId"),
                windows,
            )
            raise

    async def _scan_one_window(
        self,
        *,
        job: dict,
        header_template: Optional[dict],
        prefix: Optional[bytes],
        mix_seed: bytes,
        theta_micro: int,
        share_ratio: float,
        t_share_micro: int,
        start_nonce: int,
    ) -> Optional[CpuMinerResult]:
        """Run a single scan window for `job` and return the first share found.

        Heavy CPU work runs in a worker thread via `asyncio.to_thread` so the
        rx loop can keep servicing pool messages. The device backend (GPU)
        gets called directly because it returns quickly after dispatching a
        kernel and the caller already awaits between windows.
        """
        if header_template is not None:
            return await asyncio.to_thread(
                self._scan_header_template,
                header_template,
                theta_micro=theta_micro,
                share_ratio=share_ratio,
                start_nonce=start_nonce,
            )

        assert prefix is not None
        if self._device is not None:
            try:
                prepared = self._device.prepare_header(prefix, mix_seed)
                found = self._device.scan(
                    prepared,
                    theta_micro=float(t_share_micro),
                    start_nonce=start_nonce,
                    iterations=self._scan_window,
                    max_found=1,
                    thread_id=0,
                )
            except Exception as exc:
                log.warning(
                    "[stratum-miner] %s scan failed (%s); falling back to "
                    "pure-Python HashScanner for this job",
                    self._device_name,
                    exc,
                )
                found = None
            if found:
                entry = found[0]
                nonce_val = int(entry.get("nonce") or 0)
                h_micro_val = entry.get("h_micro")
                if h_micro_val is None:
                    digest = entry.get("digest") or entry.get("hash") or b""
                    if isinstance(digest, str) and digest.startswith("0x"):
                        digest = bytes.fromhex(digest[2:])
                    if isinstance(digest, (bytes, bytearray)) and digest:
                        h_micro_val = h_micro_from_digest(bytes(digest))
                    else:
                        h_micro_val = 0
                return CpuMinerResult(
                    job_id=str(job.get("jobId") or "unknown"),
                    nonce=nonce_val,
                    h_micro=int(h_micro_val),
                    accepted=False,
                    is_block=False,
                    reason=None,
                )

        shares = await asyncio.to_thread(
            self._scanner.scan_batch,
            prefix,
            t_share_micro,
            nonce_start=start_nonce,
            nonce_count=self._scan_window,
            theta_micro=theta_micro,
        )
        if shares:
            found_share = shares[0]
            return CpuMinerResult(
                job_id=str(job.get("jobId") or "unknown"),
                nonce=found_share.nonce,
                h_micro=found_share.h_micro,
                accepted=False,
                is_block=False,
                reason=None,
            )
        return None

    async def _submit_found_share(self, job: dict, share: CpuMinerResult) -> None:
        """Submit a single share to the pool, attaching any pending UW proofs."""
        hs_body = {"nonce": hex(share.nonce), "body": {"hMicro": share.h_micro}}

        # Attach any pending UsefulWorkProof envelopes from the AI/Quantum/
        # Storage/VDF workers (see mining.uw_inbox). The node-side UWP
        # verifier credits bonus AICF credits to the miner when accepted.
        attached_proofs = []
        try:
            from . import uw_inbox as _uw_inbox

            attached_proofs = _uw_inbox.drain(max_n=4)
        except Exception:
            attached_proofs = []

        if attached_proofs:
            res = await self._client.submit_share(
                job["jobId"], hs_body, proofs=attached_proofs
            )
        else:
            res = await self._client.submit_share(job["jobId"], hs_body)
        log.info(
            "[stratum-miner] submitted nonce=%d accepted=%s attached_proofs=%d device=%s",
            share.nonce,
            res.get("accepted"),
            len(attached_proofs),
            self._device_name,
        )

    async def run_until_stopped(self) -> None:
        await self.start()
        await self._stop.wait()
        await self.stop()
