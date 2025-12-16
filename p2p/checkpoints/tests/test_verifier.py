"""Tests for checkpoint verifier."""

import pytest
from typing import Optional

from p2p.checkpoints.loader import Checkpoint
from p2p.checkpoints.verifier import CheckpointVerifier


class MockChainView:
    """Mock chain view for testing."""
    
    def __init__(self, blocks: dict[int, str]):
        """
        Initialize mock chain.
        
        Args:
            blocks: Dict mapping height to block hash.
        """
        self.blocks = blocks
    
    async def get_block_hash_at_height(self, height: int) -> Optional[str]:
        """Get block hash at height."""
        return self.blocks.get(height)


@pytest.mark.asyncio
async def test_verifier_no_checkpoints():
    """Test verifier with no checkpoints."""
    verifier = CheckpointVerifier([])
    chain = MockChainView({100: "0xabc123"})
    
    assert not verifier.has_checkpoints()
    
    # Should always pass with no checkpoints
    is_valid, errors = await verifier.verify_chain(chain)
    assert is_valid
    assert errors == []


@pytest.mark.asyncio
async def test_verifier_matching_checkpoints():
    """Test verifier with matching checkpoints."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
        Checkpoint(height=200, hash="0xdef456"),
    ]
    verifier = CheckpointVerifier(checkpoints)
    
    chain = MockChainView({
        100: "0xabc123",
        200: "0xdef456",
    })
    
    assert verifier.has_checkpoints()
    
    is_valid, errors = await verifier.verify_chain(chain)
    assert is_valid
    assert errors == []


@pytest.mark.asyncio
async def test_verifier_mismatch():
    """Test verifier detects checkpoint mismatch."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
        Checkpoint(height=200, hash="0xdef456"),
    ]
    verifier = CheckpointVerifier(checkpoints)
    
    # Height 200 has wrong hash
    chain = MockChainView({
        100: "0xabc123",
        200: "0xwrong",
    })
    
    is_valid, errors = await verifier.verify_chain(chain)
    assert not is_valid
    assert len(errors) == 1
    assert "200" in errors[0]
    assert "mismatch" in errors[0].lower()


@pytest.mark.asyncio
async def test_verifier_skips_unsynced():
    """Test verifier skips checkpoints for unsynced blocks."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
        Checkpoint(height=200, hash="0xdef456"),
        Checkpoint(height=300, hash="0x789xyz"),
    ]
    verifier = CheckpointVerifier(checkpoints)
    
    # Only have blocks up to height 200
    chain = MockChainView({
        100: "0xabc123",
        200: "0xdef456",
    })
    
    # Should pass - skips checkpoint at 300
    is_valid, errors = await verifier.verify_chain(chain)
    assert is_valid
    assert errors == []


@pytest.mark.asyncio
async def test_verifier_max_height():
    """Test verifier respects max_height parameter."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
        Checkpoint(height=200, hash="0xwrong"),  # Wrong but beyond max_height
    ]
    verifier = CheckpointVerifier(checkpoints)
    
    chain = MockChainView({
        100: "0xabc123",
        200: "0xwrong",
    })
    
    # Verify only up to height 150
    is_valid, errors = await verifier.verify_chain(chain, max_height=150)
    assert is_valid
    assert errors == []


@pytest.mark.asyncio
async def test_verifier_single_block():
    """Test verifying a single block."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
    ]
    verifier = CheckpointVerifier(checkpoints)
    chain = MockChainView({})
    
    # Matching block
    is_valid, error = await verifier.verify_block(chain, 100, "0xabc123")
    assert is_valid
    assert error is None
    
    # Mismatching block
    is_valid, error = await verifier.verify_block(chain, 100, "0xwrong")
    assert not is_valid
    assert error is not None
    assert "mismatch" in error.lower()
    
    # No checkpoint at this height
    is_valid, error = await verifier.verify_block(chain, 200, "0xanyhash")
    assert is_valid
    assert error is None


@pytest.mark.asyncio
async def test_verifier_hash_normalization():
    """Test that verifier normalizes hashes for comparison."""
    checkpoints = [
        Checkpoint(height=100, hash="0xABC123"),  # Uppercase
    ]
    verifier = CheckpointVerifier(checkpoints)
    chain = MockChainView({
        100: "0xabc123",  # Lowercase
    })
    
    # Should match despite case difference
    is_valid, errors = await verifier.verify_chain(chain)
    assert is_valid
    assert errors == []


@pytest.mark.asyncio
async def test_verifier_hash_without_0x_prefix():
    """Test that verifier handles hashes without 0x prefix."""
    checkpoints = [
        Checkpoint(height=100, hash="abc123"),  # No 0x
    ]
    verifier = CheckpointVerifier(checkpoints)
    chain = MockChainView({
        100: "0xabc123",  # With 0x
    })
    
    # Should match despite prefix difference
    is_valid, errors = await verifier.verify_chain(chain)
    assert is_valid
    assert errors == []


def test_verifier_get_checkpoint_at_height():
    """Test getting checkpoint at specific height."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
        Checkpoint(height=200, hash="0xdef456"),
    ]
    verifier = CheckpointVerifier(checkpoints)
    
    cp = verifier.get_checkpoint_at_height(100)
    assert cp is not None
    assert cp.height == 100
    assert cp.hash == "0xabc123"
    
    cp = verifier.get_checkpoint_at_height(300)
    assert cp is None


def test_verifier_highest_lowest_checkpoint():
    """Test getting highest and lowest checkpoint heights."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
        Checkpoint(height=200, hash="0xdef456"),
        Checkpoint(height=300, hash="0x789xyz"),
    ]
    verifier = CheckpointVerifier(checkpoints)
    
    assert verifier.get_lowest_checkpoint_height() == 100
    assert verifier.get_highest_checkpoint_height() == 300
    
    # Empty verifier
    empty_verifier = CheckpointVerifier([])
    assert empty_verifier.get_lowest_checkpoint_height() is None
    assert empty_verifier.get_highest_checkpoint_height() is None
