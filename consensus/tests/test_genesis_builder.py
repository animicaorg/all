"""
Test genesis builder determinism and hash verification.
"""

import subprocess
import sys
from pathlib import Path

import pytest


def test_genesis_builder_determinism():
    """
    Test that running the genesis builder multiple times produces the same hash.
    """
    repo_root = Path(__file__).parent.parent.parent
    builder_script = repo_root / "consensus" / "build_genesis.py"
    
    assert builder_script.exists(), "Genesis builder script not found"
    
    # Run builder twice
    result1 = subprocess.run(
        [sys.executable, str(builder_script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result1.returncode == 0, f"Builder failed: {result1.stderr}"
    
    result2 = subprocess.run(
        [sys.executable, str(builder_script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 0, f"Builder failed: {result2.stderr}"
    
    # Extract genesis hash from both runs
    def extract_genesis_hash(output: str) -> str:
        for line in output.split("\n"):
            if "Genesis hash:" in line:
                return line.split("Genesis hash:")[1].strip()
        raise ValueError("Genesis hash not found in output")
    
    hash1 = extract_genesis_hash(result1.stdout)
    hash2 = extract_genesis_hash(result2.stdout)
    
    assert hash1 == hash2, f"Genesis hashes don't match: {hash1} != {hash2}"
    assert hash1.startswith("0x"), "Genesis hash should be hex"
    assert len(hash1) == 66, f"Genesis hash should be 66 chars (0x + 64 hex), got {len(hash1)}"


def test_genesis_hash_matches_committed():
    """
    Test that the genesis builder produces the hash committed in consensus.params.
    """
    from consensus import params as consensus_params
    
    repo_root = Path(__file__).parent.parent.parent
    builder_script = repo_root / "consensus" / "build_genesis.py"
    
    # Run builder with --verify flag
    result = subprocess.run(
        [sys.executable, str(builder_script), "--verify"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    
    # Should exit 0 if match, non-zero if mismatch
    assert result.returncode == 0, (
        f"Genesis hash mismatch! Output:\n{result.stdout}\n{result.stderr}"
    )
    
    # Also check output contains success message
    assert "MATCH" in result.stdout, "Expected MATCH message in output"


def test_genesis_includes_target_block_time():
    """
    Test that genesis output includes target_block_time_sec = 300.
    """
    import json
    from consensus import params as consensus_params
    
    repo_root = Path(__file__).parent.parent.parent
    genesis_output = repo_root / "consensus" / "genesis_output.json"
    
    # Run builder to ensure output exists
    builder_script = repo_root / "consensus" / "build_genesis.py"
    result = subprocess.run(
        [sys.executable, str(builder_script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    
    # Load and check output
    assert genesis_output.exists(), "Genesis output file not created"
    
    with open(genesis_output) as f:
        data = json.load(f)
    
    # Check inputs include target block time
    assert "inputs" in data
    assert data["inputs"]["target_block_time_sec"] == 300.0, (
        "Target block time should be 300 seconds (5 minutes)"
    )
    
    # Check consensus params include target block time
    assert "consensus_params" in data
    assert data["consensus_params"]["target_block_time_sec"] == 300.0
    
    # Check it matches the constant
    assert data["inputs"]["target_block_time_sec"] == consensus_params.TARGET_BLOCK_TIME_SEC


def test_genesis_fork_id_derivation():
    """
    Test that fork_id is derived from genesis_hash deterministically.
    """
    import json
    import zlib
    
    repo_root = Path(__file__).parent.parent.parent
    genesis_output = repo_root / "consensus" / "genesis_output.json"
    
    assert genesis_output.exists(), "Genesis output file not found"
    
    with open(genesis_output) as f:
        data = json.load(f)
    
    genesis_hash_hex = data["outputs"]["genesis_hash"]
    genesis_hash = bytes.fromhex(genesis_hash_hex[2:])
    
    # Recompute fork_id
    expected_fork_id = zlib.crc32(genesis_hash) & 0xFFFFFFFF
    actual_fork_id = data["identity"]["fork_id"]
    
    assert actual_fork_id == expected_fork_id, (
        f"Fork ID mismatch: expected {expected_fork_id:08x}, got {actual_fork_id:08x}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
