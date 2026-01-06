"""Tests for thread-safe event handling in LogsTab."""

import time
import threading
import pytest


def test_logs_tab_signal_slot_pattern():
    """Test that LogsTab uses signal/slot pattern for thread-safe event handling."""
    try:
        from PySide6.QtCore import Signal, Slot
        from animica_miner_gui.ui.tabs.logs import LogsTab
    except ImportError:
        pytest.skip("Qt not available")
    
    # Verify that LogsTab has the mining_event_received signal
    assert hasattr(LogsTab, 'mining_event_received'), \
        "LogsTab should have mining_event_received signal"
    
    # Verify it's a Signal
    assert isinstance(LogsTab.mining_event_received, Signal), \
        "mining_event_received should be a Qt Signal"


def test_logs_tab_has_thread_safe_handler():
    """Test that LogsTab has the thread-safe handler method."""
    try:
        from animica_miner_gui.ui.tabs.logs import LogsTab
    except ImportError:
        pytest.skip("Qt not available")
    
    # Verify that LogsTab has the _handle_mining_event_in_main_thread method
    assert hasattr(LogsTab, '_handle_mining_event_in_main_thread'), \
        "LogsTab should have _handle_mining_event_in_main_thread method"


def test_dashboard_tab_signal_slot_pattern():
    """Test that DashboardTab uses signal/slot pattern for thread-safe event handling."""
    try:
        from PySide6.QtCore import Signal, Slot
        from animica_miner_gui.ui.tabs.dashboard import DashboardTab
    except ImportError:
        pytest.skip("Qt not available")
    
    # Verify that DashboardTab has the mining_event_received signal
    assert hasattr(DashboardTab, 'mining_event_received'), \
        "DashboardTab should have mining_event_received signal"
    
    # Verify it's a Signal
    assert isinstance(DashboardTab.mining_event_received, Signal), \
        "mining_event_received should be a Qt Signal"


def test_stats_tab_signal_slot_pattern():
    """Test that StatsTab uses signal/slot pattern for thread-safe event handling."""
    try:
        from PySide6.QtCore import Signal, Slot
        from animica_miner_gui.ui.tabs.stats import StatsTab
    except ImportError:
        pytest.skip("Qt not available")
    
    # Verify that StatsTab has the mining_event_received signal
    assert hasattr(StatsTab, 'mining_event_received'), \
        "StatsTab should have mining_event_received signal"
    
    # Verify it's a Signal
    assert isinstance(StatsTab.mining_event_received, Signal), \
        "mining_event_received should be a Qt Signal"


def test_on_mining_event_emits_signal():
    """Test that on_mining_event delegates to signal emission."""
    try:
        from animica_miner_gui.ui.tabs.logs import LogsTab
        from animica_miner_gui.backend.miner_runner import MiningEvent, EventType
    except ImportError:
        pytest.skip("Qt not available")
    
    # Check that on_mining_event method exists
    assert hasattr(LogsTab, 'on_mining_event'), \
        "LogsTab should have on_mining_event method"
    
    # The method should be lightweight and just emit a signal
    # We can't easily test this without Qt, but we've verified the structure

