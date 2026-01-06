"""Test that subprocess can run mining.cli.miner with constructed PYTHONPATH."""
import os
import subprocess
import sys
from pathlib import Path


def test_subprocess_with_pythonpath():
    """Test that we can run mining.cli.miner in a subprocess with proper PYTHONPATH."""
    
    # Find repository root
    current_file = Path(__file__).resolve()
    repo_root = current_file.parent.parent.parent
    
    print(f"Repository root: {repo_root}")
    print(f"Mining module path: {repo_root / 'mining'}")
    
    # Verify mining module exists
    if not (repo_root / "mining" / "__init__.py").exists():
        print("✗ Mining module not found at expected location")
        return False
    print("✓ Mining module found")
    
    # Construct PYTHONPATH
    current_pythonpath = os.environ.get('PYTHONPATH', '')
    if current_pythonpath:
        pythonpath = f"{repo_root}{os.pathsep}{current_pythonpath}"
    else:
        pythonpath = str(repo_root)
    
    print(f"PYTHONPATH for subprocess: {pythonpath}")
    
    # Create minimal environment
    minimal_env = {
        'PATH': os.environ.get('PATH', ''),
        'HOME': os.environ.get('HOME', ''),
        'USER': os.environ.get('USER', ''),
        'PYTHONPATH': pythonpath,
    }
    
    # Try to run the mining CLI help command
    cmd = [sys.executable, "-m", "mining.cli.miner", "--help"]
    
    print(f"Running command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            env=minimal_env,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print("✓ Mining CLI executed successfully")
            print(f"Output preview: {result.stdout[:200]}...")
            return True
        else:
            print(f"✗ Mining CLI failed with return code {result.returncode}")
            print(f"stderr: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("✗ Command timed out")
        return False
    except Exception as e:
        print(f"✗ Error running command: {e}")
        return False


if __name__ == "__main__":
    print("Testing subprocess execution of mining.cli.miner...")
    print()
    
    success = test_subprocess_with_pythonpath()
    print()
    
    if success:
        print("✓ Subprocess test passed!")
        sys.exit(0)
    else:
        print("✗ Subprocess test failed")
        sys.exit(1)
