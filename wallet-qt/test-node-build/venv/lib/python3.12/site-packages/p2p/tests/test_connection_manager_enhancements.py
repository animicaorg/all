"""
Test enhancements to ConnectionManager for P2P rewrite.

Tests new features:
- Retry mechanism with backoff
- Peer prioritization
- Enhanced logging
- Connection statistics
- Diagnostic capabilities
"""

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from p2p.peer.connection_manager import (
    CMConfig,
    ConnectionManager,
    DialBackoff,
    PeerSlot,
)


class TestDialBackoff:
    """Test enhanced DialBackoff with retry tracking."""

    def test_initial_state(self):
        """Test initial backoff state."""
        bo = DialBackoff()
        assert bo.failures == 0
        assert bo.next_try_at == 0.0
        assert bo.total_attempts == 0
        assert bo.first_failure_at == 0.0
        assert bo.last_success_at == 0.0

    def test_mark_success(self):
        """Test marking a successful connection."""
        bo = DialBackoff()
        bo.failures = 3
        bo.total_attempts = 5
        bo.first_failure_at = time.time() - 100
        
        bo.mark_success()
        
        assert bo.failures == 0
        assert bo.next_try_at == 0.0
        assert bo.last_success_at > 0
        assert bo.first_failure_at == 0.0

    def test_mark_failure(self):
        """Test marking connection failures with backoff."""
        bo = DialBackoff()
        
        # First failure
        bo.mark_failure(base=2.0, jitter=0.1, max_backoff=60.0)
        assert bo.failures == 1
        assert bo.total_attempts == 1
        assert bo.first_failure_at > 0
        assert bo.next_try_at > time.time()
        
        first_failure_time = bo.first_failure_at
        
        # Second failure
        bo.mark_failure(base=2.0, jitter=0.1, max_backoff=60.0)
        assert bo.failures == 2
        assert bo.total_attempts == 2
        assert bo.first_failure_at == first_failure_time  # Should not change
        assert bo.next_try_at > time.time()

    def test_should_give_up(self):
        """Test retry limit checking."""
        bo = DialBackoff()
        
        assert not bo.should_give_up(max_retries=3)
        
        bo.failures = 2
        assert not bo.should_give_up(max_retries=3)
        
        bo.failures = 3
        assert bo.should_give_up(max_retries=3)
        
        bo.failures = 5
        assert bo.should_give_up(max_retries=3)

    def test_exponential_backoff(self):
        """Test that backoff increases exponentially."""
        bo = DialBackoff()
        
        prev_delay = 0.0
        for i in range(5):
            before = time.time()
            bo.mark_failure(base=2.0, jitter=0.0, max_backoff=1000.0)
            delay = bo.next_try_at - before
            
            # Each delay should be larger than the previous
            if i > 0:
                assert delay > prev_delay
            prev_delay = delay


class TestCMConfig:
    """Test enhanced CMConfig with new parameters."""

    def test_default_config(self):
        """Test default configuration values."""
        cfg = CMConfig()
        
        # Existing defaults
        assert cfg.target_outbound == 16
        assert cfg.max_outbound == 32
        assert cfg.max_inbound == 128
        
        # New P2P rewrite defaults
        assert cfg.max_dial_retries == 5
        assert cfg.priority_boost_stable == 10.0
        assert cfg.priority_boost_recent == 5.0
        assert cfg.stability_threshold_s == 3600.0
        assert cfg.verbosity == 0

    def test_custom_config(self):
        """Test custom configuration."""
        cfg = CMConfig(
            max_dial_retries=10,
            priority_boost_stable=20.0,
            verbosity=2,
        )
        
        assert cfg.max_dial_retries == 10
        assert cfg.priority_boost_stable == 20.0
        assert cfg.verbosity == 2


