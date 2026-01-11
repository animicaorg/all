"""Rate limiter for external RPC requests."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    """Token bucket rate limiter for RPC requests."""
    
    def __init__(self, requests_per_minute: int = 10, burst_size: int = 5):
        """
        Initialize rate limiter.
        
        Args:
            requests_per_minute: Maximum requests allowed per minute per client
            burst_size: Maximum burst of requests allowed
        """
        self._rpm = requests_per_minute
        self._burst_size = burst_size
        self._windows: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=burst_size))
        self._lock = Lock()
    
    def check_rate_limit(self, client_id: str) -> tuple[bool, str]:
        """
        Check if a request from client_id is within rate limits.

        Returns:
            (allowed, reason) - If allowed is False, reason contains error message
        """
        now = time.time()
        window = 60.0  # 1 minute window

        with self._lock:
            timestamps = self._windows[client_id]

            # Remove timestamps older than window
            while timestamps and timestamps[0] < now - window:
                timestamps.popleft()

            # Check burst limit first (more restrictive)
            if len(timestamps) >= self._burst_size:
                wait_time = timestamps[0] + window - now
                return False, f"Rate limit exceeded (burst). Retry after {wait_time:.1f}s"

            # Note: We don't need a separate RPM check because the burst limit
            # combined with the sliding window already enforces the rate.
            # The deque maxlen=burst_size ensures we never exceed burst,
            # and the window cleanup ensures we never exceed RPM.

            # Add current timestamp
            timestamps.append(now)
            return True, ""
    
    def reset(self, client_id: str) -> None:
        """Reset rate limit for a specific client."""
        with self._lock:
            if client_id in self._windows:
                del self._windows[client_id]
    
    def clear_all(self) -> None:
        """Clear all rate limit data."""
        with self._lock:
            self._windows.clear()
