"""Tests for built-in checkpoints."""

import pytest

from p2p.checkpoints import builtin
from p2p.checkpoints.loader import Checkpoint


def test_mainnet_builtin_checkpoint_exists():
    """Test that mainnet has the expected built-in checkpoint."""
    checkpoints = builtin.get_builtin_checkpoints(chain_id=1)
    
    assert len(checkpoints) >= 1, "Mainnet should have at least one built-in checkpoint"
    
    # Find the checkpoint at height 55795
    cp_55795 = next((cp for cp in checkpoints if cp.height == 55795), None)
    
    assert cp_55795 is not None, "Mainnet should have checkpoint at height 55795"
    assert cp_55795.hash == "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"


def test_mainnet_checkpoint_properties():
    """Test properties of mainnet built-in checkpoint."""
    checkpoints = builtin.get_builtin_checkpoints(chain_id=1)
    
    cp = checkpoints[0]
    
    # Verify type
    assert isinstance(cp, Checkpoint)
    
    # Verify height is positive
    assert cp.height > 0
    
    # Verify hash format
    assert cp.hash.startswith("0x")
    assert len(cp.hash) == 66  # 0x + 64 hex chars


def test_testnet_no_builtin_checkpoints():
    """Test that testnet has no built-in checkpoints (as expected)."""
    checkpoints = builtin.get_builtin_checkpoints(chain_id=2)
    
    assert checkpoints == [], "Testnet should have no built-in checkpoints"


def test_devnet_no_builtin_checkpoints():
    """Test that devnet has no built-in checkpoints (as expected)."""
    checkpoints = builtin.get_builtin_checkpoints(chain_id=1337)
    
    assert checkpoints == [], "Devnet should have no built-in checkpoints"


def test_unknown_chain_no_checkpoints():
    """Test that unknown chains have no built-in checkpoints."""
    checkpoints = builtin.get_builtin_checkpoints(chain_id=9999)
    
    assert checkpoints == [], "Unknown chain should have no built-in checkpoints"


def test_has_builtin_checkpoints():
    """Test has_builtin_checkpoints helper."""
    assert builtin.has_builtin_checkpoints(1) is True, "Mainnet should have checkpoints"
    assert builtin.has_builtin_checkpoints(2) is False, "Testnet should have no checkpoints"
    assert builtin.has_builtin_checkpoints(1337) is False, "Devnet should have no checkpoints"
    assert builtin.has_builtin_checkpoints(9999) is False, "Unknown chain should have no checkpoints"


def test_get_all_builtin_checkpoints():
    """Test getting all built-in checkpoints."""
    all_checkpoints = builtin.get_all_builtin_checkpoints()
    
    # Should have mainnet
    assert 1 in all_checkpoints, "Should include mainnet"
    assert len(all_checkpoints[1]) > 0, "Mainnet should have checkpoints"
    
    # Should not have empty entries for testnet/devnet
    if 2 in all_checkpoints:
        assert len(all_checkpoints[2]) > 0, "If testnet is present, it should have checkpoints"
    
    if 1337 in all_checkpoints:
        assert len(all_checkpoints[1337]) > 0, "If devnet is present, it should have checkpoints"


def test_builtin_checkpoints_immutable():
    """Test that built-in checkpoints are returned as copies."""
    checkpoints1 = builtin.get_builtin_checkpoints(chain_id=1)
    checkpoints2 = builtin.get_builtin_checkpoints(chain_id=1)
    
    # Should be equal but not the same object
    assert checkpoints1 == checkpoints2
    assert checkpoints1 is not checkpoints2, "Should return a copy, not the original"


def test_builtin_mainnet_checkpoints_constant():
    """Test that MAINNET_CHECKPOINTS constant contains expected checkpoint."""
    assert len(builtin.MAINNET_CHECKPOINTS) >= 1
    
    # Find the specific checkpoint
    cp_55795 = next((cp for cp in builtin.MAINNET_CHECKPOINTS if cp.height == 55795), None)
    
    assert cp_55795 is not None
    assert cp_55795.height == 55795
    assert cp_55795.hash == "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"


def test_builtin_checkpoints_sorted():
    """Test that built-in checkpoints are sorted by height."""
    checkpoints = builtin.get_builtin_checkpoints(chain_id=1)
    
    if len(checkpoints) > 1:
        for i in range(len(checkpoints) - 1):
            assert checkpoints[i].height < checkpoints[i + 1].height, \
                "Checkpoints should be sorted by height"
