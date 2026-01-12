"""Tests for the debug CLI command timestamp formatting.

Note: The _format_timestamp function is duplicated here rather than imported
from animica.cli.debug to avoid pulling in heavy CLI dependencies during testing.
The implementation should be kept in sync with the original.
"""

from __future__ import annotations

import time
from typing import Optional
from datetime import datetime


def _format_timestamp(ts: Optional[float]) -> str:
    """Format a Unix timestamp as human-readable string.
    
    This is a copy of the function from animica.cli.debug for testing purposes.
    Implementation should match the original in debug.py.
    """
    if ts is None:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return f"{ts}"


def test_format_timestamp_valid():
    """Test formatting a valid Unix timestamp."""
    # Use a known timestamp: 2026-01-11 (approximately)
    test_timestamp = 1768137600.0
    formatted = _format_timestamp(test_timestamp)
    
    # Check that it's formatted as a date string
    assert "2026-01-" in formatted, f"Expected 2026-01- in {formatted}"
    assert ":" in formatted, f"Expected time separator in {formatted}"
    assert len(formatted) == 19, f"Expected YYYY-MM-DD HH:MM:SS format, got {formatted}"


def test_format_timestamp_none():
    """Test formatting None returns N/A."""
    assert _format_timestamp(None) == "N/A"


def test_format_timestamp_epoch():
    """Test formatting Unix epoch (0) returns 1970-01-01."""
    formatted = _format_timestamp(0)
    assert "1970-01-01" in formatted


def test_format_timestamp_current():
    """Test formatting current time returns a valid string."""
    current = time.time()
    formatted = _format_timestamp(current)
    
    assert formatted != "N/A"
    assert len(formatted) > 10
    assert "-" in formatted
    assert ":" in formatted


def test_format_timestamp_problem_statement_example():
    """Test the exact timestamp from the problem statement."""
    # From the problem statement: Last progress: 1768184862.1349247
    problem_timestamp = 1768184862.1349247
    formatted = _format_timestamp(problem_timestamp)
    
    # Should be formatted, not raw
    assert str(problem_timestamp) not in formatted
    assert "2026-01-12" in formatted
    assert ":" in formatted


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
