"""
Tests for core_p2p sync improvements: timeout handling and continuous sync checking.
"""
import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import pytest

from p2p.core_p2p.sync_manager import SyncManager

# Bitcoin-style header size (80 bytes)
HEADER_SIZE = 80
# Padding size for test headers (32-byte hash + 48 bytes padding = 80 total)
TEST_HEADER_PADDING = 48


@dataclass
class FakeChain:
    """Minimal chain implementation for testing."""
    
    headers: List[bytes] = field(default_factory=list)
    blocks: Dict[bytes, bytes] = field(default_factory=dict)
    
    def best_header(self) -> bytes:
        return self.headers[-1] if self.headers else b""
    
    def locator(self) -> Sequence[bytes]:
        hashes = [hashlib.sha256(h).digest() for h in self.headers]
        return list(reversed(hashes[-10:])) or [b"\x00" * 32]
    
    def process_headers(self, headers: Sequence[bytes]) -> None:
        for header in headers:
            if header not in self.headers:
                self.headers.append(header)
    
    def headers_since(self, locator: Sequence[bytes], stop_hash: bytes) -> Sequence[bytes]:
        return []
    
    def get_block(self, block_hash: bytes) -> Optional[bytes]:
        return self.blocks.get(block_hash)
    
    def get_tx(self, tx_hash: bytes) -> Optional[bytes]:
        return None
    
    def process_block(self, block: bytes) -> None:
        header = block[:HEADER_SIZE]
        block_hash = hashlib.sha256(header).digest()
        self.blocks[block_hash] = block
    
    def process_tx(self, tx: bytes) -> None:
        pass


def test_sync_manager_timeout_basic():
    """Test that timeout handling works for basic case."""
    chain = FakeChain()
    sm = SyncManager(chain, request_timeout=0.1)  # 100ms timeout for fast test
    
    # Queue some blocks
    block_hashes = [hashlib.sha256(str(i).encode()).digest() for i in range(5)]
    sm.queue_blocks(block_hashes)
    
    # Request first batch
    batch = sm.next_block_batch(limit=3)
    assert len(batch) == 3
    assert len(sm.inflight_blocks) == 3
    
    # Initially no timeouts
    timed_out = sm.timeout_stale_requests()
    assert timed_out == 0
    
    # Wait for timeout
    time.sleep(0.15)
    
    # Now should timeout
    timed_out = sm.timeout_stale_requests()
    assert timed_out == 3
    
    # Blocks should be re-queued
    assert len(sm.pending_blocks) == 5  # 3 timed out + 2 never requested
    assert len(sm.inflight_blocks) == 0


def test_sync_manager_timeout_partial():
    """Test that only timed-out requests are affected."""
    chain = FakeChain()
    sm = SyncManager(chain, request_timeout=0.2)  # 200ms timeout
    
    # Queue and request 3 blocks
    block_hashes = [hashlib.sha256(str(i).encode()).digest() for i in range(3)]
    sm.queue_blocks(block_hashes)
    batch = sm.next_block_batch(limit=3)
    assert len(sm.inflight_blocks) == 3
    
    # Complete first block immediately
    sm.complete_inflight(batch[0])
    assert len(sm.inflight_blocks) == 2
    
    # Wait for timeout
    time.sleep(0.25)
    
    # Only the 2 remaining should timeout
    timed_out = sm.timeout_stale_requests()
    assert timed_out == 2
    
    # Should have 2 blocks re-queued
    assert len(sm.pending_blocks) == 2
    assert len(sm.inflight_blocks) == 0


def test_sync_manager_no_duplicate_requeue():
    """Test that timed-out blocks aren't duplicated in queue."""
    chain = FakeChain()
    sm = SyncManager(chain, request_timeout=0.1)
    
    block_hash = hashlib.sha256(b"test").digest()
    sm.queue_blocks([block_hash])
    
    batch = sm.next_block_batch(limit=1)
    assert len(batch) == 1
    
    # Timeout
    time.sleep(0.15)
    timed_out = sm.timeout_stale_requests()
    assert timed_out == 1
    
    # Should be back in queue exactly once
    assert len(sm.pending_blocks) == 1
    assert len(sm.pending_set) == 1
    
    # Timeout again without requesting
    time.sleep(0.15)
    timed_out = sm.timeout_stale_requests()
    assert timed_out == 0  # Nothing was inflight
    
    # Still only one copy in queue
    assert len(sm.pending_blocks) == 1


def test_sync_manager_request_timeout_config():
    """Test that request_timeout can be configured."""
    chain = FakeChain()
    
    # Default timeout
    sm1 = SyncManager(chain)
    assert sm1.request_timeout == 30.0
    
    # Custom timeout
    sm2 = SyncManager(chain, request_timeout=60.0)
    assert sm2.request_timeout == 60.0


def test_sync_manager_build_getheaders():
    """Test that getheaders message is built correctly."""
    chain = FakeChain()
    chain.headers = [b"header1" * 10, b"header2" * 10]
    
    sm = SyncManager(chain)
    msg = sm.build_getheaders()
    
    # Should include locator from chain
    assert len(msg.locator_hashes) > 0
    # Stop hash should be null (request all)
    assert msg.stop_hash == b"\x00" * 32


@pytest.mark.asyncio
async def test_sync_timeout_integration():
    """Integration test: simulate sync with timeouts."""
    chain = FakeChain()
    sm = SyncManager(chain, request_timeout=0.2)
    
    # Simulate receiving headers (32-byte hash + 48 bytes padding = 80-byte Bitcoin-style header)
    headers = [hashlib.sha256(str(i).encode()).digest() + b"\x00" * TEST_HEADER_PADDING for i in range(10)]
    sm.receive_headers(headers)
    
    # Queue blocks based on headers
    block_hashes = [hashlib.sha256(h).digest() for h in headers]
    sm.queue_blocks(block_hashes)
    
    # Start requesting
    batch1 = sm.next_block_batch(limit=5)
    assert len(batch1) == 5
    assert len(sm.inflight_blocks) == 5
    assert len(sm.pending_blocks) == 5
    
    # Simulate some blocks arriving
    sm.complete_inflight(batch1[0])
    sm.complete_inflight(batch1[1])
    assert len(sm.inflight_blocks) == 3
    
    # Request more
    batch2 = sm.next_block_batch(limit=5)
    assert len(batch2) == 5
    assert len(sm.inflight_blocks) == 8
    
    # Wait for timeout
    await asyncio.sleep(0.25)
    
    # Check timeouts
    timed_out = sm.timeout_stale_requests()
    assert timed_out == 8  # All 8 inflight should timeout
    
    # All should be back in queue
    assert len(sm.pending_blocks) == 8
    assert len(sm.inflight_blocks) == 0
    
    # Can request again
    batch3 = sm.next_block_batch(limit=10)
    assert len(batch3) == 8  # All 8 timed-out blocks


if __name__ == "__main__":
    # Run basic tests
    test_sync_manager_timeout_basic()
    print("✓ test_sync_manager_timeout_basic")
    
    test_sync_manager_timeout_partial()
    print("✓ test_sync_manager_timeout_partial")
    
    test_sync_manager_no_duplicate_requeue()
    print("✓ test_sync_manager_no_duplicate_requeue")
    
    test_sync_manager_request_timeout_config()
    print("✓ test_sync_manager_request_timeout_config")
    
    test_sync_manager_build_getheaders()
    print("✓ test_sync_manager_build_getheaders")
    
    print("\nAll tests passed!")
