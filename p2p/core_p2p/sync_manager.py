from __future__ import annotations

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
    max_inflight: int = 64

    def build_getheaders(self) -> GetHeadersMessage:
        return GetHeadersMessage(locator_hashes=self.chain.locator(), stop_hash=b"\x00" * 32)

    def receive_headers(self, headers: Sequence[bytes]) -> None:
        if headers:
            self.chain.process_headers(headers)

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
