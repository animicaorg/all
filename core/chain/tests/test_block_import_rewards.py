# SPDX-License-Identifier: Apache-2.0
"""
Tests for block reward application during block import.

Verifies that BlockImporter correctly applies block rewards to the coinbase
address when importing blocks, using the full params dict from spec/params.yaml.
"""

from __future__ import annotations

import pytest


def test_load_full_params_dict_devnet():
    """Test that _load_full_params_dict loads devnet config correctly."""
    from core.chain.block_import import _load_full_params_dict
    
    params = _load_full_params_dict(1337)
    
    # Verify structure
    assert isinstance(params, dict)
    assert "monetary" in params
    assert "issuance" in params["monetary"]
    assert "subsidy" in params["monetary"]["issuance"]
    
    # Verify devnet subsidy config
    subsidy = params["monetary"]["issuance"]["subsidy"]
    assert subsidy["start_nANM_per_block"] == 5_000_000_000  # 5 ANM
    assert subsidy["epoch_length_blocks"] == 1_350_000
    assert subsidy["decay_pct_per_epoch"] == 50.0


def test_load_full_params_dict_mainnet():
    """Test that _load_full_params_dict loads mainnet config correctly."""
    from core.chain.block_import import _load_full_params_dict
    
    params = _load_full_params_dict(1)
    
    # Verify structure
    assert isinstance(params, dict)
    assert "monetary" in params
    assert "issuance" in params["monetary"]
    assert "subsidy" in params["monetary"]["issuance"]
    
    # Verify mainnet subsidy config
    subsidy = params["monetary"]["issuance"]["subsidy"]
    assert subsidy["start_nANM_per_block"] == 5_000_000_000  # 5 ANM


def test_block_importer_initializes_with_full_params():
    """Test that BlockImporter initializes with full params dict."""
    from core.chain.block_import import BlockImporter
    from core.types.params import ChainParams, RetargetParams, RetargetBounds, BlockLimits
    from core.db.block_db import BlockDB
    from core.db.sqlite import SQLiteKV
    
    # Create minimal but valid ChainParams
    params = ChainParams(
        chain_id=1337,
        chain_name="Test",
        genesis_time="2024-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x00" * 32,
        poies_policy_root=b"\x00" * 32,
        theta_initial=1_000_000,
        theta_min=1_000_000,
        theta_max=3_000_000,
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
            tx_max_bytes=1_500_000,
            min_gas_price=0,
        ),
    )
    
    # Create minimal block_db
    kv = SQLiteKV(":memory:")
    block_db = BlockDB(kv)
    
    # Create importer
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Verify full_params_dict was loaded
    assert hasattr(importer, "full_params_dict")
    assert isinstance(importer.full_params_dict, dict)
    assert "monetary" in importer.full_params_dict


def test_block_reward_computation_with_full_params():
    """Test that block rewards can be computed using full params from BlockImporter."""
    from core.chain.block_import import _load_full_params_dict
    from consensus.rewards import compute_block_reward
    
    # Load full params for devnet
    params = _load_full_params_dict(1337)
    
    # Compute reward at height 1
    rewards = compute_block_reward(chain_id=1337, height=1, params=params)
    
    # Verify reward structure
    assert isinstance(rewards, list)
    assert len(rewards) >= 1  # At least miner reward
    
    # Verify miner reward amount (5 ANM for devnet)
    miner_addr, miner_amount = rewards[0]
    assert miner_amount == 5_000_000_000  # 5 ANM in nANM
    assert isinstance(miner_addr, str)
    assert miner_addr.startswith("anim1")


def test_block_importer_full_params_dict_fallback():
    """Test that BlockImporter falls back to empty dict if spec/params.yaml not found."""
    from core.chain.block_import import BlockImporter
    from core.types.params import ChainParams, RetargetParams, RetargetBounds, BlockLimits
    from core.db.block_db import BlockDB
    from core.db.sqlite import SQLiteKV
    
    # Create minimal but valid ChainParams with invalid chain_id (to test fallback)
    params = ChainParams(
        chain_id=99999,  # Non-existent network
        chain_name="Test",
        genesis_time="2024-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x00" * 32,
        poies_policy_root=b"\x00" * 32,
        theta_initial=1_000_000,
        theta_min=1_000_000,
        theta_max=3_000_000,
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
            tx_max_bytes=1_500_000,
            min_gas_price=0,
        ),
    )
    
    # Create minimal block_db
    kv = SQLiteKV(":memory:")
    block_db = BlockDB(kv)
    
    # Create importer
    importer = BlockImporter(params=params, block_db=block_db)
    
    # Verify full_params_dict was set to empty dict (fallback)
    assert hasattr(importer, "full_params_dict")
    assert isinstance(importer.full_params_dict, dict)
    # For non-existent network, should be empty or minimal
    # (may have empty dict if network not found)


def test_block_reward_empty_when_params_missing():
    """Test that block rewards are empty when params are missing."""
    from consensus.rewards import compute_block_reward
    
    # Empty params dict
    rewards = compute_block_reward(chain_id=1337, height=1, params={})
    assert rewards == []
    
    # None params
    rewards = compute_block_reward(chain_id=1337, height=1, params=None)
    assert rewards == []


if __name__ == "__main__":
    # Allow running tests directly with: python test_block_import_rewards.py
    pytest.main([__file__, "-v"])
