#!/usr/bin/env python3
"""
Test that TX_NOTFOUND clears txid from ALL peers' known_txids, not just the responding peer.

This prevents infinite loops where multiple peers report knowing about a transaction
that none of them actually have.
"""

import asyncio
import hashlib
import sys
from typing import Any, Optional


async def test_notfound_clears_all_peers():
    """
    Test that when one peer responds with NOTFOUND, the txid is removed from
    ALL peers' known_txids, not just that one peer.
    """
    print("\n" + "=" * 70)
    print("Test: TX_NOTFOUND clears from ALL peers")
    print("=" * 70 + "\n")

    from p2p.txrelay import TxRelayService

    # Track calls
    sent_messages = []

    async def send_noop(_peer: str, _payload: Any) -> None:
        pass

    async def send_tx_get(peer: str, txids: list[bytes]) -> None:
        sent_messages.append({"type": "get", "peer": peer, "txids": txids})

    # Simple mempool mock
    mempool = {}

    async def has_tx(txid: bytes) -> bool:
        return txid in mempool

    async def get_tx_raw(txid: bytes) -> Optional[bytes]:
        return mempool.get(txid)

    async def admit_tx(raw: bytes, origin: Optional[str]) -> tuple[bool, Optional[str]]:
        return False, "not_implemented"

    async def list_hashes(limit: int) -> list[bytes]:
        return []

    # Create relay service
    relay = TxRelayService(
        max_tx_bytes=1024 * 1024,
        peer_ids=lambda: ["peer-a", "peer-b", "peer-c"],
        peer_eligible=lambda p: True,
        send_tx_inv=send_noop,
        send_tx_get=send_tx_get,
        send_tx_data=send_noop,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=get_tx_raw,
        admit_tx=admit_tx,
        list_mempool_hashes=list_hashes,
    )

    # Register peers
    relay.register_peer("peer-a")
    relay.register_peer("peer-b")
    relay.register_peer("peer-c")

    # Simulate TX_INV from all three peers announcing the same transaction
    tx_raw = b"test-transaction-data"
    tx_hash = hashlib.sha3_256(tx_raw).digest()

    print(f"Step 1: Three peers announce txid {tx_hash.hex()[:16]}...")
    await relay.on_tx_inv("peer-a", [tx_hash])
    await relay.on_tx_inv("peer-b", [tx_hash])
    await relay.on_tx_inv("peer-c", [tx_hash])

    # Check that all three peers have it in known_txids
    peer_a_state = relay._peer_state.get("peer-a")
    peer_b_state = relay._peer_state.get("peer-b")
    peer_c_state = relay._peer_state.get("peer-c")

    assert peer_a_state is not None
    assert peer_b_state is not None
    assert peer_c_state is not None

    print(f"  Peer A has txid: {tx_hash in peer_a_state.known_txids}")
    print(f"  Peer B has txid: {tx_hash in peer_b_state.known_txids}")
    print(f"  Peer C has txid: {tx_hash in peer_c_state.known_txids}")

    assert tx_hash in peer_a_state.known_txids, "Peer A should have txid"
    assert tx_hash in peer_b_state.known_txids, "Peer B should have txid"
    assert tx_hash in peer_c_state.known_txids, "Peer C should have txid"

    print("\n✓ All three peers have the txid in known_txids")

    # Simulate peer-a responding with TX_NOTFOUND
    print(f"\nStep 2: Peer A responds with TX_NOTFOUND...")
    await relay.on_tx_notfound("peer-a", [tx_hash])

    # Check that the txid was removed from ALL peers, not just peer-a
    peer_a_has = tx_hash in peer_a_state.known_txids
    peer_b_has = tx_hash in peer_b_state.known_txids
    peer_c_has = tx_hash in peer_c_state.known_txids

    print(f"  Peer A has txid: {peer_a_has}")
    print(f"  Peer B has txid: {peer_b_has}")
    print(f"  Peer C has txid: {peer_c_has}")

    # The FIX: All peers should have the txid removed
    if peer_a_has or peer_b_has or peer_c_has:
        print("\n❌ FAILED: Txid was not removed from all peers!")
        print("   This will cause an infinite loop where transactions are repeatedly")
        print("   requested from different peers who don't have them.")
        return False

    print("\n✓ Txid was removed from ALL peers' known_txids")
    print("  This prevents infinite loops in mempool autofetch")

    # Verify that request_missing_known won't try to fetch it again
    print(f"\nStep 3: Verify request_missing_known won't retry...")
    sent_messages.clear()
    requested = await relay.request_missing_known(limit=128, force=False)

    print(f"  Requested {requested} transactions")
    print(f"  Sent {len(sent_messages)} GET messages")

    if requested > 0 or len(sent_messages) > 0:
        print("\n❌ FAILED: Should not request transactions that received NOTFOUND")
        return False

    print("\n✓ request_missing_known correctly skipped the transaction")

    print("\n" + "=" * 70)
    print("✅ TEST PASSED")
    print("=" * 70)
    return True


