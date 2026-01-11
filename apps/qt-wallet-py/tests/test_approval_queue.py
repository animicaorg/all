"""Tests for the approval queue system."""
import json
import tempfile
import time
from pathlib import Path

import pytest

from animica_qt_wallet.walletd.approval_queue import ApprovalQueue


def test_create_request():
    """Test creating an approval request."""
    queue = ApprovalQueue()
    
    request_id = queue.create_request(
        method="wallet_requestAccounts",
        params={"test": "data"},
        requester_info={"process_name": "test_app", "pid": 12345}
    )
    
    assert request_id is not None
    assert len(request_id) > 0
    
    req = queue.get_request(request_id)
    assert req is not None
    assert req.method == "wallet_requestAccounts"
    assert req.params == {"test": "data"}
    assert req.status == "pending"


def test_list_pending():
    """Test listing pending requests."""
    queue = ApprovalQueue()
    
    req1 = queue.create_request("method1", {}, {"pid": 1})
    req2 = queue.create_request("method2", {}, {"pid": 2})
    
    pending = queue.list_pending()
    assert len(pending) == 2
    assert any(r.request_id == req1 for r in pending)
    assert any(r.request_id == req2 for r in pending)


def test_approve_request():
    """Test approving a request."""
    queue = ApprovalQueue()
    
    request_id = queue.create_request("test_method", {}, {"pid": 1})
    queue.approve(request_id, {"result": "success"})
    
    req = queue.get_request(request_id)
    assert req.status == "approved"
    assert req.response == {"result": "success"}


def test_deny_request():
    """Test denying a request."""
    queue = ApprovalQueue()
    
    request_id = queue.create_request("test_method", {}, {"pid": 1})
    queue.deny(request_id, "User rejected")
    
    req = queue.get_request(request_id)
    assert req.status == "denied"
    assert req.error == "User rejected"


def test_expire_old_requests():
    """Test that old pending requests are expired."""
    queue = ApprovalQueue()
    
    request_id = queue.create_request("test_method", {}, {"pid": 1})
    
    # Manually set created_at to the past
    req = queue.get_request(request_id)
    req.created_at = time.time() - 400  # 400 seconds ago
    
    # List with max_age of 300 seconds should expire it
    pending = queue.list_pending(max_age_seconds=300)
    assert len(pending) == 0
    
    req = queue.get_request(request_id)
    assert req.status == "expired"


def test_cleanup_old():
    """Test cleanup of old completed requests."""
    queue = ApprovalQueue()
    
    req1 = queue.create_request("method1", {}, {"pid": 1})
    req2 = queue.create_request("method2", {}, {"pid": 2})
    
    # Approve one, deny the other
    queue.approve(req1, {})
    queue.deny(req2, "test")
    
    # Set created_at to the past
    queue.get_request(req1).created_at = time.time() - 4000
    queue.get_request(req2).created_at = time.time() - 4000
    
    # Cleanup with max_age of 3600 seconds
    removed = queue.cleanup_old(max_age_seconds=3600)
    assert removed == 2


def test_max_pending_limit():
    """Test that max pending limit is enforced."""
    queue = ApprovalQueue(max_pending=2)
    
    queue.create_request("method1", {}, {"pid": 1})
    queue.create_request("method2", {}, {"pid": 2})
    
    with pytest.raises(RuntimeError, match="Too many pending requests"):
        queue.create_request("method3", {}, {"pid": 3})


def test_persistence():
    """Test that queue state is persisted and loaded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        persistence_path = Path(tmpdir) / "queue.json"
        
        # Create queue and add requests
        queue1 = ApprovalQueue(persistence_path=persistence_path)
        req_id = queue1.create_request("test_method", {"key": "value"}, {"pid": 123})
        queue1.approve(req_id, {"result": "data"})
        
        # Create new queue instance - should load from file
        queue2 = ApprovalQueue(persistence_path=persistence_path)
        req = queue2.get_request(req_id)
        
        assert req is not None
        assert req.method == "test_method"
        assert req.params == {"key": "value"}
        assert req.status == "approved"
        assert req.response == {"result": "data"}


def test_invalid_operations():
    """Test invalid operations on requests."""
    queue = ApprovalQueue()
    
    # Approve non-existent request
    with pytest.raises(ValueError, match="not found"):
        queue.approve("invalid-id", {})
    
    # Deny non-existent request
    with pytest.raises(ValueError, match="not found"):
        queue.deny("invalid-id", "reason")
    
    # Approve already-approved request
    req_id = queue.create_request("test", {}, {"pid": 1})
    queue.approve(req_id, {})
    
    with pytest.raises(ValueError, match="not pending"):
        queue.approve(req_id, {})
