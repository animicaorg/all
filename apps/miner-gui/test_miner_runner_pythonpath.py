"""Test that miner_runner properly sets up PYTHONPATH for mining module."""
import os
import sys
from pathlib import Path

# Add apps/miner-gui to path so we can import the module
sys.path.insert(0, str(Path(__file__).parent))

from animica_miner_gui.backend.miner_runner import MinerRunner


def test_pythonpath_setup():
    """Test that the miner runner can locate the mining module."""
    # Simulate the PYTHONPATH detection logic from miner_runner.py
    
    # Find the repository root
    repo_root = None
    
    # First, check if mining module is already importable
    try:
        import mining
        mining_path = Path(mining.__file__).parent.parent.resolve()
        if (mining_path / "mining").is_dir():
            repo_root = str(mining_path)
            print(f"✓ Found mining module at: {repo_root}")
    except ImportError:
        print("✗ Mining module not importable via current PYTHONPATH")
    
    # If not found via import, try common relative paths
    if not repo_root:
        current_file = Path(__file__).resolve()
        print(f"  Trying to find repo root from: {current_file}")
        # This test file is at: apps/miner-gui/test_miner_runner_pythonpath.py
        # Repository root is 3 levels up (file -> miner-gui -> apps -> root)
        potential_root = current_file.parent.parent.parent
        print(f"  Checking potential root: {potential_root}")
        print(f"  Looking for: {potential_root / 'mining' / '__init__.py'}")
        if (potential_root / "mining" / "__init__.py").is_file():
            repo_root = str(potential_root)
            print(f"✓ Found repository root at: {repo_root}")
        else:
            print(f"  Not found at {potential_root}")
    
    if not repo_root:
        print("✗ Could not locate repository root")
        return False
    
    # Verify mining module exists at the located path
    mining_init = Path(repo_root) / "mining" / "__init__.py"
    mining_cli = Path(repo_root) / "mining" / "cli" / "miner.py"
    
    if not mining_init.exists():
        print(f"✗ mining/__init__.py not found at {mining_init}")
        return False
    print(f"✓ Found mining/__init__.py")
    
    if not mining_cli.exists():
        print(f"✗ mining/cli/miner.py not found at {mining_cli}")
        return False
    print(f"✓ Found mining/cli/miner.py")
    
    # Test that we can import mining with the repo root in path
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    
    try:
        import mining.cli.miner
        print(f"✓ Successfully imported mining.cli.miner")
        return True
    except ImportError as e:
        print(f"✗ Failed to import mining.cli.miner: {e}")
        return False


if __name__ == "__main__":
    print("Testing PYTHONPATH setup for mining module...")
    print(f"Current directory: {Path.cwd()}")
    print(f"Script location: {Path(__file__).resolve()}")
    print(f"Current sys.path: {sys.path[:3]}")
    print()
    
    success = test_pythonpath_setup()
    print()
    
    if success:
        print("✓ All checks passed!")
        sys.exit(0)
    else:
        print("✗ Some checks failed")
        sys.exit(1)