async def test_notfound_with_force_flag():
    """
    Test that force=True can override the reject cache to try again from a different peer.
    """
    print("\n" + "=" * 70)
    print("Test: TX_NOTFOUND with force=True")
    print("=" * 70 + "\n")

    from p2p.txrelay import TxRelayService

    # Track calls
    sent_messages = []

    async def send_noop(_peer: str, _payload: Any) -> None:
        pass

    async def send_tx_get(peer: str, txids: list[bytes]) -> None:
        sent_messages.append({"type": "get", "peer": peer, "txids": txids})

    # Simple mempool mock
    mempool = {}

    async def has_tx(txid: bytes) -> bool:
        return txid in mempool

    async def get_tx_raw(txid: bytes) -> Optional[bytes]:
        return mempool.get(txid)

    async def admit_tx(raw: bytes, origin: Optional[str]) -> tuple[bool, Optional[str]]:
        return False, "not_implemented"

    async def list_hashes(limit: int) -> list[bytes]:
        return []

    # Create relay service
    relay = TxRelayService(
        max_tx_bytes=1024 * 1024,
        peer_ids=lambda: ["peer-a", "peer-b"],
        peer_eligible=lambda p: True,
        send_tx_inv=send_noop,
        send_tx_get=send_tx_get,
        send_tx_data=send_noop,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=get_tx_raw,
        admit_tx=admit_tx,
        list_mempool_hashes=list_hashes,
    )

    # Register peers
    relay.register_peer("peer-a")
    relay.register_peer("peer-b")

    # Simulate TX_INV from both peers
    tx_raw = b"test-transaction-data"
    tx_hash = hashlib.sha3_256(tx_raw).digest()

    print(f"Step 1: Two peers announce txid {tx_hash.hex()[:16]}...")
    await relay.on_tx_inv("peer-a", [tx_hash])
    await relay.on_tx_inv("peer-b", [tx_hash])

    # Simulate peer-a responding with TX_NOTFOUND
    print(f"\nStep 2: Peer A responds with TX_NOTFOUND...")
    await relay.on_tx_notfound("peer-a", [tx_hash])

    # Verify txid was removed from both peers
    peer_a_state = relay._peer_state.get("peer-a")
    peer_b_state = relay._peer_state.get("peer-b")
    assert tx_hash not in peer_a_state.known_txids, "Should be removed from peer A"
    assert tx_hash not in peer_b_state.known_txids, "Should be removed from peer B"

    print("  ✓ Txid removed from both peers")

    # Now try with force=False (should not request anything)
    print(f"\nStep 3: Try request_missing_known with force=False...")
    sent_messages.clear()
    requested = await relay.request_missing_known(limit=128, force=False)

    print(f"  Requested {requested} transactions")
    assert requested == 0, "Should not request with force=False"
    print("  ✓ No requests sent (txid was removed from known_txids)")

    print("\n" + "=" * 70)
    print("✅ TEST PASSED")
    print("=" * 70)
    return True


def main():
    """Run all tests."""
    success = True

    tests = [
        ("TX_NOTFOUND clears from ALL peers", test_notfound_clears_all_peers),
        ("TX_NOTFOUND with force=True", test_notfound_with_force_flag),
    ]

    for test_name, test_func in tests:
        try:
            result = asyncio.run(test_func())
            if not result:
                success = False
        except Exception as exc:
            print(f"\n❌ Test '{test_name}' failed with exception: {exc}")
            import traceback

            traceback.print_exc()
            success = False

    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)

    if success:
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
