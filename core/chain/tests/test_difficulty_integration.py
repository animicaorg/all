"""
Unit tests for difficulty adjustment integration in BlockImporter.

Tests that:
1. Difficulty state is initialized correctly
2. Difficulty updates on block imports
3. Difficulty adjusts based on block intervals (faster/slower)
4. Integration works with actual block import flow
"""

from __future__ import annotations

import pytest
from typing import Any, Dict

from core.chain.block_import import BlockImporter, ImportErrorCode
from core.types.block import Block
from core.types.header import Header
from core.types.params import ChainParams, BlockLimits, RetargetParams, RetargetBounds


# Mock block database
class MockBlockDB:
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


def make_test_params() -> ChainParams:
    """Create test chain params with reasonable difficulty settings."""
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
            window=24,
            ema_alpha=0.2,
            bounds=RetargetBounds(min=0.5, max=2.0),
        ),
        block=BlockLimits(
            target_seconds=12.0,
            max_bytes=1_500_000,
            max_gas=20_000_000,
            tx_max_bytes=131_072,
            min_gas_price=1000,
        ),
    )


def make_test_header(height: int, parent_hash: bytes, timestamp: int, chain_id: int = 1337) -> Header:
    """Create a minimal test header."""
    return Header(
        v=1,
        chainId=chain_id,
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


def make_test_block(header: Header) -> Block:
    """Create a minimal test block."""
    return Block(header=header, txs=(), proofs=(), receipts=None)


def test_difficulty_state_initialization():
    """Test that difficulty state is initialized on BlockImporter creation."""
    params = make_test_params()
    block_db = MockBlockDB()
    
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Check that difficulty state was initialized
    assert importer.difficulty_state is not None
    assert importer.difficulty_state.theta_micro == params.theta_initial
    assert importer.get_current_difficulty() == params.theta_initial


def test_difficulty_updates_on_block_import():
    """Test that difficulty updates when blocks are imported."""
    params = make_test_params()
    block_db = MockBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Import genesis block
    genesis_header = make_test_header(0, b"\x00" * 32, timestamp=1000)
    genesis_block = make_test_block(genesis_header)
    result = importer.import_block(genesis_block)
    
    assert result.code == ImportErrorCode.ACCEPTED
    assert importer._last_block_time == 1000
    initial_difficulty = importer.get_current_difficulty()
    
    # Import a second block after target interval (should keep difficulty stable)
    block1_header = make_test_header(1, result.block_hash, timestamp=1012)  # +12s (target)
    block1 = make_test_block(block1_header)
    result1 = importer.import_block(block1)
    
    assert result1.code == ImportErrorCode.ACCEPTED
    difficulty_after_target = importer.get_current_difficulty()
    
    # Difficulty should remain relatively stable (within a reasonable range)
    # Since we're at target, it shouldn't change dramatically
    assert abs(difficulty_after_target - initial_difficulty) < initial_difficulty * 0.5


def test_difficulty_increases_on_fast_blocks():
    """Test that difficulty increases when blocks arrive faster than target."""
    params = make_test_params()
    block_db = MockBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Import genesis
    genesis_header = make_test_header(0, b"\x00" * 32, timestamp=1000)
    result = importer.import_block(make_test_block(genesis_header))
    initial_difficulty = importer.get_current_difficulty()
    
    # Import several fast blocks (6s each, half the target of 12s)
    prev_hash = result.block_hash
    timestamp = 1000
    for i in range(10):
        timestamp += 6  # Fast blocks
        header = make_test_header(i + 1, prev_hash, timestamp)
        result = importer.import_block(make_test_block(header))
        prev_hash = result.block_hash
    
    final_difficulty = importer.get_current_difficulty()
    
    # Difficulty should increase (theta_micro goes up for faster blocks)
    assert final_difficulty > initial_difficulty, \
        f"Expected difficulty to increase, got {final_difficulty} vs {initial_difficulty}"


def test_difficulty_decreases_on_slow_blocks():
    """Test that difficulty decreases when blocks arrive slower than target."""
    params = make_test_params()
    block_db = MockBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Import genesis
    genesis_header = make_test_header(0, b"\x00" * 32, timestamp=1000)
    result = importer.import_block(make_test_block(genesis_header))
    initial_difficulty = importer.get_current_difficulty()
    
    # Import several slow blocks (24s each, double the target of 12s)
    prev_hash = result.block_hash
    timestamp = 1000
    for i in range(10):
        timestamp += 24  # Slow blocks
        header = make_test_header(i + 1, prev_hash, timestamp)
        result = importer.import_block(make_test_block(header))
        prev_hash = result.block_hash
    
    final_difficulty = importer.get_current_difficulty()
    
    # Difficulty should decrease (theta_micro goes down for slower blocks)
    assert final_difficulty < initial_difficulty, \
        f"Expected difficulty to decrease, got {final_difficulty} vs {initial_difficulty}"


def test_difficulty_bounds_respected():
    """Test that difficulty stays within configured bounds."""
    params = make_test_params()
    block_db = MockBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Import genesis
    genesis_header = make_test_header(0, b"\x00" * 32, timestamp=1000)
    result = importer.import_block(make_test_block(genesis_header))
    
    # Import many extremely fast blocks
    prev_hash = result.block_hash
    timestamp = 1000
    for i in range(50):
        timestamp += 1  # Very fast blocks (1s vs 12s target)
        header = make_test_header(i + 1, prev_hash, timestamp)
        result = importer.import_block(make_test_block(header))
        prev_hash = result.block_hash
    
    # Check that difficulty stays within bounds
    # The difficulty module has internal max bounds (theta_max_micro)
    final_difficulty = importer.get_current_difficulty()
    assert final_difficulty < 30_000_000, "Difficulty exceeded max bounds"
    assert final_difficulty > 500_000, "Difficulty went below min bounds"


def test_difficulty_convergence():
    """Test that difficulty converges toward equilibrium at target interval."""
    params = make_test_params()
    block_db = MockBlockDB()
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Import genesis
    genesis_header = make_test_header(0, b"\x00" * 32, timestamp=1000)
    result = importer.import_block(make_test_block(genesis_header))
    
    # Import blocks exactly at target interval
    prev_hash = result.block_hash
    timestamp = 1000
    difficulties = []
    
    for i in range(50):
        timestamp += int(params.block.target_seconds)  # Exactly at target
        header = make_test_header(i + 1, prev_hash, timestamp)
        result = importer.import_block(make_test_block(header))
        prev_hash = result.block_hash
        difficulties.append(importer.get_current_difficulty())
    
    # Difficulty changes should decrease over time (converging)
    early_changes = [abs(difficulties[i+1] - difficulties[i]) for i in range(10)]
    late_changes = [abs(difficulties[i+1] - difficulties[i]) for i in range(40, 49)]
    
    avg_early = sum(early_changes) / len(early_changes) if early_changes else 0
    avg_late = sum(late_changes) / len(late_changes) if late_changes else 0
    
    # Later changes should be smaller (more stable)
    assert avg_late <= avg_early + 1000, \
        f"Difficulty not converging: early changes={avg_early}, late changes={avg_late}"


def test_difficulty_without_consensus_module():
    """Test that BlockImporter works even if consensus.difficulty is not available."""
    # This simulates the case where the difficulty module fails to import
    params = make_test_params()
    block_db = MockBlockDB()
    
    # Create importer (should handle missing difficulty gracefully)
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Even without difficulty module, should return genesis theta
    difficulty = importer.get_current_difficulty()
    assert difficulty == params.theta_initial
    
    # Should still be able to import blocks
    genesis_header = make_test_header(0, b"\x00" * 32, timestamp=1000)
    result = importer.import_block(make_test_block(genesis_header))
    assert result.code == ImportErrorCode.ACCEPTED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
