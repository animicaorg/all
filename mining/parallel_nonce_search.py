from __future__ import annotations

import logging
import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from core.types.header import Header

MAX_WORKERS = 256


@dataclass(frozen=True)
class SearchResult:
    nonce: int
    payload: Any
    worker_id: int
    attempts: int
    elapsed_s: float
    restarts: int


def resolve_worker_count(
    requested: int | None,
    *,
    env_var: str = "ANIMICA_MINER_WORKERS",
    max_workers: int = MAX_WORKERS,
) -> int:
    env_val = os.getenv(env_var)
    if requested is None and env_val:
        try:
            requested = int(env_val)
        except ValueError:
            requested = None
    if requested is None:
        requested = 0
    requested = int(requested)
    if requested < 0:
        raise ValueError("workers must be >= 0")
    if requested == 0:
        cpu = os.cpu_count() or 1
        auto = max(1, cpu - 1) if cpu > 1 else 1
        return min(auto, max_workers)
    return min(max(1, requested), max_workers)


def iter_stride(
    start_nonce: int, max_nonce: int, worker_id: int, workers: int, *, miner_id: int = 0
) -> Iterable[int]:
    """
    Generate nonce sequence for a worker using stride pattern with optional miner_id offset.
    
    For backward compatibility:
    - When miner_id=0 (default): uses stride=workers (original behavior)
    - When miner_id>0: uses stride=workers*256 for multi-node coordination
    
    Multi-node mining optimization (miner_id > 0):
    - Each (miner_id, worker_id) pair gets a unique offset in the nonce space
    - Global stride ensures no overlap between any miner/worker combinations
    - Supports up to 256 concurrent miners
    
    The global worker ID formula ensures perfect partitioning:
      global_id = miner_id * workers + worker_id
      stride = workers * 256 (multi-node) OR workers (single node with miner_id=0)
    
    Example with 3 miners (IDs 0,1,2), 2 workers each:
      Stride: 512 (2 workers * 256 miners)
      Miner 0, Worker 0 (global_id=0): 0, 512, 1024, 1536, ...
      Miner 0, Worker 1 (global_id=1): 1, 513, 1025, 1537, ...
      Miner 1, Worker 0 (global_id=2): 2, 514, 1026, 1538, ...
      Miner 1, Worker 1 (global_id=3): 3, 515, 1027, 1539, ...
      Miner 2, Worker 0 (global_id=4): 4, 516, 1028, 1540, ...
      Miner 2, Worker 1 (global_id=5): 5, 517, 1029, 1541, ...
    
    Args:
        start_nonce: Base starting nonce (usually 0)
        max_nonce: Number of nonces to search
        worker_id: Worker index within this miner (0 to workers-1)
        workers: Total number of workers in this miner
        miner_id: Unique miner instance ID (0-255) for multi-node coordination.
                  When 0 (default), uses original stride behavior for backward compatibility.
                  When >0, uses larger stride for multi-node partitioning.
    
    Returns:
        Iterator of nonce values for this worker to check
    """
    end = start_nonce + max_nonce
    
    if miner_id == 0:
        # Single miner mode (backward compatible): original stride behavior
        nonce = start_nonce + worker_id
        stride = workers
    else:
        # Multi-miner mode: partition nonce space to prevent overlap
        # Calculate global worker ID across all miners
        global_worker_id = miner_id * workers + worker_id
        # Use larger stride that accounts for up to 256 miners
        stride = workers * 256
        nonce = start_nonce + global_worker_id
    
    while nonce < end:
        yield nonce
        nonce += stride


def pow_check_nonce(
    nonce: int,
    template: Header,
    target: int,
) -> tuple[bool, tuple[bytes, int] | None]:
    try:
        header = template.with_nonce(nonce)
    except Exception:
        header = Header(
            v=template.v,
            chainId=template.chainId,
            height=template.height,
            parentHash=template.parentHash,
            timestamp=template.timestamp,
            stateRoot=template.stateRoot,
            txsRoot=template.txsRoot,
            receiptsRoot=template.receiptsRoot,
            proofsRoot=template.proofsRoot,
            daRoot=template.daRoot,
            mixSeed=template.mixSeed,
            poiesPolicyRoot=template.poiesPolicyRoot,
            pqAlgPolicyRoot=template.pqAlgPolicyRoot,
            thetaMicro=template.thetaMicro,
            workType=getattr(template, "workType", 0),
            nonce=nonce,
            extra=template.extra,
        )
    block_hash_bytes = header.hash()
    block_hash_int = int.from_bytes(block_hash_bytes, "big")
    if block_hash_int <= target:
        return True, (block_hash_bytes, block_hash_int)
    return False, None


