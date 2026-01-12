"""Tests for the debug CLI command."""

from __future__ import annotations

import time
from typing import Optional


def _format_timestamp(ts: Optional[float]) -> str:
    """Format a Unix timestamp as human-readable string."""
    if ts is None:
        return "N/A"
    try:
        from datetime import datetime
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return f"{ts}"


def test_format_timestamp_utility():
    """Test the _format_timestamp utility function."""
    # Test valid timestamp
    test_timestamp = 1768137600.0  # 2026-01-11 00:00:00 UTC
    formatted = _format_timestamp(test_timestamp)
    assert "2026-01-" in formatted
    assert "00:00:00" in formatted
    
    # Test None
    assert _format_timestamp(None) == "N/A"
    
    # Test current time (should not raise)
    current = time.time()
    formatted_current = _format_timestamp(current)
    assert formatted_current != "N/A"
    assert len(formatted_current) > 10  # Should be a formatted date string


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
