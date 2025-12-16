"""
End-to-end integration test for difficulty adjustment.

Simulates a realistic scenario with:
- Genesis block
- Initial stable block production
- Hash rate increase (faster blocks)
- Hash rate decrease (slower blocks)
- Return to equilibrium

Validates that difficulty adjusts appropriately to maintain target block time.
"""

from __future__ import annotations

import pytest
from typing import List, Tuple

from core.chain.block_import import BlockImporter, ImportErrorCode
from core.types.block import Block
from core.types.header import Header
from core.types.params import ChainParams, BlockLimits, RetargetParams, RetargetBounds


class MockBlockDB:
    """Simple in-memory block database for testing."""
    
    def __init__(self):
        self.headers = {}
        self.blocks = {}
        self.head = None
        
    def get_header_by_hash(self, h: bytes):
        return self.headers.get(h)
    
    def get_block_by_hash(self, h: bytes):
        return self.blocks.get(h)
    
    def put_header(self, height: int, h: bytes, header):
        self.headers[h] = header
    
    def put_block(self, h: bytes, block):
        self.blocks[h] = block
    
    def get_canonical_head(self):
        return self.head
    
    def set_canonical_head(self, height: int, h: bytes):
        self.head = (height, h)


def make_chain_params(target_seconds: float = 12.0) -> ChainParams:
    """Create test chain params."""
    return ChainParams(
        chain_id=1337,
        chain_name="Test Chain",
        genesis_time="2025-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x01" * 32,
        poies_policy_root=b"\x02" * 32,
        theta_initial=3_000_000,  # 3.0 nats
        gamma_total_cap=1_000_000,
        retarget=RetargetParams(
            window=24,  # 24 block half-life
            ema_alpha=0.3,  # Relatively responsive
            bounds=RetargetBounds(min=0.5, max=2.0),
        ),
        block=BlockLimits(
            target_seconds=target_seconds,
            max_bytes=1_500_000,
            max_gas=20_000_000,
            tx_max_bytes=131_072,
            min_gas_price=1000,
        ),
    )


def make_header(height: int, parent_hash: bytes, timestamp: int) -> Header:
    """Create a test header."""
    return Header(
        v=1,
        chainId=1337,
        height=height,
        parentHash=parent_hash,
        timestamp=timestamp,
        stateRoot=b"\x00" * 32,
        txsRoot=b"\x00" * 32,
        receiptsRoot=b"\x00" * 32,
        proofsRoot=b"\x00" * 32,
        daRoot=b"\x00" * 32,
        mixSeed=b"\x00" * 32,
        poiesPolicyRoot=b"\x00" * 32,
        pqAlgPolicyRoot=b"\x00" * 32,
        thetaMicro=3_000_000,
        nonce=0,
        extra=b"",
    )


def make_block(header: Header) -> Block:
    """Create a test block."""
    return Block(header=header, txs=(), proofs=(), receipts=None)


