"""Tests for miner runner lifecycle."""

import time
import pytest

from animica_miner_gui.backend.miner_runner import (
    MinerRunner,
    MinerStatus,
    EventType,
    MiningEvent,
)


def test_miner_runner_initial_state():
    """Test miner runner initial state."""
    runner = MinerRunner()
    
    assert runner.status == MinerStatus.STOPPED
    assert not runner.is_running()


def test_miner_runner_start_stop():
    """Test starting and stopping the miner."""
    runner = MinerRunner()
    
    # Start mining
    config = {"test": "config"}
    assert runner.start(config) is True
    
    # Give it a moment to start
    time.sleep(1.0)
    
    assert runner.is_running()
    
    # Stop mining
    assert runner.stop() is True
    assert not runner.is_running()


def test_miner_runner_events():
    """Test event emission and callbacks."""
    runner = MinerRunner()
    events = []
    
    def callback(event: MiningEvent):
        events.append(event)
    
    runner.add_event_callback(callback)
    
    # Start mining
    config = {}
    runner.start(config)
    
    # Wait for some events
    time.sleep(3.0)
    
    # Stop mining
    runner.stop()
    
    # Check that we received events
    assert len(events) > 0
    
    # Check for status change event
    status_events = [e for e in events if e.event_type == EventType.STATUS_CHANGE]
    assert len(status_events) > 0
    
    # Check for hashrate update event
    hashrate_events = [e for e in events if e.event_type == EventType.HASHRATE_UPDATE]
    assert len(hashrate_events) > 0


def test_miner_runner_remove_callback():
    """Test removing event callbacks."""
    runner = MinerRunner()
    events = []
    
    def callback(event: MiningEvent):
        events.append(event)
    
    runner.add_event_callback(callback)
    runner.remove_event_callback(callback)
    
    # Start and stop
    runner.start({})
    time.sleep(0.5)
    runner.stop()
    
    # Should not have received any events after removal
    assert len(events) == 0


def test_miner_runner_double_start():
    """Test that double start is handled gracefully."""
    runner = MinerRunner()
    
    assert runner.start({}) is True
    assert runner.start({}) is False  # Should fail
    
    runner.stop()


def test_miner_runner_stats():
    """Test getting mining statistics."""
    runner = MinerRunner()
    
    # Initial stats
    stats = runner.get_stats()
    assert stats['status'] == 'stopped'
    assert stats['hashrate'] == 0.0
    assert stats['shares'] == 0
    
    # Start mining
    runner.start({})
    time.sleep(2.5)
    
    # Check stats while running
    stats = runner.get_stats()
    assert stats['status'] == 'running'
    assert stats['uptime_seconds'] > 0
    
    runner.stop()


def test_mining_event_serialization():
    """Test mining event serialization."""
    event = MiningEvent(
        event_type=EventType.HASHRATE_UPDATE,
        timestamp=time.time(),
        data={"hashrate": 1000000, "unit": "H/s"}
    )
    
    event_dict = event.to_dict()
    
    assert event_dict['event_type'] == 'hashrate_update'
    assert 'timestamp' in event_dict
    assert event_dict['data']['hashrate'] == 1000000
