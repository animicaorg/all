"""
Test that genesis params path resolution works correctly.

Regression test for the bug where relative paths in genesis.json's paramsRef.path
were resolved relative to base_dir instead of repo root, causing mainnet to fail
with "genesis chainId=0 does not match params.chain_id=1" error.

NOTE: Chain IDs per network (as defined in spec/params.yaml and core/genesis/*.json):
- mainnet: 0
- testnet: 2
- devnet: 1337
"""

from pathlib import Path
import json
import sys


def test_mainnet_genesis_loads_correct_params():
    """Test that mainnet genesis loads params with correct chain_id=0."""
    from core.genesis.loader import load_genesis
    
    genesis_path = Path(__file__).resolve().parents[2] / "genesis" / "mainnet.json"
    assert genesis_path.exists(), f"Genesis file not found: {genesis_path}"
    
    # Load genesis without KV to just test params loading
    params, header = load_genesis(genesis_path, kv=None, block_db=None, log=False)
    
    # Verify chain_id is correct
    assert params.chain_id == 0, f"Expected mainnet chain_id=0, got {params.chain_id}"
    assert params.chain_name == "Animica Mainnet"
    assert header.chainId == 0, f"Expected header chainId=0, got {header.chainId}"
    
    # Verify they match
    assert params.chain_id == header.chainId, \
        f"params.chain_id={params.chain_id} != header.chainId={header.chainId}"
    
    print("✓ test_mainnet_genesis_loads_correct_params passed")


def test_testnet_genesis_loads_correct_params():
    """Test that testnet genesis loads params with correct chain_id=2."""
    from core.genesis.loader import load_genesis
    
    genesis_path = Path(__file__).resolve().parents[2] / "genesis" / "testnet.json"
    if not genesis_path.exists():
        print("⊘ test_testnet_genesis_loads_correct_params skipped (genesis not found)")
        return
    
    # Load genesis without KV to just test params loading
    params, header = load_genesis(genesis_path, kv=None, block_db=None, log=False)
    
    # Verify chain_id is correct
    assert params.chain_id == 2, f"Expected testnet chain_id=2, got {params.chain_id}"
    assert header.chainId == 2, f"Expected header chainId=2, got {header.chainId}"
    
    print("✓ test_testnet_genesis_loads_correct_params passed")


def test_devnet_genesis_loads_correct_params():
    """Test that devnet genesis loads params with correct chain_id=1337."""
    from core.genesis.loader import load_genesis
    
    genesis_path = Path(__file__).resolve().parents[2] / "genesis" / "devnet.json"
    if not genesis_path.exists():
        print("⊘ test_devnet_genesis_loads_correct_params skipped (genesis not found)")
        return
    
    # Load genesis without KV to just test params loading
    params, header = load_genesis(genesis_path, kv=None, block_db=None, log=False)
    
    # Verify chain_id is correct
    assert params.chain_id == 1337, f"Expected devnet chain_id=1337, got {params.chain_id}"
    assert header.chainId == 1337, f"Expected header chainId=1337, got {header.chainId}"
    
    print("✓ test_devnet_genesis_loads_correct_params passed")


def test_params_ref_path_is_relative_to_repo_root():
    """
    Test that paramsRef.path in genesis files is resolved relative to repo root,
    not relative to the genesis file location.
    """
    from core.genesis.loader import _load_chain_params
    
    # Create a minimal genesis dict with a relative params path
    genesis = {
        "chainId": 0,
        "paramsRef": {
            "path": "spec/params.yaml"
        }
    }
    
    # Load params - this should resolve "spec/params.yaml" relative to repo root
    params = _load_chain_params(genesis, None, base_dir=Path("/some/other/dir"))
    
    # Verify the params were loaded correctly
    assert params.chain_id == 0, f"Expected chain_id=0 for mainnet params, got {params.chain_id}"
    assert params.chain_name == "Animica Mainnet"
    
    print("✓ test_params_ref_path_is_relative_to_repo_root passed")


if __name__ == "__main__":
    try:
        test_mainnet_genesis_loads_correct_params()
        test_testnet_genesis_loads_correct_params()
        test_devnet_genesis_loads_correct_params()
        test_params_ref_path_is_relative_to_repo_root()
        print("\n✓ All tests passed!")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

