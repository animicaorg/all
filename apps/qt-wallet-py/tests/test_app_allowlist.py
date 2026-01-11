"""Tests for the app allowlist."""
import tempfile
from pathlib import Path

from animica_qt_wallet.walletd.app_allowlist import AppAllowlist


def test_default_deny_policy():
    """Test that default deny policy blocks unknown apps."""
    allowlist = AppAllowlist(default_policy="deny")
    
    assert allowlist.is_allowed("unknown_app") is False


def test_default_allow_policy():
    """Test that default allow policy allows unknown apps."""
    allowlist = AppAllowlist(default_policy="allow")
    
    assert allowlist.is_allowed("unknown_app") is True


def test_add_allowed_entry():
    """Test adding an allowed app."""
    allowlist = AppAllowlist(default_policy="deny")
    
    allowlist.add_entry("trusted_app", allowed=True)
    
    assert allowlist.is_allowed("trusted_app") is True


def test_add_denied_entry():
    """Test adding a denied app."""
    allowlist = AppAllowlist(default_policy="allow")
    
    allowlist.add_entry("blocked_app", allowed=False)
    
    assert allowlist.is_allowed("blocked_app") is False


def test_auto_approve():
    """Test auto-approve functionality."""
    allowlist = AppAllowlist()
    
    # Add app without auto-approve
    allowlist.add_entry("app1", allowed=True, auto_approve=False)
    assert allowlist.should_auto_approve("app1") is False
    
    # Add app with auto-approve
    allowlist.add_entry("app2", allowed=True, auto_approve=True)
    assert allowlist.should_auto_approve("app2") is True


def test_remove_entry():
    """Test removing an allowlist entry."""
    allowlist = AppAllowlist()
    
    allowlist.add_entry("test_app", allowed=True)
    assert allowlist.is_allowed("test_app") is True
    
    removed = allowlist.remove_entry("test_app")
    assert removed is True
    
    # Should fall back to default policy
    assert allowlist.is_allowed("test_app") is False


def test_remove_nonexistent_entry():
    """Test removing a non-existent entry."""
    allowlist = AppAllowlist()
    
    removed = allowlist.remove_entry("nonexistent")
    assert removed is False


def test_list_entries():
    """Test listing all allowlist entries."""
    allowlist = AppAllowlist()
    
    allowlist.add_entry("app1", allowed=True, notes="Test app 1")
    allowlist.add_entry("app2", allowed=False, notes="Test app 2")
    
    entries = allowlist.list_entries()
    assert len(entries) == 2
    
    app_ids = [e.app_id for e in entries]
    assert "app1" in app_ids
    assert "app2" in app_ids


def test_persistence():
    """Test that allowlist is persisted and loaded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        persistence_path = Path(tmpdir) / "allowlist.json"
        
        # Create allowlist and add entries
        allowlist1 = AppAllowlist(persistence_path=persistence_path, default_policy="deny")
        allowlist1.add_entry("app1", allowed=True, auto_approve=True, notes="Test")
        allowlist1.add_entry("app2", allowed=False, notes="Blocked")
        
        # Create new instance - should load from file
        allowlist2 = AppAllowlist(persistence_path=persistence_path)
        
        assert allowlist2.is_allowed("app1") is True
        assert allowlist2.should_auto_approve("app1") is True
        assert allowlist2.is_allowed("app2") is False
        
        entries = allowlist2.list_entries()
        assert len(entries) == 2


def test_update_existing_entry():
    """Test updating an existing entry."""
    allowlist = AppAllowlist()
    
    allowlist.add_entry("app1", allowed=True, auto_approve=False)
    assert allowlist.should_auto_approve("app1") is False
    
    # Update with auto_approve=True
    allowlist.add_entry("app1", allowed=True, auto_approve=True)
    assert allowlist.should_auto_approve("app1") is True
