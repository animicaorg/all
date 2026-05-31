"""Monero (RandomX) side of the dual-mining pool.

Architecture
------------
The Animica pool runs a SHA3-256 path (the canonical Animica algo) AND a
parallel RandomX path that proxies Monero block templates. Each miner
connects once and gets BOTH job types pushed over the same Stratum
connection; the patched xmrig fork (`--dual-mode 50`) splits its worker
threads 50/50 between SHA3 and RandomX.

Roles inside this module:

* `MoneroDaemonClient`
  Thin async JSON-RPC client for the local `monerod`. Pulls block
  templates (`get_block_template`), submits found blocks (`submit_block`),
  and watches for chain reorgs (`get_info` poll loop).

* `XmrJob`
  A Stratum job constructed from one block template. Carries the blob
  to hash, the per-share target (set by pool difficulty), the network
  block target, the seed_hash (drives RandomX cache rotation), and the
  template's height so we can detect stale shares.

* `XmrJobManager`
  Owns the upstream poll loop. New templates → emits a new XmrJob and
  pushes `mining.notify_xmr` to all subscribed connections. Manages
  the rotating set of `Cache` objects (one per seed_hash; old ones
  retired ~120 seconds after a new seed lands so late shares from
  paused workers still validate).

* `XmrShareValidator`
  Receives `mining.submit_xmr` payloads (job_id, nonce, result), checks
  staleness, recomputes the RandomX hash, compares against the share
  target, and (if it also clears the block target) hands the assembled
  block blob to `MoneroDaemonClient.submit_block`. Pool keeps one
  validator-thread pool of librandomx VMs sized to CPU count.

* `XmrShareLedger`
  Per-miner share-weight accumulator. The PPS fee model says a miner
  earns proportional XMR for each accepted share scaled by the share's
  effective difficulty. When monerod reports a block we found, the
  ledger snapshot at that block's height becomes the payout basis;
  95% gets queued to miner addresses, 5% stays in the pool fee wallet.

The pool's existing SHA3 share path is untouched — `core.py` and
`stratum_server.py` only learn about new method names
(`mining.notify_xmr`, `mining.submit_xmr`) and route those to this module.

monerod expects to be local + restricted-RPC (no wallet binding); see
/etc/systemd/system/monerod.service for the canonical config. The pool
NEVER talks to the wallet RPC directly; payouts go through a separate
`monero-wallet-rpc` process driven by `payouts.py`.
"""

from __future__ import annotations

import asyncio
import binascii
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from animica.stratum_pool._randomx import (
    RANDOMX_FLAG_DEFAULT,
    RANDOMX_FLAG_JIT,
    Cache,
    VM,
    recommended_flags,
)


_LOG = logging.getLogger("animica.stratum_pool.xmr")


# ── RPC client ──────────────────────────────────────────────────────────

