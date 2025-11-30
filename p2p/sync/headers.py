from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import (Iterable, List, Optional, Protocol, Sequence, Tuple,
                    runtime_checkable)

# Local shared knobs/types
from . import (DEFAULT_MAX_IN_FLIGHT, DEFAULT_MAX_REORG_DEPTH,
               DEFAULT_REQUEST_TIMEOUT_SEC, Hash, Height, SyncStats)


@runtime_checkable
class HeaderLike(Protocol):
    """
    Minimal header surface needed by the sync loop.

    Implementations coming from adapters should provide:
      - hash: bytes            (unique identifier of the header)
      - parent_hash: bytes     (link to parent)
    Optional (used for logs/metrics if present):
      - number or height: int  (monotonic height)
    """

    hash: bytes
    parent_hash: bytes

    # Optional height/number is probed via getattr; not required by the Protocol.


@runtime_checkable
class ChainAdapter(Protocol):
    """
    Adapter that connects header sync to the node's persistent header DB & fork-choice.

    A concrete implementation is provided in p2p/deps.py, bridging to core/db/block_db.py
    and consensus/fork_choice if available.
    """

    async def get_head(self) -> Tuple[Hash, Height]: ...

    async def has_header(self, h: Hash) -> bool: ...

    async def get_header(self, h: Hash) -> Optional[HeaderLike]: ...

    async def get_height(self, h: Hash) -> Optional[Height]: ...

    async def put_headers(self, headers: Sequence[HeaderLike]) -> None:
        """Persist a *contiguous* sequence of headers whose parents are already present (or in the sequence)."""
        ...

    async def set_canonical_head(self, h: Hash) -> None:
        """Advance the canonical head pointer to h (assumes best-chain selection already made)."""
        ...

    async def common_ancestor(self, a: Hash, b: Hash, max_back: int) -> Optional[Hash]:
        """Find a & b's common ancestor within max_back steps; None if not found."""
        ...

    async def is_better_tip(
        self, candidate: HeaderLike, current_head: HeaderLike
    ) -> bool:
        """Return True if candidate should become the canonical tip over current_head."""
        ...


@runtime_checkable
class ConsensusView(Protocol):
    """
    Lightweight consensus checks for headers during sync (cheap stateless+schedule validation).
    Keep it fast; full validation happens on block import.
    """

    async def precheck_header(self, header: HeaderLike) -> bool: ...


@runtime_checkable
class HeaderFetcher(Protocol):
    """
    Transport-agnostic "getheaders" fetcher. A concrete implementation should:
      - choose a peer (or many) under the hood,
      - send a GETHEADERS-like request with (locator, stop, limit),
      - return a contiguous list of headers (newest last).
    """

    async def getheaders(
        self,
        locator: Sequence[Hash],
        stop: Optional[Hash],
        limit: int,
        timeout_sec: float,
    ) -> List[HeaderLike]: ...


@dataclass(slots=True)
class HeaderSyncConfig:
    batch_size: int = 128
    max_in_flight: int = DEFAULT_MAX_IN_FLIGHT
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC
    max_reorg_depth: int = DEFAULT_MAX_REORG_DEPTH
    idle_backoff_sec: float = 1.0  # when no headers received
    locator_max_steps: int = 32  # number of entries in the locator (exp backoff)
    sanity_parent_required: bool = True  # require first header's parent to be known


