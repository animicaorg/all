"""Tests for CLI state management."""

from __future__ import annotations

import tempfile
from pathlib import Path

from animica.cli.state import CLIState


def test_state_set_and_get() -> None:
    """Test setting and getting state values."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)

        # Set a value
        state.set("test_key", "test_value")

        # Get the value
        assert state.get("test_key") == "test_value"


def test_state_persistence() -> None:
    """Test that state persists across instances."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"

        # Create first instance and set value
        state1 = CLIState(state_file)
        state1.set("network", "mainnet")

        # Create second instance and verify value persists
        state2 = CLIState(state_file)
        assert state2.get("network") == "mainnet"


def test_state_get_default() -> None:
    """Test getting a value with default when key doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)

        # Get non-existent key with default
        assert state.get("nonexistent", "default") == "default"


def test_state_delete() -> None:
    """Test deleting a state value."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)

        # Set a value
        state.set("temp_key", "temp_value")
        assert state.get("temp_key") == "temp_value"

        # Delete the value
        state.delete("temp_key")
        assert state.get("temp_key") is None


def test_state_all() -> None:
    """Test getting all state data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)

        # Set multiple values
        state.set("key1", "value1")
        state.set("key2", "value2")
        state.set("key3", "value3")

        # Get all state
        all_state = state.all()
        assert all_state == {"key1": "value1", "key2": "value2", "key3": "value3"}


def test_state_corrupted_file() -> None:
    """Test that corrupted state file is handled gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"

        # Write invalid JSON
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w") as f:
            f.write("invalid json {]")

        # Should handle gracefully and start fresh
        state = CLIState(state_file)
        assert state.all() == {}


def test_state_file_creation() -> None:
    """Test that state file is created if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "new_dir" / "state.json"

        # File doesn't exist yet
        assert not state_file.exists()

        # Create state and set a value
        state = CLIState(state_file)
        state.set("test", "value")

        # File should now exist
        assert state_file.exists()