class MoneroDaemonClient:
    """JSON-RPC client for the local monerod instance.

    monerod exposes two endpoint styles:
    - /json_rpc with `method` + `params` (most calls)
    - top-level /<verb> for some legacy methods (e.g. /get_height)

    We use /json_rpc for everything we need.
    """

    def __init__(
        self,
        url: str = "http://127.0.0.1:18081",
        timeout: float = 10.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self._url = url.rstrip("/") + "/json_rpc"
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._own_client = client is None

    async def close(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": "0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body and body["error"]:
            err = body["error"]
            raise RuntimeError(
                f"monerod RPC {method} failed: code={err.get('code')} msg={err.get('message')}"
            )
        return body.get("result", {})

    async def get_info(self) -> Dict[str, Any]:
        return await self._call("get_info")

    async def get_block_template(
        self, wallet_address: str, reserve_size: int = 8
    ) -> Dict[str, Any]:
        """Fetch a block template paying to `wallet_address`. The reserved
        bytes inside the coinbase are where the pool stuffs the share
        nonce so we can identify which miner contributed which template."""
        return await self._call(
            "get_block_template",
            {"wallet_address": wallet_address, "reserve_size": reserve_size},
        )

    async def submit_block(self, block_hex: str) -> Dict[str, Any]:
        """Submit a solved block (full hex) to the network."""
        payload = {
            "jsonrpc": "2.0",
            "id": "0",
            "method": "submit_block",
            "params": [block_hex],
        }
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise RuntimeError(f"submit_block rejected: {body['error']}")
        return body.get("result", {})

    async def is_synced(self) -> bool:
        try:
            info = await self.get_info()
            return bool(info.get("synchronized"))
        except Exception:
            return False


# ── job + ledger types ─────────────────────────────────────────────────

@dataclass
class XmrJob:
    """One Monero block template wrapped as a Stratum job."""
    job_id: str
    height: int
    blob: bytes              # 76-byte (or longer with reserve bytes) blob to hash
    blockhashing_blob: bytes # canonical hash input (with merge mining bytes etc.)
    seed_hash: bytes         # 32 bytes — drives RandomX cache key
    network_difficulty: int  # full block target as a difficulty
    pool_difficulty: int     # share target (per-miner vardiff'd)
    created_at: float = field(default_factory=time.time)
    template_reserved_offset: int = 0
    template_reserved_size: int = 8


@dataclass
class XmrShare:
    """One accepted share, contributing to a miner's payout weight."""
    miner_id: str
    job_id: str
    nonce: int
    hash_hex: str
    pool_difficulty: int
    block_solved: bool
    received_at: float = field(default_factory=time.time)


@dataclass
class _CacheEntry:
    cache: Cache
    seed_hash: bytes
    created_at: float
    retired_at: Optional[float] = None


# ── job manager ────────────────────────────────────────────────────────

class XmrJobManager:
    """Polls monerod for fresh block templates and pushes them to
    subscribers. Manages RandomX cache rotation across seed_hash boundaries.
    """

    def __init__(
        self,
        client: MoneroDaemonClient,
        pool_wallet_address: str,
        poll_interval: float = 1.0,
        cache_retirement_seconds: float = 120.0,
    ):
        if not pool_wallet_address:
            raise ValueError(
                "pool_wallet_address required — this is the address that "
                "receives the 5% pool fee. See "
                "/etc/animica/secrets/monero-pool-wallet.public.json"
            )
        self._client = client
        self._pool_addr = pool_wallet_address
        self._poll_interval = poll_interval
        self._cache_retirement_seconds = cache_retirement_seconds

        self._current_job: Optional[XmrJob] = None
        self._caches: Dict[bytes, _CacheEntry] = {}
        self._subscribers: List[Callable[[XmrJob], Awaitable[None]]] = []
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def current_job(self) -> Optional[XmrJob]:
        return self._current_job

    def get_cache_for_seed(self, seed_hash: bytes) -> Optional[Cache]:
        entry = self._caches.get(seed_hash)
        return entry.cache if entry else None

    def subscribe(self, callback: Callable[[XmrJob], Awaitable[None]]) -> None:
        """Register a callback that fires whenever a new XmrJob is built."""
        self._subscribers.append(callback)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="xmr-job-manager")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None
        # Clean caches — librandomx Cache.close() releases the underlying
        # ~256 MiB allocation. Important if pool restarts hot.
        for entry in self._caches.values():
            entry.cache.close()
        self._caches.clear()

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                synced = await self._client.is_synced()
                if not synced:
                    _LOG.info("monerod not yet synced, waiting...")
                    await asyncio.sleep(15.0)
                    continue
                await self._refresh_job_once()
                backoff = 1.0
                # Wait with cancellation support
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError:
                    pass
            except Exception as exc:
                _LOG.warning("xmr-job-manager loop error: %s", exc, exc_info=False)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _refresh_job_once(self) -> None:
        template = await self._client.get_block_template(
            self._pool_addr, reserve_size=8
        )
        height = int(template["height"])
        seed_hash_hex = template["seed_hash"]
        seed_hash = bytes.fromhex(seed_hash_hex)
        blockhashing_blob_hex = template["blockhashing_blob"]
        blocktemplate_blob_hex = template["blocktemplate_blob"]
        difficulty = int(template["difficulty"])

        # New template? Compare blockhashing_blob — if unchanged the
        # template is still hot and we don't need to bother miners.
        async with self._lock:
            if (
                self._current_job is not None
                and self._current_job.blockhashing_blob.hex() == blockhashing_blob_hex
            ):
                return

            await self._ensure_cache_for(seed_hash)

            new_job = XmrJob(
                job_id=f"xmr-{height}-{seed_hash_hex[:8]}-{int(time.time() * 1000)}",
                height=height,
                blob=bytes.fromhex(blocktemplate_blob_hex),
                blockhashing_blob=bytes.fromhex(blockhashing_blob_hex),
                seed_hash=seed_hash,
                network_difficulty=difficulty,
                pool_difficulty=max(1000, difficulty // 10000),  # default vardiff-able
                template_reserved_offset=int(template.get("reserved_offset", 0)),
                template_reserved_size=8,
            )
            self._current_job = new_job

        _LOG.info(
            "new xmr job: height=%d diff=%d seed=%s",
            height, difficulty, seed_hash_hex[:16],
        )
        for sub in list(self._subscribers):
            try:
                await sub(new_job)
            except Exception as exc:
                _LOG.warning("xmr subscriber raised: %s", exc, exc_info=False)

    async def _ensure_cache_for(self, seed_hash: bytes) -> None:
        if seed_hash in self._caches:
            return
        flags = recommended_flags() | RANDOMX_FLAG_JIT
        try:
            cache = Cache(seed_hash, flags=flags)
        except Exception:
            # JIT may be unavailable (sealed kernel); fall back to interp.
            _LOG.warning("RandomX JIT unavailable, falling back to interpreter")
            cache = Cache(seed_hash, flags=RANDOMX_FLAG_DEFAULT)
        self._caches[seed_hash] = _CacheEntry(
            cache=cache,
            seed_hash=seed_hash,
            created_at=time.time(),
        )
        self._retire_stale_caches()

    def _retire_stale_caches(self) -> None:
        now = time.time()
        if len(self._caches) <= 2:
            return
        # Mark old caches with a retirement timestamp; remove anything
        # past the retirement window.
        for sh, entry in list(self._caches.items()):
            if entry.retired_at is None and sh != self._current_seed_hash():
                entry.retired_at = now
            if (
                entry.retired_at is not None
                and now - entry.retired_at >= self._cache_retirement_seconds
            ):
                _LOG.info("retiring xmr cache for seed=%s", sh.hex()[:16])
                entry.cache.close()
                del self._caches[sh]

    def _current_seed_hash(self) -> Optional[bytes]:
        return self._current_job.seed_hash if self._current_job else None


# ── share validator ────────────────────────────────────────────────────

@dataclass
class _VMSlot:
    vm: VM
    seed_hash: bytes


class XmrShareValidator:
    """Validates submitted RandomX shares against the active job set.

    Holds a small pool of VMs (one per concurrent validator task). VMs
    are matched to the share's seed_hash; if the active VM is for an
    older seed we swap to the right Cache and recreate the VM.
    """

    def __init__(self, job_manager: XmrJobManager, num_workers: int = 4):
        self._jm = job_manager
        self._num_workers = num_workers
        self._jobs_by_id: Dict[str, XmrJob] = {}
        self._jobs_lock = asyncio.Lock()
        self._vm_sem = asyncio.Semaphore(num_workers)
        self._vms: List[Optional[_VMSlot]] = [None] * num_workers
        self._vm_lock = asyncio.Lock()

    async def remember_job(self, job: XmrJob) -> None:
        async with self._jobs_lock:
            self._jobs_by_id[job.job_id] = job
            # Drop jobs older than 5 minutes — miners that took longer
            # than 5 min to submit are getting stale-rejected anyway.
            cutoff = time.time() - 300
            for jid, j in list(self._jobs_by_id.items()):
                if j.created_at < cutoff:
                    del self._jobs_by_id[jid]

    async def validate(
        self, miner_id: str, job_id: str, nonce_hex: str, result_hex: str
    ) -> Tuple[bool, str, Optional[XmrShare]]:
        async with self._jobs_lock:
            job = self._jobs_by_id.get(job_id)
        if job is None:
            return False, "stale_or_unknown_job", None

        try:
            nonce_bytes = bytes.fromhex(nonce_hex)
            if len(nonce_bytes) != 4:
                return False, "bad_nonce_length", None
            claimed_hash = bytes.fromhex(result_hex)
            if len(claimed_hash) != 32:
                return False, "bad_result_length", None
        except ValueError:
            return False, "bad_hex", None

        # The Monero blockhashing_blob has the nonce at bytes [39:43].
        # We replace those 4 bytes with the miner's nonce and re-hash.
        if len(job.blockhashing_blob) < 43:
            return False, "blob_too_short", None
        blob = bytearray(job.blockhashing_blob)
        blob[39:43] = nonce_bytes
        blob = bytes(blob)

        actual_hash = await self._compute_hash(job.seed_hash, blob)
        if actual_hash != claimed_hash:
            return False, "hash_mismatch", None

        # Convert hash to integer (little-endian as Monero does)
        hash_int = int.from_bytes(actual_hash, "little")
        if hash_int == 0:
            return False, "zero_hash", None

        # Share difficulty = floor(2^256 / hash_int)
        share_difficulty = (1 << 256) // hash_int
        if share_difficulty < job.pool_difficulty:
            return False, "low_difficulty", None

        block_solved = share_difficulty >= job.network_difficulty
        return True, "ok", XmrShare(
            miner_id=miner_id,
            job_id=job_id,
            nonce=int.from_bytes(nonce_bytes, "little"),
            hash_hex=actual_hash.hex(),
            pool_difficulty=int(share_difficulty),
            block_solved=block_solved,
        )

    async def _compute_hash(self, seed_hash: bytes, blob: bytes) -> bytes:
        cache = self._jm.get_cache_for_seed(seed_hash)
        if cache is None:
            raise RuntimeError(
                f"no RandomX cache for seed_hash={seed_hash.hex()[:16]} — "
                "job manager and validator are out of sync"
            )

        # Pick a free VM slot (round-robin via semaphore)
        async with self._vm_sem:
            slot_index = await self._claim_slot(seed_hash, cache)
            try:
                slot = self._vms[slot_index]
                assert slot is not None
                # `vm.hash` does a CPU-bound call; offload to a thread so
                # the asyncio loop doesn't block. RandomX hash is ~3-10 ms
                # so the overhead of asyncio.to_thread is worth it.
                return await asyncio.to_thread(slot.vm.hash, blob)
            finally:
                pass  # slot stays in place; only the semaphore guards reuse

    async def _claim_slot(self, seed_hash: bytes, cache: Cache) -> int:
        async with self._vm_lock:
            # Prefer a slot already on this seed_hash
            for i, slot in enumerate(self._vms):
                if slot is not None and slot.seed_hash == seed_hash:
                    return i
            # Else replace the oldest slot (or the first empty one)
            for i, slot in enumerate(self._vms):
                if slot is None:
                    self._vms[i] = _VMSlot(vm=VM(cache), seed_hash=seed_hash)
                    return i
            # All slots occupied with other seeds — destroy index 0 and
            # rebuild on the current seed. This happens at most once per
            # seed rotation (every 2048 Monero blocks ≈ every 2.8 days).
            self._vms[0].vm.close()
            self._vms[0] = _VMSlot(vm=VM(cache), seed_hash=seed_hash)
            return 0

    def close(self) -> None:
        for slot in self._vms:
            if slot is not None:
                slot.vm.close()


# ── per-miner ledger ───────────────────────────────────────────────────

class XmrShareLedger:
    """In-memory share weight tracker. Persisted to disk by `payouts.py`
    on every block-found event so a pool restart doesn't lose accrued
    miner credit.

    Weight model: each accepted share contributes `share.pool_difficulty`
    units to its miner's accumulator. On block found at network_diff D,
    a miner with weight W out of total T receives:
        (block_reward * 0.95) * (W / T)
    The remaining 5% stays in the pool fee wallet.
    """

    def __init__(self):
        self._weights: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._total: int = 0
        self._block_count: int = 0

    async def record(self, share: XmrShare) -> None:
        async with self._lock:
            self._weights[share.miner_id] = (
                self._weights.get(share.miner_id, 0) + share.pool_difficulty
            )
            self._total += share.pool_difficulty
            if share.block_solved:
                self._block_count += 1

    async def snapshot_and_reset(self) -> Tuple[Dict[str, int], int]:
        async with self._lock:
            snap = dict(self._weights)
            total = self._total
            self._weights.clear()
            self._total = 0
            return snap, total

    async def stats(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "miners": len(self._weights),
                "total_weight": self._total,
                "blocks_found": self._block_count,
                "per_miner_weights_top10": dict(
                    sorted(self._weights.items(), key=lambda kv: -kv[1])[:10]
                ),
            }


# ── module-level convenience ───────────────────────────────────────────

DEFAULT_MONEROD_URL = os.getenv("ANIMICA_POOL_MONEROD_URL", "http://127.0.0.1:18081")


async def make_default_components(
    pool_xmr_address: str,
    monerod_url: str = DEFAULT_MONEROD_URL,
    num_validator_workers: int = 4,
) -> Tuple[MoneroDaemonClient, XmrJobManager, XmrShareValidator, XmrShareLedger]:
    """Factory used by core.py when XMR mining is enabled in config."""
    client = MoneroDaemonClient(url=monerod_url)
    jm = XmrJobManager(client, pool_wallet_address=pool_xmr_address)
    validator = XmrShareValidator(jm, num_workers=num_validator_workers)
    ledger = XmrShareLedger()
    # Re-notify the validator whenever a new job is built
    jm.subscribe(validator.remember_job)
    return client, jm, validator, ledger
