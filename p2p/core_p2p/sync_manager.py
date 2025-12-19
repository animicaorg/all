from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Protocol, Sequence

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
    max_inflight: int = 16

    def build_getheaders(self) -> GetHeadersMessage:
        return GetHeadersMessage(locator_hashes=self.chain.locator(), stop_hash=b"\x00" * 32)

    def receive_headers(self, headers: Sequence[bytes]) -> None:
        if headers:
            self.chain.process_headers(headers)

    def add_inflight(self, block_hash: bytes) -> bool:
        if block_hash in self.inflight_blocks:
            return False
        if len(self.inflight_blocks) >= self.max_inflight:
            return False
        self.inflight_blocks[block_hash] = time.time()
        return True

    def complete_inflight(self, block_hash: bytes) -> None:
        self.inflight_blocks.pop(block_hash, None)