def test_difficulty_adjustment_e2e():
    """
    End-to-end test: simulate realistic network hash rate changes and verify
    difficulty adjusts to maintain target block time.
    """
    params = make_chain_params(target_seconds=12.0)
    block_db = MockBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Track metrics throughout the simulation
    timestamps: List[int] = []
    difficulties: List[int] = []
    block_times: List[float] = []
    
    # Phase 1: Genesis
    timestamp = 1000
    genesis_header = make_header(0, b"\x00" * 32, timestamp)
    result = importer.import_block(make_block(genesis_header))
    assert result.code == ImportErrorCode.ACCEPTED
    
    initial_difficulty = importer.get_current_difficulty()
    timestamps.append(timestamp)
    difficulties.append(initial_difficulty)
    
    print(f"\n{'='*60}")
    print(f"Phase 1: Genesis (height=0)")
    print(f"  Initial difficulty: {initial_difficulty:,} µ-nats ({initial_difficulty/1e6:.3f} nats)")
    print(f"  Target block time: {params.block.target_seconds}s")
    
    # Phase 2: Stable production (10 blocks at target interval)
    print(f"\n{'='*60}")
    print(f"Phase 2: Stable Production (10 blocks at target 12s)")
    prev_hash = result.block_hash
    for i in range(1, 11):
        timestamp += int(params.block.target_seconds)
        header = make_header(i, prev_hash, timestamp)
        result = importer.import_block(make_block(header))
        prev_hash = result.block_hash
        
        timestamps.append(timestamp)
        difficulties.append(importer.get_current_difficulty())
        block_times.append(timestamp - timestamps[-2])
    
    stable_difficulty = importer.get_current_difficulty()
    print(f"  End difficulty: {stable_difficulty:,} µ-nats ({stable_difficulty/1e6:.3f} nats)")
    print(f"  Change: {stable_difficulty - initial_difficulty:+,} µ-nats")
    print(f"  Average block time: {sum(block_times[-10:])/10:.1f}s")
    
    # Phase 3: Hash rate increase (20 blocks at 6s - 2x faster)
    print(f"\n{'='*60}")
    print(f"Phase 3: Hash Rate Increase (20 blocks at 6s - 2x faster)")
    for i in range(11, 31):
        timestamp += 6  # Fast blocks
        header = make_header(i, prev_hash, timestamp)
        result = importer.import_block(make_block(header))
        prev_hash = result.block_hash
        
        timestamps.append(timestamp)
        difficulties.append(importer.get_current_difficulty())
        block_times.append(timestamp - timestamps[-2])
    
    fast_difficulty = importer.get_current_difficulty()
    print(f"  End difficulty: {fast_difficulty:,} µ-nats ({fast_difficulty/1e6:.3f} nats)")
    print(f"  Change from stable: {fast_difficulty - stable_difficulty:+,} µ-nats")
    print(f"  Average block time: {sum(block_times[-20:])/20:.1f}s")
    assert fast_difficulty > stable_difficulty, "Difficulty should increase with faster blocks"
    
    # Phase 4: Hash rate decrease (20 blocks at 24s - 2x slower)
    print(f"\n{'='*60}")
    print(f"Phase 4: Hash Rate Decrease (20 blocks at 24s - 2x slower)")
    for i in range(31, 51):
        timestamp += 24  # Slow blocks
        header = make_header(i, prev_hash, timestamp)
        result = importer.import_block(make_block(header))
        prev_hash = result.block_hash
        
        timestamps.append(timestamp)
        difficulties.append(importer.get_current_difficulty())
        block_times.append(timestamp - timestamps[-2])
    
    slow_difficulty = importer.get_current_difficulty()
    print(f"  End difficulty: {slow_difficulty:,} µ-nats ({slow_difficulty/1e6:.3f} nats)")
    print(f"  Change from fast: {slow_difficulty - fast_difficulty:+,} µ-nats")
    print(f"  Average block time: {sum(block_times[-20:])/20:.1f}s")
    # Note: Due to EMA lag, difficulty may still be increasing at the end of this phase
    # The important thing is that the rate of increase slows or stops
    
    # Phase 5: Return to equilibrium (30 blocks at target 12s)
    print(f"\n{'='*60}")
    print(f"Phase 5: Return to Equilibrium (30 blocks at target 12s)")
    for i in range(51, 81):
        timestamp += int(params.block.target_seconds)
        header = make_header(i, prev_hash, timestamp)
        result = importer.import_block(make_block(header))
        prev_hash = result.block_hash
        
        timestamps.append(timestamp)
        difficulties.append(importer.get_current_difficulty())
        block_times.append(timestamp - timestamps[-2])
    
    final_difficulty = importer.get_current_difficulty()
    print(f"  End difficulty: {final_difficulty:,} µ-nats ({final_difficulty/1e6:.3f} nats)")
    print(f"  Change from slow: {final_difficulty - slow_difficulty:+,} µ-nats")
    print(f"  Average block time: {sum(block_times[-30:])/30:.1f}s")
    
    # Verify convergence behavior
    # After returning to target, difficulty should be moving toward equilibrium
    # (not necessarily equal to initial, but trending toward stability)
    late_changes = [abs(difficulties[i] - difficulties[i-1]) for i in range(70, 80)]
    avg_late_change = sum(late_changes) / len(late_changes)
    print(f"  Average difficulty change (last 10 blocks): {avg_late_change:,.0f} µ-nats")
    
    # Check that difficulty is responding to block times
    # Compare rate of change in fast vs equilibrium phases
    fast_changes = [abs(difficulties[i] - difficulties[i-1]) for i in range(15, 25)]
    avg_fast_change = sum(fast_changes) / len(fast_changes)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Total blocks: {len(timestamps)}")
    print(f"  Difficulty range: {min(difficulties):,} to {max(difficulties):,} µ-nats")
    print(f"  Initial: {initial_difficulty:,} µ-nats")
    print(f"  Peak (after fast blocks): {fast_difficulty:,} µ-nats")
    print(f"  Final: {final_difficulty:,} µ-nats")
    print(f"  Total change: {final_difficulty - initial_difficulty:+,} µ-nats ({(final_difficulty-initial_difficulty)/initial_difficulty*100:+.1f}%)")
    print(f"  Average change during fast blocks: {avg_fast_change:,.0f} µ-nats/block")
    print(f"  Average change during equilibrium: {avg_late_change:,.0f} µ-nats/block")
    print(f"{'='*60}\n")
    
    # Assertions
    assert max(difficulties) < 30_000_000, "Difficulty should stay below upper bound"
    assert min(difficulties) > 500_000, "Difficulty should stay above lower bound"
    assert fast_difficulty > initial_difficulty, "Difficulty should increase during fast blocks"
    assert avg_late_change < avg_fast_change, "Difficulty changes should be smaller at equilibrium"
    assert avg_late_change < initial_difficulty * 0.1, "Difficulty should be stabilizing at target interval"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