class TestConnectionManagerPrioritization:
    """Test peer prioritization features."""

    @pytest.fixture
    def mock_transport(self):
        """Create a mock transport."""
        transport = Mock()
        transport.name = Mock(return_value="tcp")
        return transport

    @pytest.fixture
    def mock_addr_book(self):
        """Create a mock address book."""
        addr_book = Mock()
        addr_book.list_recent = Mock(return_value=[])
        addr_book.add = Mock()
        addr_book.mark_seen = Mock()
        addr_book.prune = Mock()
        return addr_book

    @pytest.fixture
    def conn_mgr(self, mock_transport, mock_addr_book):
        """Create a ConnectionManager instance."""
        cfg = CMConfig(verbosity=2)
        return ConnectionManager(mock_transport, mock_addr_book, cfg)

    def test_calculate_peer_priority_new_peer(self, conn_mgr):
        """Test priority calculation for a new peer."""
        priority = conn_mgr._calculate_peer_priority("tcp://example.com:30333")
        
        # New peer should have neutral priority
        assert priority == 0.0

    def test_calculate_peer_priority_with_failures(self, conn_mgr):
        """Test priority calculation with connection failures."""
        address = "tcp://example.com:30333"
        
        # Record some failures
        bo = DialBackoff()
        bo.failures = 3
        conn_mgr._backoff[address] = bo
        
        priority = conn_mgr._calculate_peer_priority(address)
        
        # Priority should be negative due to failures
        assert priority < 0.0

    def test_calculate_peer_priority_with_recent_success(self, conn_mgr):
        """Test priority calculation with recent successful connection."""
        address = "tcp://example.com:30333"
        
        # Record recent success
        bo = DialBackoff()
        bo.last_success_at = time.time() - 100  # 100 seconds ago
        conn_mgr._backoff[address] = bo
        
        priority = conn_mgr._calculate_peer_priority(address)
        
        # Priority should be positive due to recent success
        assert priority > 0.0

    def test_calculate_peer_priority_stable_peer(self, conn_mgr):
        """Test priority calculation for stable long-running peer."""
        address = "tcp://example.com:30333"
        
        # Record long-term success
        bo = DialBackoff()
        bo.last_success_at = time.time() - 7200  # 2 hours ago
        conn_mgr._backoff[address] = bo
        
        priority = conn_mgr._calculate_peer_priority(address)
        
        # Priority should include stability boost
        assert priority >= conn_mgr.cfg.priority_boost_stable

    def test_set_peer_priority(self, conn_mgr):
        """Test manual priority setting."""
        address = "tcp://example.com:30333"
        
        conn_mgr.set_peer_priority(address, 100.0)
        
        assert conn_mgr._peer_priorities[address] == 100.0
        priority = conn_mgr._calculate_peer_priority(address)
        assert priority >= 100.0  # Should include manual priority

    def test_update_connection_stats(self, conn_mgr):
        """Test connection statistics tracking."""
        address = "tcp://example.com:30333"
        
        # Record successful connection
        conn_mgr._update_connection_stats(address, success=True)
        
        stats = conn_mgr._connection_stats[address]
        assert stats["total_connections"] == 1
        assert stats["successful_connections"] == 1
        assert stats["last_success"] > 0
        
        # Record failed connection
        conn_mgr._update_connection_stats(address, success=False)
        
        stats = conn_mgr._connection_stats[address]
        assert stats["total_connections"] == 2
        assert stats["successful_connections"] == 1

    def test_get_peer_diagnostics(self, conn_mgr):
        """Test peer diagnostics retrieval."""
        address = "tcp://example.com:30333"
        
        # Setup some state
        bo = DialBackoff()
        bo.failures = 2
        bo.total_attempts = 5
        conn_mgr._backoff[address] = bo
        
        conn_mgr._update_connection_stats(address, success=True)
        conn_mgr._update_connection_stats(address, success=False)
        
        diag = conn_mgr.get_peer_diagnostics(address)
        
        assert diag["address"] == address
        assert "priority" in diag
        assert diag["backoff"]["failures"] == 2
        assert diag["backoff"]["total_attempts"] == 5
        assert diag["stats"]["total_connections"] == 2
        assert diag["stats"]["successful_connections"] == 1
        assert not diag["is_banned"]
        assert not diag["is_connected"]


class TestConnectionManagerLogging:
    """Test enhanced logging features."""

    @pytest.fixture
    def mock_transport(self):
        """Create a mock transport."""
        transport = Mock()
        transport.name = Mock(return_value="tcp")
        transport.dial = AsyncMock(side_effect=asyncio.TimeoutError("timeout"))
        return transport

    @pytest.fixture
    def mock_addr_book(self):
        """Create a mock address book."""
        addr_book = Mock()
        addr_book.list_recent = Mock(return_value=[])
        addr_book.add = Mock()
        addr_book.mark_seen = Mock()
        return addr_book

    @pytest.mark.asyncio
    async def test_verbose_logging_enabled(self, mock_transport, mock_addr_book):
        """Test that verbose logging is configured correctly."""
        cfg = CMConfig(verbosity=2)
        conn_mgr = ConnectionManager(mock_transport, mock_addr_book, cfg)
        
        # Logger should be configured
        assert conn_mgr._log is not None
        assert conn_mgr.cfg.verbosity == 2

    @pytest.mark.asyncio
    async def test_dial_failure_logging(self, mock_transport, mock_addr_book):
        """Test that dial failures are logged properly."""
        cfg = CMConfig(verbosity=1)
        conn_mgr = ConnectionManager(mock_transport, mock_addr_book, cfg)
        
        address = "tcp://example.com:30333"
        
        # Should log failure
        result = await conn_mgr._dial_one(address)
        
        assert result is None
        assert address in conn_mgr._backoff
        assert conn_mgr._backoff[address].failures > 0


@pytest.mark.asyncio
async def test_connection_manager_retry_limit():
    """Test that connection manager respects max retry limit."""
    # Setup
    transport = Mock()
    transport.name = Mock(return_value="tcp")
    transport.dial = AsyncMock(side_effect=Exception("connection failed"))
    
    addr_book = Mock()
    addr_book.list_recent = Mock(return_value=[])
    addr_book.mark_seen = Mock()
    
    cfg = CMConfig(max_dial_retries=3)
    conn_mgr = ConnectionManager(transport, addr_book, cfg)
    
    address = "tcp://example.com:30333"
    
    # Attempt connection multiple times
    for i in range(5):
        await conn_mgr._dial_one(address)
    
    # Should have given up after max retries
    bo = conn_mgr._backoff[address]
    assert bo.failures >= cfg.max_dial_retries
    assert bo.should_give_up(cfg.max_dial_retries)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
