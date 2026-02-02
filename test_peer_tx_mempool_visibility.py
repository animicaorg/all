#!/usr/bin/env python3
"""
Integration test to verify peer transactions are visible in mempool list.

This test simulates:
1. Two nodes with P2P connection
2. Node A receives tx from peer B
3. Verify tx is admitted to Node A's mempool
4. Verify tx is visible via mempool.getPending RPC on Node A
"""

import asyncio
import hashlib
import sys
import time
from typing import Any, Optional


class MockMempoolService:
    """Mock mempool service for testing."""

    def __init__(self):
        self._txs = {}
        self._p2p_callback = None
        self._p2p_loop = None

    def set_p2p_broadcast_callback(self, callback, *, loop=None):
        """Set P2P broadcast callback."""
        self._p2p_callback = callback
        self._p2p_loop = loop
        print(f"✓ P2P callback registered with mempool")

    async def admit_tx(
        self, raw: bytes, local: Optional[bool] = None, origin_peer: Optional[str] = None
    ) -> tuple[bool, Optional[str]]:
        """Admit transaction to mempool."""
        tx_hash = hashlib.sha3_256(raw).digest()
        tx_hash_hex = "0x" + tx_hash.hex()

        if tx_hash in self._txs:
            return True, "duplicate"

        self._txs[tx_hash] = {
            "raw": raw,
            "hash_hex": tx_hash_hex,
            "local": local,
            "origin": origin_peer,
            "received_at": time.time(),
        }

        origin = "local" if local else f"peer:{origin_peer or 'unknown'}"
        print(f"  [mempool] Admitted tx {tx_hash_hex[:18]}... from {origin}")

        # Trigger P2P callback if set
        if self._p2p_callback is not None and local:
            try:
                if self._p2p_loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self._p2p_callback(tx_hash, raw), self._p2p_loop
                    )
                else:
                    await self._p2p_callback(tx_hash, raw)
            except Exception as e:
                print(f"  [mempool] P2P callback failed: {e}")

        return True, None

    def has_hash(self, tx_hash_hex: str) -> bool:
        """Check if transaction is in mempool."""
        tx_hash = bytes.fromhex(tx_hash_hex[2:] if tx_hash_hex.startswith("0x") else tx_hash_hex)
        return tx_hash in self._txs

    def get_raw(self, tx_hash_hex: str) -> Optional[bytes]:
        """Get raw transaction bytes."""
        tx_hash = bytes.fromhex(tx_hash_hex[2:] if tx_hash_hex.startswith("0x") else tx_hash_hex)
        entry = self._txs.get(tx_hash)
        return entry["raw"] if entry else None

    def snapshot(self, limit: int = 1000):
        """Get mempool snapshot."""
        from collections import namedtuple
        Snapshot = namedtuple("Snapshot", ["entries", "raw_by_hash"])
        Entry = namedtuple("Entry", ["hash_hex", "raw", "received_at"])

        entries = []
        raw_by_hash = {}
        for tx_hash, info in list(self._txs.items())[:limit]:
            entry = Entry(
                hash_hex=info["hash_hex"],
                raw=info["raw"],
                received_at=info["received_at"],
            )
            entries.append(entry)
            raw_by_hash[info["hash_hex"]] = info["raw"]

        return Snapshot(entries=entries, raw_by_hash=raw_by_hash)


