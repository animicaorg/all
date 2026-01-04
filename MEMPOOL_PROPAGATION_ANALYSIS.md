# Mempool Transaction Propagation Analysis

## Problem Statement Review
The issue reported was that transactions only stay in the origin node's mempool and don't propagate to other nodes, even though peers are connected.

## Code Analysis Results

### 1. ChainAdapter.get_tx() ✅ CORRECT
**Location:** `p2p/core_p2p/chain_adapter.py:79-86`

```python
def get_tx(self, tx_hash: bytes) -> Optional[bytes]:
    raw = self.deps.get_tx_raw(tx_hash)  # ← Checks mempool FIRST
    if raw is not None:
        return raw
    tx = self.deps.tx_by_hash(tx_hash)  # ← Then checks blocks
    if tx is None:
        return None
    return bytes(tx.to_cbor())
```

**Status:** Already checks mempool via `deps.get_tx_raw()` before checking blocks.

### 2. ChainAdapter.process_tx() ✅ CORRECT
**Location:** `p2p/core_p2p/chain_adapter.py:98-106`

```python
def process_tx(self, tx: bytes) -> None:
    try:
        decoded = Tx.from_cbor(tx)
    except Exception as exc:
        log.warning("core p2p failed to decode tx payload", exc_info=exc)
        return
    accepted, reason = self.deps.admit_tx(decoded)  # ← Admits to mempool
    if not accepted:
        log.warning("core p2p tx rejected", extra={"reason": reason})
```

**Status:** Already admits transactions to mempool via `deps.admit_tx()`.

### 3. P2PDeps.get_tx_raw() ✅ CORRECT
**Location:** `p2p/deps.py:846-863`

```python
def get_tx_raw(self, tx_hash: bytes) -> Optional[bytes]:
    try:
        from rpc.methods import tx as tx_methods
    except Exception:
        return None

    svc = tx_methods._get_mempool_service()  # ← Get mempool service
    if svc is not None:
        getter = getattr(svc, "get_raw", None)
        if callable(getter):
            raw = getter("0x" + tx_hash.hex())  # ← Check mempool FIRST
            if isinstance(raw, (bytes, bytearray)):
                return bytes(raw)

    try:
        return tx_methods._pending_get("0x" + tx_hash.hex())  # ← Fallback cache
    except Exception:
        return None
```

**Status:** Already checks mempool service first, then falls back to pending cache.

### 4. P2PDeps.admit_tx() ✅ CORRECT
**Location:** `p2p/deps.py:754-844`

- Validates and decodes transaction
- Verifies chain ID
- Verifies PQ signature
- Submits to mempool service via `_mempool_submit()`
- Also adds to fallback pending cache

**Status:** Properly admits transactions to mempool with full validation.

### 5. Mempool Sync on Peer Connect ✅ CORRECT
**Location:** `p2p/node/p2p_service.py:4791-4794`

```python
self._txrelay.register_peer(...)
self._create_child_task(
    self._txrelay.request_mempool_sync(peer_key),
    name=f"p2p.txrelay.mempool_sync@{peer_key}",
)
```

**Status:** Mempool sync is triggered when peers connect.

### 6. TxRelayService ✅ PROPERLY WIRED
**Location:** `p2p/node/p2p_service.py:780-783`

```python
has_tx=self._txrelay_has_tx,
has_chain_tx=self._txrelay_has_chain_tx,
get_tx_raw=self._txrelay_get_tx_raw,
admit_tx=self._txrelay_admit_tx,
```

**Status:** TxRelayService callbacks properly connected to P2PDeps methods.

## Test Results

### Test 1: ChainAdapter.get_tx() checks mempool
**File:** `p2p/tests/test_mempool_propagation.py`
**Result:** ✅ PASS
- Verified that get_tx() returns transactions from mempool
- Confirmed that deps.get_tx_raw() is called correctly

### Test 2: Two-node propagation
**File:** `p2p/tests/test_two_node_tx_propagation_e2e.py`
**Result:** ✅ PASS  
- Transaction submitted to NodeA propagates to NodeB
- NodeB can retrieve transaction bytes
- NodeB has transaction in mempool

### Test 3: Three-node multi-hop
**File:** `p2p/tests/test_two_node_tx_propagation_e2e.py`
**Result:** ✅ PASS
- Transaction propagates through multiple hops (A → B → C)
- All nodes receive and can retrieve the transaction

### Test 4: No duplicate propagation
**File:** `p2p/tests/test_two_node_tx_propagation_e2e.py`
**Result:** ✅ PASS
- Nodes don't create duplicate entries
- Proper deduplication working

## Conclusion

**All suggested fixes from the problem statement are already implemented in the codebase.**

The transaction propagation infrastructure is complete and correct:
1. ✅ Mempool is checked when peers request transactions
2. ✅ Transactions are admitted to mempool when received from peers
3. ✅ Mempool sync triggers on peer connection
4. ✅ TxRelayService properly handles INV/GET/DATA message flow

## Potential Issues in Production

If propagation issues persist in production environments, check:

1. **RPC Module Initialization**
   - Ensure `rpc.methods.tx` module is properly initialized before P2P starts
   - Check that `_get_mempool_service()` returns a valid service

2. **Service Configuration**
   - Verify nodes are using `P2PService` (with TxRelayService), not just `CoreP2PService`
   - Check that both P2P services (`P2PService` and `CoreP2PService`) have access to mempool

3. **Network Configuration**
   - Ensure peers can actually connect (firewall, NAT, etc.)
   - Verify both nodes are on the same chain (genesis hash match)
   - Check that peer discovery is working

4. **Logging**
   - Monitor logs for:
     - "core p2p tx rejected" warnings
     - Import errors when loading rpc.methods.tx
     - Mempool admission failures

5. **Timing**
   - Ensure mempool service is fully initialized before accepting P2P connections
   - Check for race conditions in service startup order

## Recommendations

1. **Monitor existing logs** - The code already logs warnings for rejection reasons
2. **Verify deployment config** - Ensure both P2P services are enabled and properly configured
3. **Test connectivity** - Use p2p.listPeers RPC to verify peers are actually connected
4. **Check mempool state** - Use mempool.list RPC to see if transactions are being admitted locally

## No Code Changes Required

The codebase already contains the correct implementation. If issues persist, they are likely:
- Configuration problems
- Network connectivity issues  
- Service initialization order issues
- Not using the full P2PService with TxRelayService

These should be diagnosed through logging and configuration review rather than code changes.
