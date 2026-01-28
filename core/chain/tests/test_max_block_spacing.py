"""
Tests for maximum block spacing enforcement.

The max_block_spacing_ms parameter sets an upper bound on the time between
consecutive blocks, preventing stale blocks from being accepted.
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


class MockStateDB:
    """Minimal mock state database for testing."""
    pass


def test_max_block_spacing_read_from_config():
    """
    Test that max_block_spacing_ms is correctly read from config.
    """
    params = make_test_params(chain_id=1)
    
    # Create full params dict that includes max_block_spacing_ms
    full_params_dict = {
        "networks": {
            "animica:1": {
                "monetary": {
                    "issuance": {
                        "target_block_interval_ms": 300000,
                        "min_block_spacing_ms": 60000,  # 60 seconds
                        "max_block_spacing_ms": 3600000,  # 3600 seconds (1 hour)
                    }
                }
            }
        }
    }
    
    # Create importer - it should read max_block_spacing_ms from config
    importer = BlockImporter(
        block_db=MockBlockDB(),
        state_db=MockStateDB(),
        params=params,
        full_params_dict=full_params_dict,
    )
    
    # Verify the value was read correctly
    assert importer._max_block_spacing_ms == 3600000


def test_max_block_spacing_defaults_to_zero():
    """
    Test that when max_block_spacing_ms is not specified, it defaults to 0.
    """
    params = make_test_params(chain_id=1)
    
    # Don't provide max_block_spacing_ms in full_params_dict
    full_params_dict = {
        "networks": {
            "animica:1": {
                "monetary": {
                    "issuance": {
                        "target_block_interval_ms": 300000,
                        # max_block_spacing_ms not specified
                    }
                }
            }
        }
    }
    
    # Create importer without max_block_spacing_ms
    importer = BlockImporter(
        block_db=MockBlockDB(),
        state_db=MockStateDB(),
        params=params,
        full_params_dict=full_params_dict,
    )
    
    # Verify it defaults to 0 (no enforcement)
    assert importer._max_block_spacing_ms == 0


def test_max_block_spacing_from_env_var():
    """
    Test that the ANIMICA_MAX_BLOCK_SPACING_MS environment variable overrides config.
    """
    params = make_test_params(chain_id=1)
    
    full_params_dict = {
        "networks": {
            "animica:1": {
                "monetary": {
                    "issuance": {
                        "target_block_interval_ms": 300000,
                        "max_block_spacing_ms": 3600000,  # Config value (should be overridden)
                    }
                }
            }
        }
    }
    
    # Set environment variable to override config
    old_val = os.environ.get("ANIMICA_MAX_BLOCK_SPACING_MS")
    try:
        os.environ["ANIMICA_MAX_BLOCK_SPACING_MS"] = "7200000"  # 2 hours
        importer = BlockImporter(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            params=params,
            full_params_dict=full_params_dict,
        )
        
        # Verify env var overrides config (2 hours = 7200000 ms)
        assert importer._max_block_spacing_ms == 7200000
    finally:
        if old_val is None:
            os.environ.pop("ANIMICA_MAX_BLOCK_SPACING_MS", None)
        else:
            os.environ["ANIMICA_MAX_BLOCK_SPACING_MS"] = old_val


def test_max_block_spacing_validation_rejects_negative():
    """
    Test that negative max_block_spacing_ms values are rejected with warning.
    """
    params = make_test_params(chain_id=1)
    
    full_params_dict = {
        "networks": {
            "animica:1": {
                "monetary": {
                    "issuance": {
                        "target_block_interval_ms": 300000,
                        "max_block_spacing_ms": 3600000,
                    }
                }
            }
        }
    }
    
    # Set negative value via environment variable
    old_val = os.environ.get("ANIMICA_MAX_BLOCK_SPACING_MS")
    try:
        os.environ["ANIMICA_MAX_BLOCK_SPACING_MS"] = "-1000"
        importer = BlockImporter(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            params=params,
            full_params_dict=full_params_dict,
        )
        
        # Verify negative value is corrected to 0
        assert importer._max_block_spacing_ms == 0
    finally:
        if old_val is None:
            os.environ.pop("ANIMICA_MAX_BLOCK_SPACING_MS", None)
        else:
            os.environ["ANIMICA_MAX_BLOCK_SPACING_MS"] = old_val


def test_max_block_spacing_validation_in_timestamp_sanity():
    """
    Test that _timestamp_sanity rejects blocks exceeding max spacing.
    """
    params = make_test_params(chain_id=1)
    
    full_params_dict = {
        "networks": {
            "animica:1": {
                "monetary": {
                    "issuance": {
                        "max_block_spacing_ms": 3600000,  # 1 hour
                    }
                }
            }
        }
    }
    
    importer = BlockImporter(
        block_db=MockBlockDB(),
        state_db=MockStateDB(),
        params=params,
        full_params_dict=full_params_dict,
    )
    
    # Create mock headers
    class MockHeader:
        def __init__(self, height):
            self.height = height
    
    parent_header = MockHeader(100)
    header = MockHeader(101)
    
    # Test case 1: Block within max spacing (59 minutes) - should be accepted
    payload_good = {"timestamp": 4540}  # 59 minutes after parent (1000 + 3540)
    
    # Mock the _timestamp_of function to return timestamps
    def mock_timestamp_of(hdr, payload=None):
        if payload and "timestamp" in payload:
            return payload["timestamp"]
        return 1000  # parent timestamp
    
    # Temporarily replace _timestamp_of
    import core.chain.block_import as bi_module
    original_timestamp_of = bi_module._timestamp_of
    bi_module._timestamp_of = mock_timestamp_of
    
    try:
        result = importer._timestamp_sanity(header, parent_header, payload_good)
        assert result is None  # Should accept
        
        # Test case 2: Block exactly at max spacing (60 minutes) - should be accepted
        payload_exact = {"timestamp": 4600}  # Exactly 60 minutes later (1000 + 3600)
        
        result = importer._timestamp_sanity(header, parent_header, payload_exact)
        assert result is None  # Should accept
        
        # Test case 3: Block exceeding max spacing (61 minutes) - should be rejected
        payload_too_long = {"timestamp": 4660}  # 61 minutes later (1000 + 3660)
        
        result = importer._timestamp_sanity(header, parent_header, payload_too_long)
        assert result == "timestamp spacing too long"  # Should reject
    finally:
        bi_module._timestamp_of = original_timestamp_of


def test_max_block_spacing_disabled_when_zero():
    """
    Test that max spacing validation is skipped when set to 0.
    """
    params = make_test_params(chain_id=1)
    
    full_params_dict = {
        "networks": {
            "animica:1": {
                "monetary": {
                    "issuance": {
                        "max_block_spacing_ms": 0,  # Disabled
                    }
                }
            }
        }
    }
    
    importer = BlockImporter(
        block_db=MockBlockDB(),
        state_db=MockStateDB(),
        params=params,
        full_params_dict=full_params_dict,
    )
    
    # Create mock headers
    class MockHeader:
        def __init__(self, height):
            self.height = height
    
    parent_header = MockHeader(100)
    header = MockHeader(101)
    
    # Test with very long spacing (10 hours) - should be accepted when disabled
    payload = {"timestamp": 37000}  # 10 hours later (1000 + 36000)
    
    # Mock the _timestamp_of function
    def mock_timestamp_of(hdr, payload_arg=None):
        if payload_arg and "timestamp" in payload_arg:
            return payload_arg["timestamp"]
        return 1000  # parent timestamp
    
    import core.chain.block_import as bi_module
    original_timestamp_of = bi_module._timestamp_of
    bi_module._timestamp_of = mock_timestamp_of
    
    try:
        result = importer._timestamp_sanity(header, parent_header, payload)
        assert result is None  # Should accept (max spacing disabled)
    finally:
        bi_module._timestamp_of = original_timestamp_of


def test_max_block_spacing_from_defaults():
    """
    Test that max_block_spacing_ms is read from defaults section when not in network config.
    """
    params = make_test_params(chain_id=999)  # Unknown chain
    
    full_params_dict = {
        "networks": {
            "animica:1": {
                "monetary": {
                    "issuance": {
                        # Network-specific config doesn't include our chain
                    }
                }
            }
        },
        "defaults": {
            "issuance": {
                "max_block_spacing_ms": 7200000,  # 2 hours default
            }
        }
    }
    
    importer = BlockImporter(
        block_db=MockBlockDB(),
        state_db=MockStateDB(),
        params=params,
        full_params_dict=full_params_dict,
    )
    
    # Verify it reads from defaults
    assert importer._max_block_spacing_ms == 7200000
