"""
Usage tracking stores for billing.

Provides in-memory and file-backed stores for tracking API usage,
rate limiting, and billing metrics.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class UsageRecord:
    """
    Record of API usage for a specific API key.
    
    Attributes:
        api_key: The API key
        plan: Plan name (e.g., "free", "pro")
        requests_count: Total number of requests
        da_bytes_posted: Total DA bytes posted
        rpc_calls: Total RPC calls made
        aicf_units_used: Total AICF resource units consumed
        last_request_time: Unix timestamp of last request
        window_start: Unix timestamp of current rate limit window start
        window_requests: Requests in current window
    """
    
    api_key: str
    plan: str
    requests_count: int = 0
    da_bytes_posted: int = 0
    rpc_calls: int = 0
    aicf_units_used: int = 0
    last_request_time: float = 0.0
    window_start: float = 0.0
    window_requests: int = 0


class UsageStore:
    """
    In-memory usage tracking store.
    
    Thread-safe store for tracking API usage metrics and rate limiting.
    """
    
    def __init__(self):
        """Initialize in-memory usage store."""
        self._lock = threading.RLock()
        self._records: Dict[str, UsageRecord] = {}
    
    def get_record(self, api_key: str, plan: str = "free") -> UsageRecord:
        """
        Get or create usage record for an API key.
        
        Args:
            api_key: The API key
            plan: Plan name (default "free")
            
        Returns:
            UsageRecord for the API key
        """
        with self._lock:
            if api_key not in self._records:
                self._records[api_key] = UsageRecord(api_key=api_key, plan=plan)
            return self._records[api_key]
    
    def increment_requests(self, api_key: str, count: int = 1) -> None:
        """
        Increment request counter for an API key.
        
        Args:
            api_key: The API key
            count: Number of requests to add (default 1)
        """
        with self._lock:
            record = self.get_record(api_key)
            record.requests_count += count
            record.last_request_time = time.time()
    
    def increment_da_bytes(self, api_key: str, bytes_count: int) -> None:
        """
        Increment DA bytes counter for an API key.
        
        Args:
            api_key: The API key
            bytes_count: Number of bytes to add
        """
        with self._lock:
            record = self.get_record(api_key)
            record.da_bytes_posted += bytes_count
    
    def increment_rpc_calls(self, api_key: str, count: int = 1) -> None:
        """
        Increment RPC calls counter for an API key.
        
        Args:
            api_key: The API key
            count: Number of calls to add (default 1)
        """
        with self._lock:
            record = self.get_record(api_key)
            record.rpc_calls += count
    
    def increment_aicf_units(self, api_key: str, units: int) -> None:
        """
        Increment AICF units counter for an API key.
        
        Args:
            api_key: The API key
            units: Number of units to add
        """
        with self._lock:
            record = self.get_record(api_key)
            record.aicf_units_used += units
    
    def check_rate_limit(
        self, api_key: str, limit_rpm: int, window_seconds: float = 60.0
    ) -> tuple[bool, int]:
        """
        Check if API key is within rate limit.
        
        Args:
            api_key: The API key
            limit_rpm: Requests per minute limit
            window_seconds: Window size in seconds (default 60)
            
        Returns:
            Tuple of (allowed, requests_in_window)
        """
        with self._lock:
            record = self.get_record(api_key)
            now = time.time()
            
            # Reset window if expired
            if now - record.window_start >= window_seconds:
                record.window_start = now
                record.window_requests = 0
            
            # Check limit
            if record.window_requests >= limit_rpm:
                return (False, record.window_requests)
            
            # Increment window counter
            record.window_requests += 1
            return (True, record.window_requests)
    
    def get_all_records(self) -> Dict[str, UsageRecord]:
        """
        Get all usage records.
        
        Returns:
            Dictionary mapping API keys to usage records
        """
        with self._lock:
            return dict(self._records)
    
    def clear(self) -> None:
        """Clear all usage records."""
        with self._lock:
            self._records.clear()


class FileBackedUsageStore(UsageStore):
    """
    File-backed usage tracking store.
    
    Extends in-memory store with periodic persistence to disk.
    """
    
    def __init__(self, file_path: str | Path, auto_save_interval: float = 60.0):
        """
        Initialize file-backed usage store.
        
        Args:
            file_path: Path to JSON file for persistence
            auto_save_interval: Auto-save interval in seconds (default 60)
        """
        super().__init__()
        self.file_path = Path(file_path)
        self.auto_save_interval = auto_save_interval
        self._last_save_time = 0.0
        
        # Load existing data
        self._load()
    
    def _load(self) -> None:
        """Load usage records from file."""
        if not self.file_path.exists():
            return
        
        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
            
            with self._lock:
                for key, record_data in data.items():
                    self._records[key] = UsageRecord(**record_data)
        except Exception as e:
            # Log error but don't fail - start with empty store
            print(f"Warning: Could not load usage store from {self.file_path}: {e}")
    
    def save(self) -> None:
        """Save usage records to file."""
        with self._lock:
            data = {key: asdict(record) for key, record in self._records.items()}
        
        # Ensure parent directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write atomically via temp file
        temp_path = self.file_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w") as f:
                json.dump(data, f, indent=2)
            temp_path.replace(self.file_path)
            self._last_save_time = time.time()
        except Exception as e:
            print(f"Warning: Could not save usage store to {self.file_path}: {e}")
            if temp_path.exists():
                temp_path.unlink()
    
    def _auto_save(self) -> None:
        """Auto-save if interval has elapsed."""
        now = time.time()
        if now - self._last_save_time >= self.auto_save_interval:
            self.save()
    
    def increment_requests(self, api_key: str, count: int = 1) -> None:
        """Increment request counter and auto-save."""
        super().increment_requests(api_key, count)
        self._auto_save()
    
    def increment_da_bytes(self, api_key: str, bytes_count: int) -> None:
        """Increment DA bytes counter and auto-save."""
        super().increment_da_bytes(api_key, bytes_count)
        self._auto_save()
    
    def increment_rpc_calls(self, api_key: str, count: int = 1) -> None:
        """Increment RPC calls counter and auto-save."""
        super().increment_rpc_calls(api_key, count)
        self._auto_save()
    
    def increment_aicf_units(self, api_key: str, units: int) -> None:
        """Increment AICF units counter and auto-save."""
        super().increment_aicf_units(api_key, units)
        self._auto_save()


__all__ = [
    "UsageRecord",
    "UsageStore",
    "FileBackedUsageStore",
]