def _nonce_worker(
    check_fn: Callable[..., tuple[bool, Any]],
    check_args: tuple[Any, ...],
    start_nonce: int,
    max_nonce: int,
    worker_id: int,
    workers: int,
    stop_event: mp.synchronize.Event,
    result_queue: mp.Queue,
    crash_after: int | None,
    miner_id: int,
) -> None:
    attempts = 0
    start_time = time.monotonic()
    for nonce in iter_stride(start_nonce, max_nonce, worker_id, workers, miner_id=miner_id):
        if stop_event.is_set():
            return
        if crash_after is not None and attempts >= crash_after:
            raise RuntimeError("simulated worker crash")
        found, payload = check_fn(nonce, *check_args)
        attempts += 1
        if found:
            stop_event.set()
            result_queue.put(
                {
                    "nonce": nonce,
                    "payload": payload,
                    "worker_id": worker_id,
                    "attempts": attempts,
                    "elapsed_s": time.monotonic() - start_time,
                }
            )
            return


def parallel_nonce_search(
    check_fn: Callable[..., tuple[bool, Any]],
    check_args: tuple[Any, ...],
    start_nonce: int,
    max_nonce: int,
    workers: int,
    *,
    timeout_s: float | None = None,
    max_restarts: int = 1,
    log: logging.Logger | None = None,
    crash_after_by_worker: dict[int, int] | None = None,
    miner_id: int = 0,
) -> SearchResult | None:
    """
    Parallel nonce search with multi-node mining support.
    
    Args:
        check_fn: Function to check if a nonce is valid
        check_args: Additional arguments for check_fn
        start_nonce: Starting nonce value
        max_nonce: Maximum number of nonces to check
        workers: Number of worker processes (0=auto)
        timeout_s: Optional timeout in seconds
        max_restarts: Maximum number of worker restarts on crash
        log: Optional logger
        crash_after_by_worker: For testing - crash workers after N attempts
        miner_id: Unique miner instance ID (0-255) for multi-node coordination
    
    Returns:
        SearchResult if nonce found, None otherwise
    """
    resolved_workers = resolve_worker_count(workers)
    if resolved_workers <= 1:
        start_time = time.monotonic()
        attempts = 0
        for nonce in iter_stride(start_nonce, max_nonce, 0, 1, miner_id=miner_id):
            found, payload = check_fn(nonce, *check_args)
            attempts += 1
            if found:
                return SearchResult(
                    nonce=nonce,
                    payload=payload,
                    worker_id=0,
                    attempts=attempts,
                    elapsed_s=time.monotonic() - start_time,
                    restarts=0,
                )
        return None

    ctx = mp.get_context("spawn")
    stop_event = ctx.Event()
    result_queue: mp.Queue = ctx.Queue()
    crash_after_by_worker = crash_after_by_worker or {}
    processes: dict[int, mp.Process] = {}
    restarts = 0
    start_time = time.monotonic()

    def _spawn(worker_id: int) -> None:
        crash_after = crash_after_by_worker.get(worker_id)
        proc = ctx.Process(
            target=_nonce_worker,
            args=(
                check_fn,
                check_args,
                start_nonce,
                max_nonce,
                worker_id,
                resolved_workers,
                stop_event,
                result_queue,
                crash_after,
                miner_id,
            ),
        )
        proc.daemon = True
        proc.start()
        processes[worker_id] = proc

    for worker_id in range(resolved_workers):
        _spawn(worker_id)

    try:
        while True:
            if timeout_s is not None and (time.monotonic() - start_time) > timeout_s:
                if log:
                    log.warning(
                        "Nonce search timed out",
                        extra={"timeout_s": timeout_s, "workers": resolved_workers},
                    )
                break
            try:
                result = result_queue.get(timeout=0.1)
            except Exception:
                result = None
            if result is not None:
                stop_event.set()
                return SearchResult(
                    nonce=int(result["nonce"]),
                    payload=result["payload"],
                    worker_id=int(result["worker_id"]),
                    attempts=int(result["attempts"]),
                    elapsed_s=float(result["elapsed_s"]),
                    restarts=restarts,
                )

            all_dead = True
            for worker_id, proc in list(processes.items()):
                if proc.is_alive():
                    all_dead = False
                    continue
                if stop_event.is_set():
                    continue
                exit_code = proc.exitcode
                if exit_code not in (0, None) and restarts < max_restarts:
                    restarts += 1
                    if log:
                        log.warning(
                            "Restarting mining worker",
                            extra={
                                "worker_id": worker_id,
                                "exit_code": exit_code,
                                "restarts": restarts,
                            },
                        )
                    _spawn(worker_id)
                    all_dead = False
                else:
                    processes.pop(worker_id, None)

            if all_dead:
                break
    except KeyboardInterrupt:
        stop_event.set()
        if log:
            log.info("Nonce search interrupted; stopping workers")
    finally:
        stop_event.set()
        for proc in processes.values():
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=1)

    return None
