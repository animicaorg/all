"""Tests for miner runner lifecycle."""

import time
import pytest

from animica_miner_gui.backend.miner_runner import (
    MinerRunner,
    MinerStatus,
    EventType,
    MiningEvent,
)


def test_miner_runner_initial_state():
    """Test miner runner initial state."""
    runner = MinerRunner()
    
    assert runner.status == MinerStatus.STOPPED
    assert not runner.is_running()


def test_miner_runner_start_stop():
    """Test starting and stopping the miner."""
    runner = MinerRunner()
    
    # Start mining
    config = {"test": "config"}
    assert runner.start(config) is True
    
    # Give it a moment to start
    time.sleep(1.0)
    
    assert runner.is_running()
    
    # Stop mining
    assert runner.stop() is True
    assert not runner.is_running()


def test_miner_runner_events():
    """Test event emission and callbacks."""
    runner = MinerRunner()
    events = []
    
    def callback(event: MiningEvent):
        events.append(event)
    
    runner.add_event_callback(callback)
    
    # Start mining
    config = {}
    runner.start(config)
    
    # Wait for some events
    time.sleep(3.0)
    
    # Stop mining
    runner.stop()
    
    # Check that we received events
    assert len(events) > 0
    
    # Check for status change event
    status_events = [e for e in events if e.event_type == EventType.STATUS_CHANGE]
    assert len(status_events) > 0
    
    # Check for hashrate update event
    hashrate_events = [e for e in events if e.event_type == EventType.HASHRATE_UPDATE]
    assert len(hashrate_events) > 0


def test_miner_runner_remove_callback():
    """Test removing event callbacks."""
    runner = MinerRunner()
    events = []
    
    def callback(event: MiningEvent):
        events.append(event)
    
    runner.add_event_callback(callback)
    runner.remove_event_callback(callback)
    
    # Start and stop
    runner.start({})
    time.sleep(0.5)
    runner.stop()
    
    # Should not have received any events after removal
    assert len(events) == 0


def test_miner_runner_double_start():
    """Test that double start is handled gracefully."""
    runner = MinerRunner()
    
    assert runner.start({}) is True
    assert runner.start({}) is False  # Should fail
    
    runner.stop()


def test_miner_runner_stats():
    """Test getting mining statistics."""
    runner = MinerRunner()
    
    # Initial stats
    stats = runner.get_stats()
    assert stats['status'] == 'stopped'
    assert stats['hashrate'] == 0.0
    assert stats['blocks'] == 0
    
    # Start mining
    runner.start({})
    time.sleep(2.5)
    
    # Check stats while running
    stats = runner.get_stats()
    assert stats['status'] == 'running'
    assert stats['uptime_seconds'] > 0
    
    runner.stop()


def test_mining_event_serialization():
    """Test mining event serialization."""
    event = MiningEvent(
        event_type=EventType.HASHRATE_UPDATE,
        timestamp=time.time(),
        data={"hashrate": 1000000, "unit": "H/s"}
    )
    
    event_dict = event.to_dict()
    
    assert event_dict['event_type'] == 'hashrate_update'
    assert 'timestamp' in event_dict
    assert event_dict['data']['hashrate'] == 1000000


def test_pythonpath_detection():
    """Test that mining module can be located for subprocess."""
    import os
    import sys
    from pathlib import Path
    
    # Simulate the PYTHONPATH detection logic from miner_runner._run_miner_thread
    repo_root = None
    
    # First, check if mining module is already importable
    try:
        import mining
        mining_path = Path(mining.__file__).parent.parent.resolve()
        if (mining_path / "mining").is_dir():
            repo_root = str(mining_path)
    except ImportError:
        pass
    
    # If not found via import, try common relative paths from this file
    if not repo_root:
        # This test is at: apps/miner-gui/animica_miner_gui/tests/test_miner_runner.py
        # Go up 5 levels: tests -> animica_miner_gui -> miner-gui -> apps -> repository root
        # Same calculation as miner_runner.py (5 .parent calls)
        test_file = Path(__file__).resolve()
        potential_root = test_file.parent.parent.parent.parent.parent
        if (potential_root / "mining" / "__init__.py").is_file():
            repo_root = str(potential_root)
    
    # Should have found the repo root one way or another
    assert repo_root is not None, "Could not locate repository root"
    
    # Verify mining module exists at the located path
    mining_init = Path(repo_root) / "mining" / "__init__.py"
    mining_cli = Path(repo_root) / "mining" / "cli" / "miner.py"
    
    assert mining_init.exists(), f"mining/__init__.py not found at {mining_init}"
    assert mining_cli.exists(), f"mining/cli/miner.py not found at {mining_cli}"
    
    # Verify PYTHONPATH would be constructed correctly
    current_pythonpath = os.environ.get('PYTHONPATH', '')
    if current_pythonpath:
        pythonpath = f"{repo_root}{os.pathsep}{current_pythonpath}"
    else:
        pythonpath = repo_root
    
    assert repo_root in pythonpath, "Repository root should be in PYTHONPATH"


def test_mining_cli_executable_with_pythonpath():
    """Test that mining.cli.miner can be executed as a subprocess with proper PYTHONPATH."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    
    # Find repository root using same logic as miner_runner
    repo_root = None
    
    try:
        import mining
        mining_path = Path(mining.__file__).parent.parent.resolve()
        if (mining_path / "mining").is_dir():
            repo_root = str(mining_path)
    except ImportError:
        pass
    
    if not repo_root:
        test_file = Path(__file__).resolve()
        # Same path traversal as in miner_runner.py: 5 levels up
        potential_root = test_file.parent.parent.parent.parent.parent
        if (potential_root / "mining" / "__init__.py").is_file():
            repo_root = str(potential_root)
    
    # Skip test if we can't find the repo root
    if not repo_root:
        pytest.skip("Could not locate repository root")
    
    # Try to run mining CLI help command
    cmd = [sys.executable, "-m", "mining.cli.miner", "--help"]
    
    current_pythonpath = os.environ.get('PYTHONPATH', '')
    if current_pythonpath:
        pythonpath = f"{repo_root}{os.pathsep}{current_pythonpath}"
    else:
        pythonpath = repo_root
    
    minimal_env = {
        'PATH': os.environ.get('PATH', ''),
        'HOME': os.environ.get('HOME', ''),
        'USER': os.environ.get('USER', ''),
        'PYTHONPATH': pythonpath,
    }
    
    try:
        result = subprocess.run(
            cmd,
            env=minimal_env,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Should execute successfully
        assert result.returncode == 0, f"Mining CLI failed: {result.stderr}"
        assert "Animica built-in miner" in result.stdout or "miner" in result.stdout.lower()
        
    except subprocess.TimeoutExpired:
        pytest.fail("Mining CLI command timed out")
    except Exception as e:
        pytest.fail(f"Error running mining CLI: {e}")
