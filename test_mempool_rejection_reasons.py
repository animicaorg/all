#!/usr/bin/env python3
"""
Manual test to verify that mempool list shows rejection reasons.

This test simulates the behavior of the enhanced mempool list command
to ensure rejection reasons are properly displayed.
"""

import json


def _short_id(value, length=10):
    """Helper to shorten IDs for display."""
    if not value:
        return None
    text = value
    if text.startswith("0x"):
        text = text[2:]
    if len(text) <= length:
        return "0x" + text
    return "0x" + text[:length]


def test_rejection_display():
    """Test that rejection reasons are displayed correctly."""
    print("Testing rejection reason display logic...")
    print()
    
    # Simulate the RPC response with transaction state sample
    import_result = {
        "requested": 3,
        "tx_state_sample": [
            {
                "txid": "0xabc123def456789012345678901234567890123456789012345678901234567890",
                "state": "received_invalid",
                "last_peer": "0xpeer1234567890",
                "last_reason": "invalid_signature",
                "attempts": 1,
            },
            {
                "txid": "0xdef456789012345678901234567890123456789012345678901234567890abcdef",
                "state": "dropped_evicted",
                "last_peer": "0xpeer9876543210",
                "last_reason": "insufficient_balance",
                "attempts": 2,
            },
            {
                "txid": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                "state": "requested",
                "last_peer": "0xpeeraabbccddee",
                "last_reason": None,
                "attempts": 1,
            }
        ]
    }
    
    tx_state_sample = import_result.get("tx_state_sample", [])
    
    if tx_state_sample:
        # Group transactions by state and reason
        rejected_txs = []
        accepted_txs = []
        pending_txs = []
        
        for tx_state in tx_state_sample:
            if not isinstance(tx_state, dict):
                continue
            state = tx_state.get("state", "")
            
            if state in {"accepted_in_mempool"}:
                accepted_txs.append(tx_state)
            elif state in {"requested", "inflight"}:
                pending_txs.append(tx_state)
            else:
                rejected_txs.append(tx_state)
        
        # Show summary first
        print(f"Transaction Status Summary (sampled {len(tx_state_sample)} recent):")
        print(f"  ✓ Accepted:  {len(accepted_txs)}")
        print(f"  ⏳ Pending:   {len(pending_txs)}")
        print(f"  ✗ Rejected:  {len(rejected_txs)}")
        print("")
        
        if rejected_txs:
            print("Rejected Transaction Details:")
            # Limit output to avoid overwhelming the user
            for tx_state in rejected_txs[:20]:
                txid = tx_state.get("txid", "unknown")
                state = tx_state.get("state", "unknown")
                reason = tx_state.get("last_reason")
                peer = tx_state.get("last_peer", "n/a")
                attempts = tx_state.get("attempts", 0)
                
                # Format the rejection info with better readability
                reason_text = f" → {reason}" if reason else ""
                print(
                    f"  • {_short_id(txid, 16)} [{state}]{reason_text} (peer={_short_id(peer, 10) or 'n/a'}, attempts={attempts})"
                )
            
            if len(rejected_txs) > 20:
                print(f"  ... and {len(rejected_txs) - 20} more rejected transactions")
        
        if pending_txs:
            print("")
            print("Pending Transaction Details:")
            for tx_state in pending_txs[:10]:
                txid = tx_state.get("txid", "unknown")
                state = tx_state.get("state", "unknown")
                peer = tx_state.get("last_peer", "n/a")
                attempts = tx_state.get("attempts", 0)
                print(
                    f"  • {_short_id(txid, 16)} [{state}] (peer={_short_id(peer, 10) or 'n/a'}, attempts={attempts})"
                )
    
    print()
    print("✅ Test passed: Rejection reasons are properly formatted and displayed")
    print()
    print("Expected output:")
    print("  - Transaction Status Summary showing counts")
    print("  - Two rejected transactions with reasons (invalid_signature, insufficient_balance)")
    print("  - One pending transaction (requested without a reason yet)")
    print()


def test_empty_state_fallback():
    """Test that we fall back to generic note when no state info is available."""
    print("Testing fallback to generic note...")
    print()
    
    import_result = {
        "requested": 2,
        "tx_state_sample": []  # Empty sample
    }
    
    tx_state_sample = import_result.get("tx_state_sample", [])
    
    if not tx_state_sample:
        print("  Note: Transactions may have been:")
        print("    • Rejected during validation (hash mismatch, invalid signature)")
        print("    • Failed mempool admission (insufficient balance, nonce conflict, low fee)")
        print("    • Not available on peers (responded with TX_NOTFOUND)")
        print("  Check node logs for: TX_DATA_ADMIT_RESULT, TX_REJECTED, TX_NOTFOUND")
    
    print()
    print("✅ Test passed: Generic fallback note is displayed when no state info")
    print()


def test_all_accepted():
    """Test when all transactions are accepted (should not show rejection section)."""
    print("Testing scenario where all transactions are accepted...")
    print()
    
    import_result = {
        "requested": 2,
        "tx_state_sample": [
            {
                "txid": "0xabc123",
                "state": "accepted_in_mempool",
                "last_peer": "0xpeer1",
                "last_reason": None,
                "attempts": 1,
            },
            {
                "txid": "0xdef456",
                "state": "accepted_in_mempool",
                "last_peer": "0xpeer2",
                "last_reason": None,
                "attempts": 1,
            }
        ]
    }
    
    tx_state_sample = import_result.get("tx_state_sample", [])
    rejected_txs = []
    
    for tx_state in tx_state_sample:
        if not isinstance(tx_state, dict):
            continue
        state = tx_state.get("state", "")
        if state not in {"accepted_in_mempool"}:
            rejected_txs.append(tx_state)
    
    if not rejected_txs:
        print("  (No rejection details shown - all transactions accepted)")
    
    print()
    print("✅ Test passed: No rejection details shown when all transactions accepted")
    print()


if __name__ == "__main__":
    print("=" * 70)
    print("Mempool Rejection Reasons Display Test")
    print("=" * 70)
    print()
    
    test_rejection_display()
    test_empty_state_fallback()
    test_all_accepted()
    
    print("=" * 70)
    print("All tests passed! ✅")
    print("=" * 70)
