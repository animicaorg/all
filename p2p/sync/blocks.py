from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import (Dict, Iterable, List, Optional, Protocol, Sequence, Tuple,
                    runtime_checkable)

# Shared knobs & types expected to be exported by p2p/sync/__init__.py (or similar).
# We import symbol names used in headers.py for consistency.
from . import DEFAULT_MAX_IN_FLIGHT, DEFAULT_REQUEST_TIMEOUT_SEC, Hash


@runtime_checkable
class BlockLike(Protocol):
    """
    Minimal surface we need for body sync & reassembly.
      - hash: bytes
      - parent_hash: bytes
    Optional (for logs/metrics):
      - number or height: int
    """

    hash: bytes
    parent_hash: bytes


@runtime_checkable
class ConsensusView(Protocol):
    """
    Lightweight, fast block checks suitable for the sync hot-path (e.g., schedule/policy roots).
    Full validation (state transition) is out-of-scope for the P2P sync stage.
    """

    async def precheck_block(self, block: BlockLike) -> bool: ...


@runtime_checkable
class ChainAdapter(Protocol):
    """
    Adapter to the node's persistent storage and fork-choice for block bodies.
    A concrete implementation is provided in p2p/deps.py bridging to core/.
    """

    async def has_block(self, h: Hash) -> bool: ...
    async def has_header(self, h: Hash) -> bool: ...
    async def get_head(self) -> Tuple[Hash, int]: ...
    async def put_blocks(self, blocks: Sequence[BlockLike]) -> None:
        """Persist a *contiguous* sequence whose parents are present or included."""
        ...

    async def get_block(self, h: Hash) -> Optional[BlockLike]: ...


@runtime_checkable
class BlockFetcher(Protocol):
    """
    Transport-agnostic fetcher. Implementations choose peers, perform GETDATA/blocks,
    handle censorship/timeouts, and return the full block.
    """

    async def get_block(self, h: Hash, timeout_sec: float) -> Optional[BlockLike]: ...


@dataclass(slots=True)
class BlocksSyncConfig:
    max_parallel: int = 4096  # Ultra-increased from 2048 to 4096 for extreme parallel sync (thousands of blocks/sec)
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC
    max_retries: int = 3
    jitter_frac: float = 0.15
    idle_backoff_sec: float = 0.001  # Further reduced from 0.01 to 0.001 for instant response (1ms)
    sanity_parent_required: bool = (
        True  # reassembly requires known parent for the first commit
    )
    # Phase 3: Error recovery constants
    backoff_cap_sec: float = 4.0  # Cap exponential backoff at 4s for faster recovery
    backoff_multiplier: float = 1.4  # Multiply timeout by this on retry (reduced from 1.6x)
    # CRITICAL FIX: Add timeout for buffered blocks waiting for parent
    buffered_block_timeout_sec: float = 300.0  # 5 minutes max wait for missing parent
    buffered_block_cleanup_interval_sec: float = 30.0  # Check every 30s for stale blocks
    max_timeout_sec: float = 30.0  # Hard limit to prevent extreme timeout growth


@dataclass(slots=True)
class BlocksSyncStats:
    started_at: float = field(default_factory=time.time)
    last_progress_at: float = field(default_factory=time.time)
    fetched: int = 0
    committed: int = 0
    timeouts: int = 0
    errors: int = 0
    retries: int = 0
    misses: int = 0  # fetcher returned None


