"""Application allowlist manager for external RPC access."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal


@dataclass
class AllowlistEntry:
    """An entry in the application allowlist."""
    app_id: str  # Usually process name or identifier
    allowed: bool
    auto_approve: bool = False  # If True, skip approval dialog
    created_at: float = 0.0
    notes: str = ""


class AppAllowlist:
    """Manage which external applications can access wallet RPC."""

    def __init__(
        self,
        persistence_path: Path | None = None,
        default_policy: Literal["allow", "deny"] = "deny",
    ):
        """
        Initialize allowlist.
        
        Args:
            persistence_path: Path to persist allowlist state
            default_policy: Default policy for unknown apps ("allow" or "deny")
        """
        self._entries: dict[str, AllowlistEntry] = {}
        self._lock = Lock()
        self._persistence_path = persistence_path
        self._default_policy = default_policy
        self._load_state()
    
    def is_allowed(self, app_id: str) -> bool:
        """Check if an app is allowed to make requests."""
        with self._lock:
            entry = self._entries.get(app_id)
            if entry:
                return entry.allowed
            return self._default_policy == "allow"
    
    def should_auto_approve(self, app_id: str) -> bool:
        """Check if requests from this app should be auto-approved."""
        with self._lock:
            entry = self._entries.get(app_id)
            return bool(entry and entry.allowed and entry.auto_approve)
    
    def add_entry(
        self,
        app_id: str,
        allowed: bool = True,
        auto_approve: bool = False,
        notes: str = "",
    ) -> None:
        """Add or update an allowlist entry."""
        import time
        
        with self._lock:
            self._entries[app_id] = AllowlistEntry(
                app_id=app_id,
                allowed=allowed,
                auto_approve=auto_approve,
                created_at=time.time(),
                notes=notes,
            )
            self._persist()
    
    def remove_entry(self, app_id: str) -> bool:
        """Remove an entry from the allowlist. Returns True if removed."""
        with self._lock:
            if app_id in self._entries:
                del self._entries[app_id]
                self._persist()
                return True
            return False
    
    def list_entries(self) -> list[AllowlistEntry]:
        """List all allowlist entries."""
        with self._lock:
            return list(self._entries.values())
    
    def _persist(self) -> None:
        """Persist allowlist to disk (called with lock held)."""
        if not self._persistence_path:
            return
        
        data = {
            "default_policy": self._default_policy,
            "entries": [
                {
                    "app_id": entry.app_id,
                    "allowed": entry.allowed,
                    "auto_approve": entry.auto_approve,
                    "created_at": entry.created_at,
                    "notes": entry.notes,
                }
                for entry in self._entries.values()
            ],
        }
        
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self._persistence_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    def _load_state(self) -> None:
        """Load allowlist from disk."""
        if not self._persistence_path or not self._persistence_path.exists():
            return
        
        try:
            data = json.loads(self._persistence_path.read_text(encoding="utf-8"))
            self._default_policy = data.get("default_policy", "deny")
            
            with self._lock:
                for entry_data in data.get("entries", []):
                    app_id = entry_data["app_id"]
                    self._entries[app_id] = AllowlistEntry(
                        app_id=app_id,
                        allowed=entry_data.get("allowed", True),
                        auto_approve=entry_data.get("auto_approve", False),
                        created_at=entry_data.get("created_at", 0.0),
                        notes=entry_data.get("notes", ""),
                    )
        except Exception:  # noqa: BLE001
            # If we can't load, start fresh
            pass
