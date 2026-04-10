"""
State management for Animica CLI.

Provides persistent storage for CLI configuration settings like the active network.
State is stored in ~/.config/animica/state.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from animica.cli.paths import ensure_file_dir

DEFAULT_STATE_DIR = Path.home() / ".config" / "animica"
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "state.json"


class CLIState:
    """Manages persistent CLI state."""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or DEFAULT_STATE_FILE
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load state from disk if it exists."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                # If file is corrupted or unreadable, start fresh
                import sys
                print(f"Warning: CLI state file corrupted ({self.state_file}): {e}", file=sys.stderr)
                print("Starting with fresh state.", file=sys.stderr)
                self._data = {}

    def _save(self) -> None:
        """Save state to disk."""
        # Ensure directory exists (non-sensitive state)
        ensure_file_dir(self.state_file, sensitive=False)
        with open(self.state_file, "w") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from state."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in state and persist to disk."""
        self._data[key] = value
        self._save()

    def delete(self, key: str) -> None:
        """Delete a value from state."""
        if key in self._data:
            del self._data[key]
            self._save()

    def all(self) -> Dict[str, Any]:
        """Get all state data."""
        return self._data.copy()


def get_cli_state() -> CLIState:
    """Get the global CLI state instance."""
    return CLIState()


__all__ = ["CLIState", "get_cli_state", "DEFAULT_STATE_DIR", "DEFAULT_STATE_FILE"]
