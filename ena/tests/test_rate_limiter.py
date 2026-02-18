"""Tests for rate limiter."""

import time
import pytest
from ena.services.ena_node.rate_limiter import RateLimiter


class TestRateLimiter:
    """Test rate limiting functionality."""
    
    def test_allow_within_limit(self):
        """Test requests within limit are allowed."""
        limiter = RateLimiter(
            requests_per_hour_address=10,
            requests_per_hour_ip=20,
        )
        
        # First request should be allowed
        assert limiter.check_address("addr1") is True
        assert limiter.check_ip("1.2.3.4") is True
    
    def test_block_over_limit(self):
        """Test requests over limit are blocked."""
        limiter = RateLimiter(
            requests_per_hour_address=2,
            requests_per_hour_ip=2,
        )
        
        # First two requests allowed
        assert limiter.check_address("addr1") is True
        assert limiter.check_address("addr1") is True
        
        # Third request blocked
        assert limiter.check_address("addr1") is False
    
    def test_separate_limits_per_address(self):
        """Test limits are separate per address."""
        limiter = RateLimiter(
            requests_per_hour_address=1,
            requests_per_hour_ip=10,
        )
        
        assert limiter.check_address("addr1") is True
        assert limiter.check_address("addr2") is True
        
        # addr1 should be blocked
        assert limiter.check_address("addr1") is False
        # addr2 should still be allowed
        assert limiter.check_address("addr2") is False  # now blocked too
    
    def test_combined_check(self):
        """Test combined address + IP check."""
        limiter = RateLimiter(
            requests_per_hour_address=2,
            requests_per_hour_ip=1,
        )
        
        # First request allowed
        assert limiter.check("addr1", "1.2.3.4") is True
        
        # Second request blocked by IP limit
        assert limiter.check("addr1", "1.2.3.4") is False
    
    def test_get_remaining(self):
        """Test getting remaining tokens."""
        limiter = RateLimiter(requests_per_hour_address=5)
        
        remaining = limiter.get_remaining("addr1")
        assert remaining == 5
        
        limiter.check_address("addr1")
        remaining = limiter.get_remaining("addr1")
        assert remaining == 4