class BlocksDownloader:
    """
    Download & reassemble a known ordered segment of block hashes (oldest→newest).
    
    Enhanced with better logging and idle handling for P2P rewrite.

    Typical usage:
      1) headers sync yields a contiguous header segment that's chosen as best.
      2) planner passes the *ordered* list of block hashes for that segment.
      3) BlocksDownloader fetches in parallel (out-of-order) and commits in-order
         as soon as parents are present (either already persisted, or within the segment).

    Reassembly rule:
      - Maintain a cursor `next_idx` into `order`.
      - As soon as the block for `order[next_idx]` is present in the buffer AND
        its parent is present (either in-store or equals order[next_idx-1] just committed),
        we can commit it (and subsequent contiguous ones).

    Safety:
      - A fast `consensus.precheck_block` filter is applied before persisting.
      - First commit optionally requires known parent in DB (configurable).
    """

    def __init__(
        self,
        chain: ChainAdapter,
        fetcher: BlockFetcher,
        consensus: ConsensusView,
        config: Optional[BlocksSyncConfig] = None,
    ) -> None:
        self.chain = chain
        self.fetcher = fetcher
        self.consensus = consensus
        self.cfg = config or BlocksSyncConfig()
        self.stats = BlocksSyncStats()
        self._log = logging.getLogger("animica.p2p.sync.blocks")

    async def download_and_apply(self, order: Sequence[Hash]) -> int:
        """
        Fetch and commit the blocks for `order` (oldest→newest).
        Returns the number of blocks committed.
        
        Enhanced with logging for P2P rewrite.
        """
        if not order:
            return 0

        # Enhanced logging with genesis detection
        first_height = None
        try:
            first_header = await self.chain.get_header(order[0])
            if first_header:
                first_height = getattr(first_header, 'height', None) or getattr(first_header, 'number', None)
        except Exception:
            pass
        
        if first_height == 0:
            self._log.info(f"Starting block download from GENESIS for {len(order)} blocks")
        else:
            self._log.info(f"Starting block download for {len(order)} blocks (first at height {first_height})")
        
        # Fast path: drop any prefix that is already persisted.
        next_idx = 0
        while next_idx < len(order) and await self.chain.has_block(order[next_idx]):
            next_idx += 1

        if next_idx >= len(order):
            self._log.debug(f"All {len(order)} blocks already synced")
            return 0  # already synced
        
        blocks_to_fetch = len(order) - next_idx
        self._log.info(f"Need to fetch {blocks_to_fetch} blocks (skipped {next_idx} already present)")

        # Concurrency control.
        sem = asyncio.Semaphore(max(1, self.cfg.max_parallel))
        in_flight: Dict[Hash, asyncio.Task[Optional[BlockLike]]] = {}
        buffer: Dict[Hash, BlockLike] = {}
        # CRITICAL FIX: Track when each block was buffered to detect stalls
        buffer_timestamps: Dict[Hash, float] = {}

        async def fetch_one(h: Hash) -> Optional[BlockLike]:
            timeout = self.cfg.request_timeout_sec
            for attempt in range(self.cfg.max_retries + 1):
                try:
                    async with asyncio.timeout(timeout):
                        blk = await self.fetcher.get_block(h, timeout_sec=timeout)
                    if blk is None:
                        # Peer(s) failed to provide; mark as miss and stop retrying immediately.
                        self.stats.misses += 1
                        self._log.warning(f"Block fetch miss for {h.hex()[:16]}...")
                        return None
                    self._log.debug(f"Successfully fetched block {h.hex()[:16]}...")
                    return blk
                except asyncio.TimeoutError:
                    self.stats.timeouts += 1
                    self.stats.retries += 1
                    self._log.warning(
                        f"Block fetch timeout for {h.hex()[:16]}... "
                        f"(attempt {attempt + 1}/{self.cfg.max_retries + 1})"
                    )
                    # Phase 3: Improved exponential backoff with better cap
                    # Instead of unbounded growth to 6s+, use bounded exponential backoff
                    base = min(self.cfg.backoff_cap_sec, timeout * self.cfg.backoff_multiplier)
                    timeout = base * (
                        1.0 + (random.random() - 0.5) * 2 * self.cfg.jitter_frac
                    )
                    timeout = min(timeout, self.cfg.max_timeout_sec)  # Hard cap
                except (ConnectionError, OSError) as e:
                    # Transient network errors - retry with short backoff
                    self.stats.errors += 1
                    self.stats.retries += 1
                    self._log.warning(
                        f"Network error fetching block {h.hex()[:16]}...: {e.__class__.__name__} "
                        f"(attempt {attempt + 1}/{self.cfg.max_retries + 1})"
                    )
                    # Always sleep on network errors (even last attempt) for consistent behavior
                    await asyncio.sleep(0.002)  # 2ms backoff for network errors
                except Exception as e:
                    # Other errors (validation, parse, etc.) - log and retry with backoff
                    self.stats.errors += 1
                    self.stats.retries += 1
                    self._log.error(
                        f"Block fetch error for {h.hex()[:16]}...: {e.__class__.__name__}: {e}"
                    )
                    # Reduced from 0.05s to 0.005s (5ms) for faster error recovery
                    await asyncio.sleep(0.005)
            self._log.error(f"Failed to fetch block {h.hex()[:16]}... after {self.cfg.max_retries + 1} attempts")
            return None

        async def schedule_until_full() -> None:
            nonlocal next_idx
            # Fill the window with yet-unfetched targets.
            while len(in_flight) < self.cfg.max_parallel:
                # Find the next hash not already fetched/in-flight.
                # We bias towards hashes close to next_idx to help reassembly progress,
                # but allow the whole tail to fill the window.
                target_idx = self._next_want_index(order, next_idx, in_flight, buffer)
                if target_idx is None:
                    break
                h = order[target_idx]
                await sem.acquire()
                in_flight[h] = asyncio.create_task(fetch_one(h))
                in_flight[h].add_done_callback(lambda _t: sem.release())

        # Initial fill
        await schedule_until_full()

        committed_this_round = 0

        while next_idx < len(order) and (in_flight or buffer):
            # Drain any finished fetch
            if in_flight:
                done, _pending = await asyncio.wait(
                    in_flight.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Move finished into buffer
                finished_hashes: List[Hash] = []
                for h, task in list(in_flight.items()):
                    if task in done:
                        finished_hashes.append(h)
                        try:
                            blk = task.result()
                        except Exception:
                            blk = None
                            self.stats.errors += 1
                        if blk is not None:
                            buffer[h] = blk
                            buffer_timestamps[h] = time.time()  # CRITICAL FIX: Track buffer time
                            self.stats.fetched += 1
                        # Remove from in_flight
                        del in_flight[h]

                # Try to refill the window
                await schedule_until_full()
            else:
                # Nothing in-flight (e.g., all misses); break to avoid infinite loop.
                break

            # Try to commit contiguous prefix from next_idx forward.
            committed_now = await self._commit_ready_prefix(order, buffer, next_idx)
            next_idx += committed_now
            committed_this_round += committed_now
            if committed_now > 0:
                self.stats.committed += committed_now
                self.stats.last_progress_at = time.time()
                # CRITICAL FIX: Clean up timestamps for committed blocks
                for i in range(next_idx - committed_now, next_idx):
                    if i < len(order):
                        buffer_timestamps.pop(order[i], None)
            
            # CRITICAL FIX: Check for stale buffered blocks and skip them to prevent deadlock
            now = time.time()
            stale_blocks = []
            for h, timestamp in list(buffer_timestamps.items()):
                age = now - timestamp
                if age > self.cfg.buffered_block_timeout_sec:
                    stale_blocks.append(h)
            
            if stale_blocks:
                self._log.warning(
                    f"Found {len(stale_blocks)} stale buffered blocks (waiting >{self.cfg.buffered_block_timeout_sec}s), "
                    f"skipping to prevent deadlock"
                )
                for h in stale_blocks:
                    buffer.pop(h, None)
                    buffer_timestamps.pop(h, None)
                    # Try to move next_idx forward if this was blocking us
                    while next_idx < len(order) and order[next_idx] in stale_blocks:
                        next_idx += 1
                        self._log.warning(f"Skipped stale block at index {next_idx - 1}")

        return committed_this_round

    # ---------------------------
    # Helpers
    # ---------------------------

    def _next_want_index(
        self,
        order: Sequence[Hash],
        next_idx: int,
        in_flight: Dict[Hash, asyncio.Task[Optional[BlockLike]]],
        buffer: Dict[Hash, BlockLike],
    ) -> Optional[int]:
        """
        Choose the next index to fetch with optimized search:
          - prefer a small band near `next_idx` to encourage quick reassembly,
          - skip ones already in buffer or in-flight.
        Optimized to reduce O(n²) behavior with efficient set lookups.
        """
        window = max(self.cfg.max_parallel * 2, 8)
        lo = next_idx
        hi = min(len(order), next_idx + window)
        # Primary window search - O(window) with O(1) set lookups
        for i in range(lo, hi):
            h = order[i]
            if h not in in_flight and h not in buffer:
                return i
        # If the near band is saturated, search the tail sparsely.
        # This is a fallback case that should be rare with proper window sizing.
        for i in range(hi, len(order)):
            h = order[i]
            if h not in in_flight and h not in buffer:
                return i
        return None

    async def _commit_ready_prefix(
        self,
        order: Sequence[Hash],
        buffer: Dict[Hash, BlockLike],
        next_idx: int,
    ) -> int:
        """
        From `order[next_idx:]`, find the longest contiguous prefix whose blocks are
        available in `buffer` and whose parent linkage holds with either:
          - already-persisted parent (for the very first to commit), or
          - the immediately previous block in the prefix.
        Persist that prefix via chain.put_blocks and evict from buffer.

        Returns the count committed.
        """
        if next_idx >= len(order):
            return 0

        # Check the first candidate's parent if required.
        first_hash = order[next_idx]
        first_blk = buffer.get(first_hash)
        if first_blk is None:
            return 0

        # GENESIS FIX: Check if this is the genesis block (height 0)
        first_height = getattr(first_blk, 'height', None) or getattr(first_blk, 'number', None)
        is_genesis = first_height == 0
        
        if self.cfg.sanity_parent_required and not is_genesis:
            # Parent must be present in DB for the very first in this batch (except genesis).
            if not await self.chain.has_header(first_blk.parent_hash):
                parent_hex = first_blk.parent_hash.hex()[:16] if first_blk.parent_hash else "None"
                self._log.warning(
                    f"Cannot commit block at index {next_idx} (height={first_height}): "
                    f"parent {parent_hex}... not in DB yet. Block will timeout if parent doesn't arrive."
                )
                return 0
        elif is_genesis:
            self._log.info("Processing genesis block (height 0) for commit, skipping parent check")

        # Grow the prefix
        contiguous: List[BlockLike] = [first_blk]
        # Validate lightweight consensus for the first
        if not await self.consensus.precheck_block(first_blk):
            # Drop this bad block from buffer so we don't spin.
            buffer.pop(first_hash, None)
            return 0

        # Extend while the next block is present and links to the previous.
        idx = next_idx + 1
        while idx < len(order):
            h = order[idx]
            blk = buffer.get(h)
            if blk is None:
                break
            prev = contiguous[-1]
            if blk.parent_hash != getattr(prev, "hash", None):
                break
            if not await self.consensus.precheck_block(blk):
                # Bad block interrupts; drop it and stop extending.
                buffer.pop(h, None)
                break
            contiguous.append(blk)
            idx += 1

        # Persist the ready prefix and evict from buffer.
        await self.chain.put_blocks(contiguous)
        for b in contiguous:
            buffer.pop(getattr(b, "hash"), None)

        return len(contiguous)


# Optional utility: a simple "run_forever" consumer that downloads whatever a planner yields.


@runtime_checkable
class BlocksPlanner(Protocol):
    """
    Provides ordered segments of block hashes to download.
    For example, a planner might read the best header chain and emit missing bodies in chunks.
    """

    async def next_segment(self) -> Optional[Sequence[Hash]]:
        """
        Returns an *ordered* sequence (oldest→newest) of block hashes to fetch and commit,
        or None if there is currently nothing to do.
        """
        ...


class BlocksSyncService:
    """
    Thin orchestrator that repeatedly asks a planner for the next ordered segment,
    then uses BlocksDownloader to fetch+commit that segment.
    """

    def __init__(
        self,
        chain: ChainAdapter,
        fetcher: BlockFetcher,
        consensus: ConsensusView,
        planner: BlocksPlanner,
        config: Optional[BlocksSyncConfig] = None,
    ) -> None:
        self.downloader = BlocksDownloader(chain, fetcher, consensus, config)
        self.planner = planner
        self._stop = asyncio.Event()
        self.cfg = self.downloader.cfg

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            seg = await self.planner.next_segment()
            if not seg:
                await asyncio.sleep(self.cfg.idle_backoff_sec)
                continue
            try:
                committed = await self.downloader.download_and_apply(seg)
                # In a real app, we'd log with structured logger.
                if committed:
                    print(
                        f"[blocks] committed {committed} from a segment of {len(seg)}"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[blocks] segment sync error: {e!r}")
                await asyncio.sleep(min(2 * self.cfg.idle_backoff_sec, 2.0))

# Alias for backward compatibility and consistency with __init__.py exports
BlockSync = BlocksSyncService
