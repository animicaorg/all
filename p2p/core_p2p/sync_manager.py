from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, Optional, Protocol, Sequence, Set

from .protocol import GetHeadersMessage, HeadersMessage


class ChainAdapter(Protocol):
    def best_header(self) -> bytes: ...
    def locator(self) -> Sequence[bytes]: ...
    def process_headers(self, headers: Sequence[bytes]) -> None: ...
    def headers_since(self, locator: Sequence[bytes], stop_hash: bytes) -> Sequence[bytes]: ...
    def get_block(self, block_hash: bytes) -> Optional[bytes]: ...
    def get_tx(self, tx_hash: bytes) -> Optional[bytes]: ...
    def process_block(self, block: bytes) -> None: ...
    def process_tx(self, tx: bytes) -> None: ...


@dataclass
class SyncManager:
    chain: ChainAdapter
    inflight_blocks: Dict[bytes, float] = field(default_factory=dict)
    pending_blocks: Deque[bytes] = field(default_factory=deque)
    pending_set: Set[bytes] = field(default_factory=set)
    max_inflight: int = 4096  # Massively increased to 4096 for 10,000+ blocks/minute (166 blocks/sec)
    last_headers_request: float = field(default_factory=lambda: 0.0)
    last_progress: float = field(default_factory=time.time)
    last_peer_id: Optional[str] = None
    last_stall_log: float = field(default_factory=lambda: 0.0)
    last_tip_height: int = 0
    header_request_interval: float = field(
        default_factory=lambda: float(os.getenv("ANIMICA_P2P_HEADERS_INTERVAL_SEC", "3") or 3)
    )
    inflight_timeout: float = field(
        default_factory=lambda: float(os.getenv("ANIMICA_P2P_INFLIGHT_TIMEOUT_SEC", "30") or 30)
    )
    stall_timeout: float = field(
        default_factory=lambda: float(os.getenv("ANIMICA_P2P_SYNC_STALL_SEC", "15") or 15)
    )
    stall_log_interval: float = field(
        default_factory=lambda: float(os.getenv("ANIMICA_P2P_SYNC_STALL_LOG_SEC", "10") or 10)
    )

    def build_getheaders(self) -> GetHeadersMessage:
        return GetHeadersMessage(locator_hashes=self.chain.locator(), stop_hash=b"\x00" * 32)

    def receive_headers(self, headers: Sequence[bytes]) -> None:
        if headers:
            self.chain.process_headers(headers)
            self.mark_progress()

    def queue_blocks(self, block_hashes: Iterable[bytes]) -> int:
        queued = 0
        for block_hash in block_hashes:
            if block_hash in self.inflight_blocks or block_hash in self.pending_set:
                continue
            self.pending_blocks.append(block_hash)
            self.pending_set.add(block_hash)
            queued += 1
        return queued

    def next_block_batch(self, limit: int) -> list[bytes]:
        batch: list[bytes] = []
        while (
            self.pending_blocks
            and len(self.inflight_blocks) < self.max_inflight
            and len(batch) < limit
        ):
            block_hash = self.pending_blocks.popleft()
            self.pending_set.discard(block_hash)
            if block_hash in self.inflight_blocks:
                continue
            if not self.add_inflight(block_hash):
                continue
            batch.append(block_hash)
        return batch

    def add_inflight(self, block_hash: bytes) -> bool:
        if block_hash in self.inflight_blocks:
            return False
        if len(self.inflight_blocks) >= self.max_inflight:
            return False
        self.inflight_blocks[block_hash] = time.time()
        return True

    def complete_inflight(self, block_hash: bytes) -> None:
        self.inflight_blocks.pop(block_hash, None)
        self.mark_progress()

    def mark_progress(self) -> None:
        self.last_progress = time.time()

    def mark_headers_request(self, peer_id: Optional[str]) -> None:
        self.last_headers_request = time.time()
        if peer_id is not None:
            self.last_peer_id = peer_id

    def record_tip(self, tip_height: int) -> None:
        if tip_height > self.last_tip_height:
            self.last_tip_height = tip_height

    def expire_inflight(self, now: float) -> int:
        if not self.inflight_blocks:
            return 0
        stale = [h for h, ts in self.inflight_blocks.items() if now - ts > self.inflight_timeout]
        for block_hash in stale:
            self.inflight_blocks.pop(block_hash, None)
            if block_hash not in self.pending_set:
                self.pending_blocks.appendleft(block_hash)
                self.pending_set.add(block_hash)
        return len(stale)

    def should_log_stall(self, now: float) -> bool:
        return now - self.last_stall_log >= self.stall_log_interval

    def mark_stall_logged(self, now: float) -> None:
        self.last_stall_log = now
