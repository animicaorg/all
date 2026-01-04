"""
Tests for instant block (zero-reward, non-advancing) functionality.

Instant blocks:
- Carry instantBlock=True flag in header
- Have zero block rewards (no coinbase)
- Do not advance canonical height for halving calculations
- Skip PoW validation (nonce=0)
- Are produced immediately upon transaction arrival
"""
from __future__ import annotations

from core.chain.block_import import compute_header_hash
from core.types.header import Header
from core.types.block import Block
from consensus.rewards import compute_block_reward, compute_canonical_height
from core.utils.hash import sha3_256


def test_instant_block_header_serialization() -> None:
    """Test that instant block flag is properly serialized/deserialized."""
    zero32 = b"\x00" * 32
    
    # Create an instant block header
    header = Header(
        v=1,
        chainId=1337,
        height=10,
        parentHash=zero32,
        timestamp=1_700_000_000,
        stateRoot=zero32,
        txsRoot=zero32,
        receiptsRoot=zero32,
        proofsRoot=zero32,
        daRoot=zero32,
        mixSeed=zero32,
        poiesPolicyRoot=zero32,
        pqAlgPolicyRoot=zero32,
        thetaMicro=1_000_000,
        nonce=0,  # Instant blocks have nonce=0
        extra=b"",
        instantBlock=True,
    )
    
    # Serialize and deserialize
    cbor_bytes = header.to_cbor()
    obj = header.to_obj()
    
    # Check that instantBlock is in the serialized object
    assert obj.get("instantBlock") is True
    
    # Deserialize and check flag is preserved
    header2 = Header.from_cbor(cbor_bytes)
    assert header2.instantBlock is True
    assert header2.nonce == 0
    
    # Normal blocks should not have the flag
    normal_header = Header(
        v=1,
        chainId=1337,
        height=11,
        parentHash=zero32,
        timestamp=1_700_000_001,
        stateRoot=zero32,
        txsRoot=zero32,
        receiptsRoot=zero32,
        proofsRoot=zero32,
        daRoot=zero32,
        mixSeed=zero32,
        poiesPolicyRoot=zero32,
        pqAlgPolicyRoot=zero32,
        thetaMicro=1_000_000,
        nonce=12345,
        extra=b"",
        instantBlock=False,
    )
    
    normal_obj = normal_header.to_obj()
    # instantBlock should not be in the object if False (to save space)
    assert "instantBlock" not in normal_obj or normal_obj.get("instantBlock") is False


def test_instant_block_zero_reward() -> None:
    """Test that instant blocks always have zero rewards."""
    # Normal block should have rewards
    normal_rewards = compute_block_reward(
        chain_id=1337,
        height=100,
        params={
            "monetary": {
                "issuance": {
                    "subsidy": {
                        "start_nANM_per_block": 5_000_000_000,
                        "epoch_length_blocks": 90_000_000,
                        "decay_pct_per_epoch": 50.0,
                        "tail_nANM_per_block": 100_000,
                        "max_halvings": 64,
                    },
                    "subsidy_split_pct": {
                        "miner": 60,
                        "aicf": 30,
                        "treasury": 10,
                    },
                }
            },
            "system_addresses": {
                "coinbase_default": "anim1test",
                "aicf_treasury": "anim1aicf",
                "treasury": "anim1treasury",
            },
        },
        instant_block=False,
    )
    
    # Instant block should have zero rewards
    instant_rewards = compute_block_reward(
        chain_id=1337,
        height=100,
        params={
            "monetary": {
                "issuance": {
                    "subsidy": {
                        "start_nANM_per_block": 5_000_000_000,
                        "epoch_length_blocks": 90_000_000,
                        "decay_pct_per_epoch": 50.0,
                        "tail_nANM_per_block": 100_000,
                        "max_halvings": 64,
                    },
                    "subsidy_split_pct": {
                        "miner": 60,
                        "aicf": 30,
                        "treasury": 10,
                    },
                }
            },
            "system_addresses": {
                "coinbase_default": "anim1test",
                "aicf_treasury": "anim1aicf",
                "treasury": "anim1treasury",
            },
        },
        instant_block=True,
    )
    
    # Normal block should have non-zero rewards
    assert len(normal_rewards) > 0
    total_normal = sum(amt for _, amt in normal_rewards)
    assert total_normal > 0
    
    # Instant block should have zero rewards
    assert len(instant_rewards) == 0


