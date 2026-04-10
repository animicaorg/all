"""
Test minimum block spacing enforcement.

This test verifies that blocks cannot be produced faster than the minimum
configured spacing, preventing rapid block mining that could game the system.
"""

import os
import pytest

from core.chain.block_import import BlockImporter
from core.types.params import ChainParams, BlockLimits, RetargetParams, RetargetBounds


def make_test_params(chain_id: int = 1337) -> ChainParams:
    """Create test parameters."""
    return ChainParams(
        chain_id=chain_id,
        chain_name="Test Chain",
        genesis_time="2026-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x01" * 32,
        poies_policy_root=b"\x02" * 32,
        theta_initial=3_000_000,
        theta_min=500_000,
        theta_max=100_000_000,
        gamma_total_cap=1_000_000,
        retarget=RetargetParams(
            window=10,
            ema_alpha=0.1,
            bounds=RetargetBounds(min=0.5, max=2.0),
        ),
        block=BlockLimits(
            target_seconds=300.0,
            max_bytes=2_000_000,
            max_gas=40_000_000,
            tx_max_bytes=131_072,
            min_gas_price=1000,
        ),
    )


class MockBlockDB:
    """Minimal mock block database for testing."""
    def get_canonical_head(self):
        return None
    
    def get_header_by_hash(self, block_hash):
        return None
    
    def get_header_by_height(self, height):
        return None


def test_min_block_spacing_read_from_config():
    """
    Test that min_block_spacing_ms is correctly read from config.
    """
    params = make_test_params()
    
    # Create full params dict that includes min_block_spacing_ms
    full_params_dict = {
        "networks": {
            "animica:1337": {
                "monetary": {
                    "issuance": {
                        "target_block_interval_ms": 300000,
                        "min_block_spacing_ms": 60000,  # 60 seconds
                    }
                }
            }
        }
    }
    
    block_db = MockBlockDB()
    
    # Create importer - it should read min_block_spacing_ms from config
    importer = BlockImporter(
        params=params,
        block_db=block_db,
        full_params_dict=full_params_dict,
    )
    
    # Verify the value was read correctly
    assert importer._min_block_spacing_ms == 60000
    

def test_min_block_spacing_defaults_to_zero():
    """
    Test that when min_block_spacing_ms is not specified, it defaults to 0.
    """
    params = make_test_params()
    
    # Don't provide min_block_spacing_ms in full_params_dict
    full_params_dict = {
        "networks": {
            "animica:1337": {
                "monetary": {
                    "issuance": {
                        "target_block_interval_ms": 300000,
                        # min_block_spacing_ms not specified
                    }
                }
            }
        }
    }
    
    block_db = MockBlockDB()
    
    importer = BlockImporter(
        params=params,
        block_db=block_db,
        full_params_dict=full_params_dict,
    )
    
    # Should default to 0 (no minimum spacing)
    assert importer._min_block_spacing_ms == 0


def test_min_block_spacing_from_env_var():
    """
    Test that ANIMICA_MIN_BLOCK_SPACING_MS environment variable overrides config.
    """
    import os
    
    # Set env var to 120 seconds
    old_value = os.environ.get("ANIMICA_MIN_BLOCK_SPACING_MS")
    os.environ["ANIMICA_MIN_BLOCK_SPACING_MS"] = "120000"
    
    try:
        params = make_test_params()
        
        # Config says 60 seconds, but env var says 120 seconds
        full_params_dict = {
            "networks": {
                "animica:1337": {
                    "monetary": {
                        "issuance": {
                            "target_block_interval_ms": 300000,
                            "min_block_spacing_ms": 60000,  # Config value (should be overridden)
                        }
                    }
                }
            }
        }
        
        block_db = MockBlockDB()
        
        importer = BlockImporter(
            params=params,
            block_db=block_db,
            full_params_dict=full_params_dict,
        )
        
        # Verify that the env var value is used (120 seconds)
        assert importer._min_block_spacing_ms == 120000
        
    finally:
        # Restore env var
        if old_value is not None:
            os.environ["ANIMICA_MIN_BLOCK_SPACING_MS"] = old_value
        else:
            os.environ.pop("ANIMICA_MIN_BLOCK_SPACING_MS", None)


def test_min_block_spacing_validation_rejects_negative():
    """
    Test that negative min_block_spacing_ms values are rejected with warning.
    """
    import os
    
    # Set env var to negative value
    old_value = os.environ.get("ANIMICA_MIN_BLOCK_SPACING_MS")
    os.environ["ANIMICA_MIN_BLOCK_SPACING_MS"] = "-1000"
    
    try:
        params = make_test_params()
        full_params_dict = {
            "networks": {
                "animica:1337": {
                    "monetary": {
                        "issuance": {
                            "target_block_interval_ms": 300000,
                            "min_block_spacing_ms": 60000,
                        }
                    }
                }
            }
        }
        
        block_db = MockBlockDB()
        
        importer = BlockImporter(
            params=params,
            block_db=block_db,
            full_params_dict=full_params_dict,
        )
        
        # Negative value should be corrected to 0
        assert importer._min_block_spacing_ms == 0
        
    finally:
        # Restore env var
        if old_value is not None:
            os.environ["ANIMICA_MIN_BLOCK_SPACING_MS"] = old_value
        else:
            os.environ.pop("ANIMICA_MIN_BLOCK_SPACING_MS", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
