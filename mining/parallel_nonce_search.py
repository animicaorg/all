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
    start_nonce: int, max_nonce: int, worker_id: int, workers: int
) -> Iterable[int]:
    end = start_nonce + max_nonce
    nonce = start_nonce + worker_id
    while nonce < end:
        yield nonce
        nonce += workers


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
) -> None:
    attempts = 0
    start_time = time.monotonic()
    for nonce in iter_stride(start_nonce, max_nonce, worker_id, workers):
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
) -> SearchResult | None:
    resolved_workers = resolve_worker_count(workers)
    if resolved_workers <= 1:
        start_time = time.monotonic()
        attempts = 0
        for nonce in range(start_nonce, start_nonce + max_nonce):
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
