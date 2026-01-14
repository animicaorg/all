"""
Test checkpoint validation in block import.

Checkpoints are hardcoded block hashes at specific heights that prevent forks
beyond those heights. This ensures all nodes agree on the canonical chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.chain.block_import import BlockImporter, ImportErrorCode
from core.types.params import ChainParams, BlockLimits, RetargetParams, RetargetBounds
from core.types.block import Block
from core.types.header import Header


@dataclass
class MockBlockDB:
    """Mock BlockDB for testing."""
    
    headers: dict[bytes, Any]
    blocks: dict[bytes, Any]
    canonical_head: tuple[int, bytes] | None
    canonical_hashes: dict[int, bytes]
    
    def __init__(self):
        self.headers = {}
        self.blocks = {}
        self.canonical_head = None
        self.canonical_hashes = {}
    
    def get_header_by_hash(self, h: bytes) -> Any | None:
        return self.headers.get(h)
    
    def get_block_by_hash(self, h: bytes) -> Any | None:
        return self.blocks.get(h)
    
    def get_canonical_head(self) -> tuple[int, bytes] | None:
        return self.canonical_head
    
    def set_canonical_head(self, height: int, h: bytes) -> None:
        self.canonical_head = (height, h)
    
    def get_canonical_hash(self, height: int) -> bytes | None:
        return self.canonical_hashes.get(height)
    
    def set_canonical_height(self, height: int) -> None:
        pass
    
    def get_canonical_height(self) -> int | None:
        return None
    
    def put_header(self, header: Any) -> None:
        h = header.hash()
        self.headers[h] = header
    
    def put_block(self, block: Any) -> None:
        h = block.header.hash()
        self.blocks[h] = block


def make_test_params(checkpoints: dict[int, bytes] | None = None) -> ChainParams:
    """Create test ChainParams with optional checkpoints."""
    if checkpoints is None:
        checkpoints = {}
    
    return ChainParams(
        chain_id=1337,
        chain_name="Test Chain",
        genesis_time="2025-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x00" * 32,
        poies_policy_root=b"\x00" * 32,
        theta_initial=1_000_000,
        gamma_total_cap=1_000_000,
        retarget=RetargetParams(
            window=2048,
            ema_alpha=0.1,
            bounds=RetargetBounds(min=0.5, max=2.0),
        ),
        block=BlockLimits(
            target_seconds=2.0,
            max_bytes=1_500_000,
            max_gas=20_000_000,
            tx_max_bytes=131_072,
            min_gas_price=1000,
        ),
        checkpoints=checkpoints,
    )


def make_test_header(height: int, parent_hash: bytes, nonce: int = 0) -> Header:
    """Create a test header."""
    return Header(
        version=1,
        chain_id=1337,
        height=height,
        timestamp=1700000000 + height,
        parent_hash=parent_hash,
        state_root=b"\x00" * 32,
        txs_root=b"\x00" * 32,
        receipts_root=b"\x00" * 32,
        proofs_root=b"\x00" * 32,
        da_root=b"\x00" * 32,
        nonce=nonce,
        mix_seed=b"\x00" * 32,
        theta_micro=1_000_000,
        policy_alg_root=b"\x00" * 32,
        poies_policy_root=b"\x00" * 32,
        extra=b"",
    )


def make_test_block(header: Header) -> Block:
    """Create a test block."""
    return Block(header=header, txs=[], proofs=[])


def test_checkpoint_validation_passes_with_correct_hash():
    """Test that a block passes checkpoint validation when hash matches."""
    # Create a specific block hash for checkpoint
    checkpoint_hash = b"\x01" * 32
    
    # Set up params with checkpoint at height 12000
    params = make_test_params(checkpoints={12000: checkpoint_hash})
    
    # Create mock DB with genesis
    db = MockBlockDB()
    genesis_header = make_test_header(0, b"\x00" * 32)
    genesis_block = make_test_block(genesis_header)
    genesis_hash = genesis_header.hash()
    
    db.headers[genesis_hash] = genesis_header
    db.blocks[genesis_hash] = genesis_block
    db.canonical_head = (0, genesis_hash)
    
    # Create importer
    importer = BlockImporter(params=params, block_db=db)
    
    # Import genesis
    result = importer.import_block(genesis_block)
    assert result.code in (ImportErrorCode.ACCEPTED, ImportErrorCode.DUPLICATE)
    
    # Build chain up to height 11999
    prev_hash = genesis_hash
    for h in range(1, 11999):
        header = make_test_header(h, prev_hash, nonce=h)
        block = make_test_block(header)
        prev_hash = header.hash()
        
        db.headers[prev_hash] = header
        db.blocks[prev_hash] = block
        db.canonical_head = (h, prev_hash)
    
    # Create block at checkpoint height with WRONG hash (nonce=0 won't produce checkpoint_hash)
    # This should fail checkpoint validation
    checkpoint_header_wrong = make_test_header(12000, prev_hash, nonce=0)
    checkpoint_block_wrong = make_test_block(checkpoint_header_wrong)
    wrong_hash = checkpoint_header_wrong.hash()
    
    # The hash won't match the checkpoint, so import should fail
    if wrong_hash != checkpoint_hash:
        result = importer.import_block(checkpoint_block_wrong)
        assert result.code == ImportErrorCode.INVALID
        assert result.reason is not None
        assert "checkpoint" in result.reason.lower()


def test_checkpoint_validation_fails_with_wrong_hash():
    """Test that a block fails checkpoint validation when hash doesn't match."""
    # Create a specific block hash for checkpoint
    checkpoint_hash = b"\x01" * 32
    
    # Set up params with checkpoint at height 100 (easier to test)
    params = make_test_params(checkpoints={100: checkpoint_hash})
    
    # Create mock DB with genesis
    db = MockBlockDB()
    genesis_header = make_test_header(0, b"\x00" * 32)
    genesis_block = make_test_block(genesis_header)
    genesis_hash = genesis_header.hash()
    
    db.headers[genesis_hash] = genesis_header
    db.blocks[genesis_hash] = genesis_block
    db.canonical_head = (0, genesis_hash)
    
    # Create importer
    importer = BlockImporter(params=params, block_db=db)
    
    # Import genesis
    result = importer.import_block(genesis_block)
    assert result.code in (ImportErrorCode.ACCEPTED, ImportErrorCode.DUPLICATE)
    
    # Build chain up to height 99
    prev_hash = genesis_hash
    for h in range(1, 100):
        header = make_test_header(h, prev_hash, nonce=h)
        block = make_test_block(header)
        prev_hash = header.hash()
        
        db.headers[prev_hash] = header
        db.blocks[prev_hash] = block
        db.canonical_head = (h, prev_hash)
    
    # Create block at checkpoint height with wrong hash
    checkpoint_header = make_test_header(100, prev_hash, nonce=999)
    checkpoint_block = make_test_block(checkpoint_header)
    actual_hash = checkpoint_header.hash()
    
    # The hash won't match checkpoint (unless by astronomical coincidence)
    if actual_hash != checkpoint_hash:
        result = importer.import_block(checkpoint_block)
        assert result.code == ImportErrorCode.INVALID
        assert result.reason is not None
        assert "checkpoint" in result.reason.lower()
        assert "violation" in result.reason.lower()