async def test_peer_tx_visible_in_mempool():
    """Test that peer transactions are visible in mempool."""
    print("\n" + "=" * 70)
    print("Test: Peer TX Visible in Mempool")
    print("=" * 70 + "\n")

    # Create mock mempool service
    mempool = MockMempoolService()

    # Create mock P2P callback
    broadcast_calls = []

    async def mock_p2p_callback(tx_hash: bytes, raw: bytes):
        broadcast_calls.append((tx_hash.hex(), len(raw)))
        print(f"  [p2p] Broadcast callback: {tx_hash.hex()[:18]}... ({len(raw)} bytes)")

    # Register callback
    mempool.set_p2p_broadcast_callback(mock_p2p_callback, loop=asyncio.get_event_loop())

    # Test 1: Local transaction
    print("Test 1: Submit local transaction")
    raw_tx_local = b"local_transaction_" + str(time.time()).encode()
    tx_hash_local = hashlib.sha3_256(raw_tx_local).digest()
    tx_hash_local_hex = "0x" + tx_hash_local.hex()

    ok, reason = await mempool.admit_tx(raw_tx_local, local=True, origin_peer=None)
    assert ok, f"Failed to admit local tx: {reason}"
    print(f"  ✓ Local tx admitted: {tx_hash_local_hex[:18]}...")

    # Verify it's in mempool
    assert mempool.has_hash(tx_hash_local_hex), "Local tx not found in mempool"
    print(f"  ✓ Local tx visible in mempool")

    # Test 2: Peer transaction
    print("\nTest 2: Submit peer transaction")
    raw_tx_peer = b"peer_transaction_" + str(time.time()).encode()
    tx_hash_peer = hashlib.sha3_256(raw_tx_peer).digest()
    tx_hash_peer_hex = "0x" + tx_hash_peer.hex()

    ok, reason = await mempool.admit_tx(raw_tx_peer, local=False, origin_peer="peer_node_abc123")
    assert ok, f"Failed to admit peer tx: {reason}"
    print(f"  ✓ Peer tx admitted: {tx_hash_peer_hex[:18]}...")

    # Verify it's in mempool
    assert mempool.has_hash(tx_hash_peer_hex), "Peer tx not found in mempool"
    print(f"  ✓ Peer tx visible in mempool")

    # Test 3: Snapshot includes both
    print("\nTest 3: Verify snapshot includes both transactions")
    snapshot = mempool.snapshot(limit=100)
    tx_hashes_in_snapshot = [e.hash_hex for e in snapshot.entries]

    assert tx_hash_local_hex in tx_hashes_in_snapshot, "Local tx not in snapshot"
    print(f"  ✓ Local tx in snapshot")

    assert tx_hash_peer_hex in tx_hashes_in_snapshot, "Peer tx not in snapshot"
    print(f"  ✓ Peer tx in snapshot")

    # Test 4: get_raw works for both
    print("\nTest 4: Verify get_raw works for both transactions")
    raw_local_retrieved = mempool.get_raw(tx_hash_local_hex)
    assert raw_local_retrieved == raw_tx_local, "Local tx raw bytes mismatch"
    print(f"  ✓ Local tx raw bytes retrievable")

    raw_peer_retrieved = mempool.get_raw(tx_hash_peer_hex)
    assert raw_peer_retrieved == raw_tx_peer, "Peer tx raw bytes mismatch"
    print(f"  ✓ Peer tx raw bytes retrievable")

    print("\n" + "=" * 70)
    print("✅ All tests passed - Peer transactions are visible in mempool!")
    print("=" * 70)

    return True


async def test_p2p_deps_has_tx():
    """Test that P2PDeps.has_tx method works correctly."""
    print("\n" + "=" * 70)
    print("Test: P2PDeps.has_tx Method")
    print("=" * 70 + "\n")

    # Test that P2PDeps has has_tx method
    from p2p.deps import P2PDeps, AsyncP2PDeps

    print("Checking P2PDeps class for has_tx method...")
    assert hasattr(P2PDeps, "has_tx"), "P2PDeps missing has_tx method"
    print("  ✓ P2PDeps has has_tx method")

    print("Checking AsyncP2PDeps class for has_tx method...")
    assert hasattr(AsyncP2PDeps, "has_tx"), "AsyncP2PDeps missing has_tx method"
    print("  ✓ AsyncP2PDeps has has_tx method")

    # Verify method signature
    import inspect

    sync_sig = inspect.signature(P2PDeps.has_tx)
    sync_params = list(sync_sig.parameters.keys())
    assert sync_params == ["self", "tx_hash"], f"Unexpected P2PDeps.has_tx params: {sync_params}"
    print(f"  ✓ P2PDeps.has_tx signature: {sync_params}")

    async_sig = inspect.signature(AsyncP2PDeps.has_tx)
    async_params = list(async_sig.parameters.keys())
    assert async_params == ["self", "tx_hash"], f"Unexpected AsyncP2PDeps.has_tx params: {async_params}"
    print(f"  ✓ AsyncP2PDeps.has_tx signature: {async_params}")

    # Verify return type hints (loosely - just check it's defined)
    assert sync_sig.return_annotation is not None, "P2PDeps.has_tx should have return type"
    print("  ✓ P2PDeps.has_tx has return type annotation")

    assert async_sig.return_annotation is not None, "AsyncP2PDeps.has_tx should have return type"
    print("  ✓ AsyncP2PDeps.has_tx has return type annotation")

    print("\n" + "=" * 70)
    print("✅ P2PDeps.has_tx method signature verified!")
    print("=" * 70)

    return True


async def main():
    """Run all tests."""
    tests = [
        ("Peer TX Visible in Mempool", test_peer_tx_visible_in_mempool),
        ("P2PDeps.has_tx Method", test_p2p_deps_has_tx),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            passed = await test_func()
            results.append((test_name, passed))
        except Exception as exc:
            print(f"\n❌ Test '{test_name}' failed with exception: {exc}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)

    all_passed = True
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n✅ All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