class HeaderSync:
    """
    HeaderSync runs a compact "getheaders/headers" loop:

      1) Build a *locator* from the local canonical head with exponentially
         increasing gaps (Bitcoin-style).
      2) Ask a peer (via HeaderFetcher) for up to N headers after that locator.
      3) Pre-check each header via ConsensusView (schedule/policy roots).
      4) Persist the contiguous tail whose ancestry is known (or fully included).
      5) If the returned branch is better, switch the canonical head.

    Fork handling:
      - If the batch's first header parent is unknown, we tighten the locator
        (walk further back) next round until we intersect.
      - We bound reorgs with `max_reorg_depth`; deeper forks will only be
        adopted once an ancestor within that bound is known.
    """

    def __init__(
        self,
        chain: ChainAdapter,
        fetcher: HeaderFetcher,
        consensus: ConsensusView,
        config: Optional[HeaderSyncConfig] = None,
    ) -> None:
        self.chain = chain
        self.fetcher = fetcher
        self.consensus = consensus
        self.cfg = config or HeaderSyncConfig()
        self._stop = asyncio.Event()
        self.stats = SyncStats(started_at=time.time(), last_progress_at=time.time())

    # ---------------------------
    # Public control surface
    # ---------------------------

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        """
        Single-threaded sync loop. It cooperates with other tasks via await and
        runs until stop() is called.
        """
        while not self._stop.is_set():
            try:
                made_progress = await self._sync_step()
                if not made_progress:
                    await asyncio.sleep(self.cfg.idle_backoff_sec)
            except asyncio.CancelledError:
                raise
            except (
                Exception
            ) as e:  # noqa: BLE001 - we want to keep syncing unless fatal
                # In production, prefer a structured logger (wired in deps).
                print(f"[headers] sync step error: {e!r}")
                self.stats.errors += 1
                await asyncio.sleep(min(2 * self.cfg.idle_backoff_sec, 5.0))

    # ---------------------------
    # Core logic
    # ---------------------------

    async def _sync_step(self) -> bool:
        head_hash, head_height = await self.chain.get_head()

        locator = await self._build_locator(
            head_hash, max_steps=self.cfg.locator_max_steps
        )
        headers = await self.fetcher.getheaders(
            locator=locator,
            stop=None,
            limit=self.cfg.batch_size,
            timeout_sec=self.cfg.request_timeout_sec,
        )

        if not headers:
            return False

        # Sanity: require linkage and known parent for the first header unless explicitly allowed.
        first = headers[0]
        if self.cfg.sanity_parent_required:
            parent_known = await self.chain.has_header(first.parent_hash)
            if not parent_known:
                # No known parent yet—likely mid-fork; tighten by increasing locator depth next time.
                # We still *may* accept if the parent is also within this batch (rare); check contiguous tail below.
                pass

        # Precheck + compute the largest *contiguous* suffix whose ancestry is known or included.
        contiguous: List[HeaderLike] = []
        known_or_batched: set[Hash] = set([h.hash for h in headers])
        for idx, h in enumerate(headers):
            # Validate basic parent linkage
            if idx == 0:
                # Parent is either known locally or appears later in the batch — allow batch-internal linkage.
                if (
                    not await self.chain.has_header(h.parent_hash)
                    and h.parent_hash not in known_or_batched
                ):
                    break
            else:
                # Enforce contiguous linkage inside the batch
                prev = headers[idx - 1]
                if h.parent_hash != prev.hash and not await self.chain.has_header(
                    h.parent_hash
                ):
                    break

            # Lightweight consensus schedule/policy precheck (fast)
            ok = await self.consensus.precheck_header(h)
            if not ok:
                break

            contiguous.append(h)

        if not contiguous:
            return False

        # Enforce reorg bound: if we're switching to a fork, the fork point must be within max_reorg_depth.
        new_tail_first = contiguous[0]
        # If parent is unknown, find common ancestor with current head (bounded).
        if not await self.chain.has_header(new_tail_first.parent_hash):
            # Try to locate a fork point quickly; if not found, delay adoption.
            ancestor = await self.chain.common_ancestor(
                head_hash, new_tail_first.hash, self.cfg.max_reorg_depth
            )
            if ancestor is None:
                # Can't find a shallow common ancestor → wait for more headers / peers.
                return False

        # Persist and maybe advance the canonical head.
        await self.chain.put_headers(contiguous)
        self.stats.headers_fetched += len(contiguous)
        self.stats.last_progress_at = time.time()

        # Decide whether to advance head to the last header in the contiguous suffix.
        last = contiguous[-1]
        current_head_obj = await self.chain.get_header(head_hash)
        if current_head_obj is None:
            # Extremely unlikely (head must exist), but be defensive.
            await self.chain.set_canonical_head(last.hash)
            return True

        if await self.chain.is_better_tip(last, current_head_obj):
            await self.chain.set_canonical_head(last.hash)
            return True

        return True

    # ---------------------------
    # Helpers
    # ---------------------------

    async def _build_locator(self, start: Hash, max_steps: int = 32) -> List[Hash]:
        """
        Build a Bitcoin-like block locator:
          - last 10 headers: step = 1
          - then step *= 2 until we reach genesis or max_steps
        The local adapter supplies ancestry via get_header().
        """
        locator: List[Hash] = []
        step = 1
        n_filled = 0
        cursor: Optional[Hash] = start

        while cursor is not None and n_filled < max_steps:
            locator.append(cursor)
            n_filled += 1

            if n_filled < 10:
                step = 1
            else:
                step *= 2

            cursor = await self._walk_back(cursor, step)

            # Stop if we reached genesis (no further parent).
            if cursor is None:
                break

        return locator

    async def _walk_back(self, h: Hash, steps: int) -> Optional[Hash]:
        """
        Walk back `steps` parents from header `h`. Returns None if a parent is missing
        (which shouldn't happen for canonical ancestry, but we guard defensively).
        """
        cur = await self.chain.get_header(h)
        if cur is None:
            return None
        for _ in range(steps):
            parent = getattr(cur, "parent_hash", None)
            if not parent:
                return None
            cur = await self.chain.get_header(parent)
            if cur is None:
                # We know the hash of the parent, but not the header body (e.g., pruned or not yet persisted).
                return parent if _ == steps - 1 else None
        return getattr(cur, "hash", None)


# ---------------------------
# Tiny utility (optional)
# ---------------------------


def height_of(h: HeaderLike) -> Optional[int]:
    """Best-effort read of header height for logging/metrics."""
    v = getattr(h, "height", None)
    if isinstance(v, int):
        return v
    v = getattr(h, "number", None)
    if isinstance(v, int):
        return v
    return None