def test_canonical_height_computation() -> None:
    """Test that canonical height calculation excludes instant blocks."""
    # Genesis block: canonical height = 0
    assert compute_canonical_height(0, False) == 0
    assert compute_canonical_height(0, True) == 0
    
    # Normal block at height 1: canonical height = 1
    assert compute_canonical_height(1, False, canonical_height=0) == 1
    
    # Instant block at height 2: canonical height stays at 1
    assert compute_canonical_height(2, True, canonical_height=1) == 1
    
    # Normal block at height 3: canonical height = 2
    assert compute_canonical_height(3, False, canonical_height=1) == 2
    
    # Multiple instant blocks preserve canonical height
    assert compute_canonical_height(4, True, canonical_height=2) == 2
    assert compute_canonical_height(5, True, canonical_height=2) == 2
    assert compute_canonical_height(6, True, canonical_height=2) == 2
    
    # Next normal block increments canonical height
    assert compute_canonical_height(7, False, canonical_height=2) == 3


def test_instant_block_hash_differs_from_normal() -> None:
    """Test that instant block flag affects the block hash."""
    zero32 = b"\x00" * 32
    
    # Create two identical headers except for instantBlock flag
    base_params = {
        "v": 1,
        "chainId": 1337,
        "height": 10,
        "parentHash": zero32,
        "timestamp": 1_700_000_000,
        "stateRoot": zero32,
        "txsRoot": zero32,
        "receiptsRoot": zero32,
        "proofsRoot": zero32,
        "daRoot": zero32,
        "mixSeed": zero32,
        "poiesPolicyRoot": zero32,
        "pqAlgPolicyRoot": zero32,
        "thetaMicro": 1_000_000,
        "nonce": 0,
        "extra": b"",
    }
    
    instant_header = Header(**base_params, instantBlock=True)
    normal_header = Header(**base_params, instantBlock=False)
    
    instant_hash = compute_header_hash(instant_header)
    normal_hash = compute_header_hash(normal_header)
    
    # Hashes should be different because instantBlock flag is included in serialization
    assert instant_hash != normal_hash


def test_instant_block_build_child() -> None:
    """Test that build_child can create instant blocks."""
    zero32 = b"\x00" * 32
    
    parent = Header(
        v=1,
        chainId=1337,
        height=5,
        parentHash=zero32,
        timestamp=1_700_000_000,
        stateRoot=zero32,
        txsRoot=zero32,
        receiptsRoot=zero32,
        proofsRoot=zero32,
        daRoot=zero32,
        mixSeed=zero32,
        poiesPolicyRoot=zero32,
        pqAlgPolicyRoot=zero32,
        thetaMicro=1_000_000,
        nonce=0,
        extra=b"",
        instantBlock=False,
    )
    
    # Build a normal child
    normal_child = parent.build_child(
        timestamp=1_700_000_010,
        state_root=zero32,
        txs_root=zero32,
        receipts_root=zero32,
        proofs_root=zero32,
        da_root=zero32,
        instant_block=False,
    )
    
    assert normal_child.height == 6
    assert normal_child.parentHash == parent.hash()
    assert normal_child.instantBlock is False
    
    # Build an instant child
    instant_child = parent.build_child(
        timestamp=1_700_000_020,
        state_root=zero32,
        txs_root=zero32,
        receipts_root=zero32,
        proofs_root=zero32,
        da_root=zero32,
        instant_block=True,
    )
    
    assert instant_child.height == 6
    assert instant_child.parentHash == parent.hash()
    assert instant_child.instantBlock is True
    assert instant_child.nonce == 0
