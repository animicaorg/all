"""
Test that dropped transactions can be retried immediately.

This verifies the fix for the issue where dropped transactions couldn't be
re-requested because next_retry_at was not reset, leaving them stuck with the
old cooldown period.
"""
import time
from p2p.txrelay import TxRequestManager


def test_dropped_tx_can_be_retried():
    """Test that a dropped transaction can be retried immediately."""
    
    mgr = TxRequestManager(cooldown_s=3.5, cap=1000)
    txid = b'\x01' * 32
    now = time.time()
    
    # Mark transaction as requested
    mgr.mark_requested(txid, peer="peer1", now=now)
    
    # Should not be able to request immediately (cooldown)
    assert not mgr.can_request(txid, now=now)
    assert not mgr.can_request(txid, now=now + 1.0)
    
    # But should be able to request after cooldown
    assert mgr.can_request(txid, now=now + 4.0)
    
    # Now mark as dropped
    mgr.mark_dropped(txid, peer="peer1", reason="fetch_timeout", now=now + 5.0)
    
    # KEY FIX: Should be able to request immediately after being dropped
    # Before the fix, next_retry_at would still be set to the old cooldown time
    # After the fix, next_retry_at is reset to now, allowing immediate retry
    assert mgr.can_request(txid, now=now + 5.0), \
        "Dropped transaction should be immediately retryable"
    
    # Verify state
    state = mgr.get_state(txid)
    assert state is not None
    assert state.state == "dropped_evicted"
    assert state.next_retry_at == now + 5.0  # Should be reset to drop time


def test_dropped_tx_state_transitions():
    """Test various state transitions with dropped transactions."""
    
    mgr = TxRequestManager(cooldown_s=3.5, cap=1000)
    txid = b'\x02' * 32
    now = time.time()
    
    # Request -> Dropped -> Request again
    mgr.mark_requested(txid, peer="peer1", now=now)
    assert not mgr.can_request(txid, now=now)
    
    mgr.mark_dropped(txid, peer="peer1", reason="fetch_timeout", now=now + 10.0)
    assert mgr.can_request(txid, now=now + 10.0)
    
    # Should be able to request from a different peer
    mgr.mark_requested(txid, peer="peer2", now=now + 10.0)
    assert not mgr.can_request(txid, now=now + 10.0)
    
    # If dropped again, should be immediately retryable again
    mgr.mark_dropped(txid, peer="peer2", reason="validation_failed", now=now + 20.0)
    assert mgr.can_request(txid, now=now + 20.0)


def test_dropped_tx_different_reasons():
    """Test that dropped transactions with various reasons can be retried."""
    
    mgr = TxRequestManager(cooldown_s=3.5, cap=1000)
    now = time.time()
    
    reasons = ["fetch_timeout", "validation_failed", "evicted", "rejected"]
    
    for i, reason in enumerate(reasons):
        txid = bytes([i] * 32)
        
        # Request and drop with specific reason
        mgr.mark_requested(txid, peer="peer1", now=now)
        mgr.mark_dropped(txid, peer="peer1", reason=reason, now=now + 1.0)
        
        # Should be retryable immediately regardless of drop reason
        assert mgr.can_request(txid, now=now + 1.0), \
            f"Transaction dropped with reason '{reason}' should be immediately retryable"
        
        state = mgr.get_state(txid)
        assert state.state == "dropped_evicted"
        assert state.last_reason == reason


if __name__ == "__main__":
    test_dropped_tx_can_be_retried()
    test_dropped_tx_state_transitions()
    test_dropped_tx_different_reasons()
    print("✅ All tests passed!")
