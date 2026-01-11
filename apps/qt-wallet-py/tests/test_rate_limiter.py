"""Tests for the rate limiter."""
import time

from animica_qt_wallet.walletd.rate_limiter import RateLimiter


def test_rate_limiter_allows_initial_requests():
    """Test that initial requests are allowed."""
    limiter = RateLimiter(requests_per_minute=10, burst_size=5)
    
    allowed, reason = limiter.check_rate_limit("client1")
    assert allowed is True
    assert reason == ""


def test_rate_limiter_burst_limit():
    """Test that burst limit is enforced."""
    limiter = RateLimiter(requests_per_minute=10, burst_size=3)
    
    # First 3 requests should be allowed
    for _ in range(3):
        allowed, reason = limiter.check_rate_limit("client1")
        assert allowed is True
    
    # 4th request should be blocked
    allowed, reason = limiter.check_rate_limit("client1")
    assert allowed is False
    assert "burst" in reason.lower()


def test_rate_limiter_window_cleanup():
    """Test that old timestamps are cleaned up."""
    limiter = RateLimiter(requests_per_minute=10, burst_size=3)
    
    # Make 3 requests
    for _ in range(3):
        limiter.check_rate_limit("client1")
    
    # Manually clear the window to simulate time passing
    limiter._windows["client1"].clear()
    
    # Should be able to make requests again
    allowed, reason = limiter.check_rate_limit("client1")
    assert allowed is True


def test_rate_limiter_per_client():
    """Test that rate limiting is per-client."""
    limiter = RateLimiter(requests_per_minute=10, burst_size=2)
    
    # Client1 uses up its burst
    for _ in range(2):
        limiter.check_rate_limit("client1")
    
    # Client1 should be blocked
    allowed, _ = limiter.check_rate_limit("client1")
    assert allowed is False
    
    # Client2 should still be allowed
    allowed, _ = limiter.check_rate_limit("client2")
    assert allowed is True


def test_rate_limiter_reset():
    """Test resetting rate limit for a client."""
    limiter = RateLimiter(requests_per_minute=10, burst_size=2)
    
    # Use up burst
    for _ in range(2):
        limiter.check_rate_limit("client1")
    
    # Should be blocked
    allowed, _ = limiter.check_rate_limit("client1")
    assert allowed is False
    
    # Reset
    limiter.reset("client1")
    
    # Should be allowed again
    allowed, _ = limiter.check_rate_limit("client1")
    assert allowed is True


def test_rate_limiter_clear_all():
    """Test clearing all rate limit data."""
    limiter = RateLimiter(requests_per_minute=10, burst_size=2)
    
    # Multiple clients use their bursts
    for _ in range(2):
        limiter.check_rate_limit("client1")
        limiter.check_rate_limit("client2")
    
    # Clear all
    limiter.clear_all()
    
    # Both should be allowed again
    allowed, _ = limiter.check_rate_limit("client1")
    assert allowed is True
    allowed, _ = limiter.check_rate_limit("client2")
    assert allowed is True