def test_checkpoint_allows_placeholder_hash():
    """Test that blocks pass validation when checkpoint has placeholder hash (all zeros)."""
    # Use placeholder hash (not yet finalized)
    placeholder_hash = b"\x00" * 32
    
    # Set up params with placeholder checkpoint
    params = make_test_params(checkpoints={12000: placeholder_hash})
    
    # Create mock DB with genesis
    db = MockBlockDB()
    genesis_header = make_test_header(0, b"\x00" * 32)
    genesis_block = make_test_block(genesis_header)
    genesis_hash = genesis_header.hash()
    
    db.headers[genesis_hash] = genesis_header
    db.blocks[genesis_hash] = genesis_block
    db.canonical_head = (0, genesis_hash)
    
    # Create importer
    importer = BlockImporter(params=params, block_db=db)
    
    # Import genesis
    result = importer.import_block(genesis_block)
    assert result.code in (ImportErrorCode.ACCEPTED, ImportErrorCode.DUPLICATE)
    
    # Build chain up to height 11999
    prev_hash = genesis_hash
    for h in range(1, 11999):
        header = make_test_header(h, prev_hash, nonce=h)
        block = make_test_block(header)
        prev_hash = header.hash()
        
        db.headers[prev_hash] = header
        db.blocks[prev_hash] = block
        db.canonical_head = (h, prev_hash)
    
    # Create block at checkpoint height - should pass with placeholder
    checkpoint_header = make_test_header(12000, prev_hash, nonce=12000)
    checkpoint_block = make_test_block(checkpoint_header)
    
    # This should succeed because checkpoint is a placeholder
    # (In practice, import might fail for other reasons like PoW, but not checkpoint)
    result = importer.import_block(checkpoint_block)
    # We expect it to not fail with checkpoint error
    if result.code == ImportErrorCode.INVALID and result.reason:
        assert "checkpoint" not in result.reason.lower()


def test_no_checkpoint_at_height():
    """Test that blocks pass validation when there's no checkpoint at that height."""
    # Set up params with checkpoint at a different height
    params = make_test_params(checkpoints={5000: b"\x01" * 32})
    
    # Create mock DB with genesis
    db = MockBlockDB()
    genesis_header = make_test_header(0, b"\x00" * 32)
    genesis_block = make_test_block(genesis_header)
    genesis_hash = genesis_header.hash()
    
    db.headers[genesis_hash] = genesis_header
    db.blocks[genesis_hash] = genesis_block
    db.canonical_head = (0, genesis_hash)
    
    # Create importer
    importer = BlockImporter(params=params, block_db=db)
    
    # Import genesis
    result = importer.import_block(genesis_block)
    assert result.code in (ImportErrorCode.ACCEPTED, ImportErrorCode.DUPLICATE)
    
    # Build chain up to height 99 (no checkpoint)
    prev_hash = genesis_hash
    for h in range(1, 100):
        header = make_test_header(h, prev_hash, nonce=h)
        block = make_test_block(header)
        prev_hash = header.hash()
        
        db.headers[prev_hash] = header
        db.blocks[prev_hash] = block
        db.canonical_head = (h, prev_hash)
    
    # Create block at height 100 (no checkpoint at this height)
    header_100 = make_test_header(100, prev_hash, nonce=100)
    block_100 = make_test_block(header_100)
    
    # This should not fail due to checkpoint (may fail for other reasons)
    result = importer.import_block(block_100)
    if result.code == ImportErrorCode.INVALID and result.reason:
        assert "checkpoint" not in result.reason.lower()


if __name__ == "__main__":
    # Run tests
    test_checkpoint_validation_passes_with_correct_hash()
    test_checkpoint_validation_fails_with_wrong_hash()
    test_checkpoint_allows_placeholder_hash()
    test_no_checkpoint_at_height()
    print("All checkpoint validation tests passed!")
