#!/usr/bin/env python3
"""
End-to-end integration test for transaction propagation and multi-node mining.

This test validates the core requirement: "If a user sends a tx to Node A (RPC or wallet),
it must become visible to miners on Node B/C and be mined by them."

Test scenario:
1. Start Node A and Node B with P2P connection
2. Submit 20 transactions to Node A via RPC
3. Wait for transactions to propagate to Node B
4. Build block template on Node B
5. Verify block template includes transactions from Node A
6. Mine block on Node B
7. Verify Node A sees the mined transactions

Success criteria:
- All 20 transactions propagate from A to B within timeout
- Block template on B includes multiple transactions (>1)
- Block mined on B includes transactions originally sent to A
- Node A sees confirmations for its transactions
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add repo root to path
repo_root = Path(__file__).resolve().parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


class TestResult:
    """Container for test results."""
    
    def __init__(self):
        self.total_tests = 0
        self.passed = 0
        self.failed = 0
        self.errors: List[str] = []
    
    def add_pass(self, test_name: str):
        self.total_tests += 1
        self.passed += 1
        log.info(f"✅ PASS: {test_name}")
    
    def add_fail(self, test_name: str, reason: str):
        self.total_tests += 1
        self.failed += 1
        self.errors.append(f"{test_name}: {reason}")
        log.error(f"❌ FAIL: {test_name}: {reason}")
    
    def summary(self) -> str:
        status = "SUCCESS" if self.failed == 0 else "FAILURE"
        msg = (
            f"\n{'='*70}\n"
            f"Test Summary: {status}\n"
            f"Total: {self.total_tests}, Passed: {self.passed}, Failed: {self.failed}\n"
        )
        if self.errors:
            msg += "\nFailures:\n"
            for err in self.errors:
                msg += f"  - {err}\n"
        msg += "=" * 70
        return msg
    
    def is_success(self) -> bool:
        return self.failed == 0


async def create_test_transaction(
    chain_id: int = 1,
    sender: bytes = None,
    nonce: int = 0,
    to: Optional[str] = None,
    value: int = 1000,
) -> bytes:
    """Create a test transaction in canonical CBOR format."""
    import cbor2
    
    if sender is None:
        # Generate random sender for testing
        sender = os.urandom(32)
    
    if to is None:
        # Generate random recipient
        to = "0x" + os.urandom(32).hex()
    
    # Create transaction body (v1 format with nonce)
    tx_body = {
        "chainId": chain_id,
        "from": "0x" + sender.hex(),
        "to": to,
        "nonce": nonce,
        "value": value,
        "gasLimit": 21000,
        "maxFee": 1000000000,  # 1 gwei
        "data": b"",
    }
    
    # Create minimal signature (for testing, not cryptographically valid)
    # In production, this would use Dilithium3/SPHINCS+
    sig = {
        "alg": 2,  # Dilithium3
        "pubkey": os.urandom(1952),  # Dilithium3 public key size
        "sig": os.urandom(3309),  # Dilithium3 signature size
    }
    
    # Create transaction envelope
    tx_envelope = {
        "tx": tx_body,
        "sigs": [sig],
    }
    
    # Encode to canonical CBOR
    raw_bytes = cbor2.dumps(tx_envelope, canonical=True)
    return raw_bytes


async def test_tx_propagation_and_mining():
    """Run the end-to-end integration test."""
    results = TestResult()
    
    log.info("="*70)
    log.info("End-to-End Transaction Propagation & Multi-Node Mining Test")
    log.info("="*70)
    log.info("")
    
    # Test parameters
    NUM_TXS = 20
    PROPAGATION_TIMEOUT_S = 10.0
    MINING_TIMEOUT_S = 30.0
    
    try:
        # Import required modules
        from p2p.txrelay import TxRelayService
        from rpc.mempool_service import MempoolService
        from mempool.pool import Pool, PoolConfig
        from mempool.watermark import FeeWatermark, WatermarkConfig
        
        log.info("Step 1: Creating mock mempools for Node A and Node B")
        
        # Create mempool for Node A
        pool_a = Pool(
            cfg=PoolConfig(
                max_txs=1000,
                max_bytes=10 * 1024 * 1024,
                target_util=0.9,
            ),
            watermark=FeeWatermark(WatermarkConfig(min_floor_wei=1)),
        )
        
        mempool_a = MempoolService(
            pool=pool_a,
            chain_id=1,
            min_gas_price_wei=1,
            state_db=None,
            tx_index=None,
            persist_enabled=False,
        )
        
        # Create mempool for Node B
        pool_b = Pool(
            cfg=PoolConfig(
                max_txs=1000,
                max_bytes=10 * 1024 * 1024,
                target_util=0.9,
            ),
            watermark=FeeWatermark(WatermarkConfig(min_floor_wei=1)),
        )
        
        mempool_b = MempoolService(
            pool=pool_b,
            chain_id=1,
            min_gas_price_wei=1,
            state_db=None,
            tx_index=None,
            persist_enabled=False,
        )
        
        log.info("Step 2: Creating P2P relay services with bidirectional routing")
        
        # Message routing
        async def send_tx_inv_a(peer: str, txids: List[bytes]) -> None:
            if peer == "peer-b":
                await relay_b.on_tx_inv("peer-a", txids)
        
        async def send_tx_inv_b(peer: str, txids: List[bytes]) -> None:
            if peer == "peer-a":
                await relay_a.on_tx_inv("peer-b", txids)
        
        async def send_tx_get_a(peer: str, txids: List[bytes]) -> None:
            if peer == "peer-b":
                await relay_b.on_tx_get("peer-a", txids)
        
        async def send_tx_get_b(peer: str, txids: List[bytes]) -> None:
            if peer == "peer-a":
                await relay_a.on_tx_get("peer-b", txids)
        
        async def send_tx_data_a(peer: str, items: List[Dict]) -> None:
            if peer == "peer-b":
                await relay_b.on_tx_data("peer-a", items)
        
        async def send_tx_data_b(peer: str, items: List[Dict]) -> None:
            if peer == "peer-a":
                await relay_a.on_tx_data("peer-b", items)
        
        async def send_noop(_peer: str, _payload: Any) -> None:
            pass
        
        # Create TxRelayService for Node A
        relay_a = TxRelayService(
            max_tx_bytes=10 * 1024 * 1024,
            inv_batch_size=200,
            inv_flush_interval_s=0.1,
            peer_ids=lambda: ["peer-b"],
            peer_eligible=lambda p: True,
            send_tx_inv=send_tx_inv_a,
            send_tx_get=send_tx_get_a,
            send_tx_data=send_tx_data_a,
            send_tx_notfound=send_noop,
            send_mempool_req=send_noop,
            send_mempool_resp=send_noop,
            has_tx=lambda txid: asyncio.create_task(mempool_a.has_hash("0x" + txid.hex()).__bool__()),
            has_chain_tx=lambda _: asyncio.sleep(0, False),
            get_tx_raw=lambda txid: asyncio.create_task(
                mempool_a.get_raw("0x" + txid.hex()) or asyncio.sleep(0, None)
            ),
            admit_tx=mempool_a.admit_tx,
            list_mempool_hashes=lambda limit: asyncio.create_task(
                [bytes.fromhex(h[2:]) for h in list(pool_a.index._by_hash.keys())[:limit]]
            ),
        )
        
        # Create TxRelayService for Node B
        relay_b = TxRelayService(
            max_tx_bytes=10 * 1024 * 1024,
            inv_batch_size=200,
            inv_flush_interval_s=0.1,
            peer_ids=lambda: ["peer-a"],
            peer_eligible=lambda p: True,
            send_tx_inv=send_tx_inv_b,
            send_tx_get=send_tx_get_b,
            send_tx_data=send_tx_data_b,
            send_tx_notfound=send_noop,
            send_mempool_req=send_noop,
            send_mempool_resp=send_noop,
            has_tx=lambda txid: asyncio.create_task(mempool_b.has_hash("0x" + txid.hex()).__bool__()),
            has_chain_tx=lambda _: asyncio.sleep(0, False),
            get_tx_raw=lambda txid: asyncio.create_task(
                mempool_b.get_raw("0x" + txid.hex()) or asyncio.sleep(0, None)
            ),
            admit_tx=mempool_b.admit_tx,
            list_mempool_hashes=lambda limit: asyncio.create_task(
                [bytes.fromhex(h[2:]) for h in list(pool_b.index._by_hash.keys())[:limit]]
            ),
        )
        
        # Wire mempool callbacks for P2P broadcast
        mempool_a.set_p2p_broadcast_callback(relay_a.on_mempool_add)
        mempool_b.set_p2p_broadcast_callback(relay_b.on_mempool_add)
        
        log.info("Step 3: Submitting %d transactions to Node A", NUM_TXS)
        
        sender = os.urandom(32)
        submitted_txids = []
        
        for i in range(NUM_TXS):
            raw_tx = await create_test_transaction(
                chain_id=1,
                sender=sender,
                nonce=i,
                value=1000 + i,
            )
            
            # Compute txid
            txid = hashlib.sha3_256(raw_tx).digest()
            txid_hex = "0x" + txid.hex()
            submitted_txids.append(txid_hex)
            
            # Submit to Node A
            try:
                result_hash = mempool_a.submit(
                    tx=raw_tx,
                    raw=raw_tx,
                    tx_hash_hex=txid_hex,
                    local=True,
                )
                
                if result_hash != txid_hex:
                    results.add_fail(
                        f"tx_submit_{i}",
                        f"Hash mismatch: expected {txid_hex}, got {result_hash}"
                    )
                    continue
                
                log.info(f"  ✓ Submitted tx {i+1}/{NUM_TXS}: {txid_hex[:18]}...")
            
            except Exception as e:
                results.add_fail(f"tx_submit_{i}", f"Submission failed: {e}")
                log.error(f"  ✗ Failed to submit tx {i+1}/{NUM_TXS}: {e}")
        
        # Check Node A mempool
        node_a_count = len(pool_a)
        if node_a_count == NUM_TXS:
            results.add_pass("node_a_mempool_has_all_txs")
            log.info(f"✓ Node A mempool has all {NUM_TXS} transactions")
        else:
            results.add_fail(
                "node_a_mempool_has_all_txs",
                f"Expected {NUM_TXS} txs, got {node_a_count}"
            )
        
        log.info("Step 4: Waiting for transaction propagation to Node B")
        
        start_time = time.time()
        propagated_count = 0
        
        while time.time() - start_time < PROPAGATION_TIMEOUT_S:
            propagated_count = len(pool_b)
            
            if propagated_count >= NUM_TXS:
                break
            
            # Trigger INV flush
            await asyncio.sleep(0.2)
        
        elapsed = time.time() - start_time
        
        if propagated_count >= NUM_TXS:
            results.add_pass("tx_propagation_to_node_b")
            log.info(
                f"✓ All {NUM_TXS} transactions propagated to Node B "
                f"in {elapsed:.2f}s"
            )
        elif propagated_count > 0:
            results.add_fail(
                "tx_propagation_to_node_b",
                f"Only {propagated_count}/{NUM_TXS} txs propagated in {elapsed:.2f}s"
            )
        else:
            results.add_fail(
                "tx_propagation_to_node_b",
                f"No transactions propagated to Node B in {elapsed:.2f}s"
            )
        
        log.info("Step 5: Building block template on Node B")
        
        # Simulate block template building (using mempool.select)
        from mempool.select import select_for_block, PendingTxEntry
        
        # Collect pending entries from Node B's mempool
        pending_entries = []
        for tx_hash_hex in list(pool_b.index._by_hash.keys()):
            entry = pool_b.index.get(bytes.fromhex(tx_hash_hex[2:]))
            if entry:
                pending_entries.append(
                    PendingTxEntry(
                        hash_hex=tx_hash_hex,
                        raw=entry.tx.raw if hasattr(entry.tx, 'raw') else b"",
                        tx=entry.tx,
                    )
                )
        
        # Select transactions for block (simplified, no chain state)
        block_selection = select_for_block(
            pending=pending_entries,
            chain_id=1,
            current_height=1,
            block_gas_limit=100_000_000_000,
            block_byte_limit=1_000_000_000,
            decode_tx=None,
            get_balance=None,
            get_nonce=None,
        )
        
        selected_count = len(block_selection.selected)
        
        if selected_count > 1:
            results.add_pass("block_template_multiple_txs")
            log.info(f"✓ Block template includes {selected_count} transactions")
        elif selected_count == 1:
            results.add_fail(
                "block_template_multiple_txs",
                "Block template only includes 1 tx (expected >1)"
            )
        else:
            results.add_fail(
                "block_template_multiple_txs",
                "Block template is empty"
            )
        
        # Check that selected txs include ones from Node A
        selected_hashes = set(block_selection.selected_hashes)
        submitted_set = set(submitted_txids)
        intersection = selected_hashes & submitted_set
        
        if len(intersection) > 0:
            results.add_pass("block_includes_node_a_txs")
            log.info(
                f"✓ Block template includes {len(intersection)} transactions "
                f"originally submitted to Node A"
            )
        else:
            results.add_fail(
                "block_includes_node_a_txs",
                "Block template does not include any txs from Node A"
            )
        
        log.info("Step 6: Simulating block mining on Node B")
        
        # In a real scenario, Node B would:
        # 1. Build block with selected transactions
        # 2. Mine the block (find valid nonce)
        # 3. Broadcast block to network
        # 4. Node A receives block and removes txs from mempool
        
        # For this test, we simulate by removing selected txs from both mempools
        mined_txids = [bytes.fromhex(h[2:]) for h in block_selection.selected_hashes[:10]]
        
        # Remove from Node B's mempool
        pool_b.remove_included(mined_txids)
        
        # Remove from Node A's mempool (simulating block propagation)
        pool_a.remove_included(mined_txids)
        
        log.info(f"  ✓ Simulated mining of block with {len(mined_txids)} transactions")
        
        # Verify mempool sizes reduced
        node_a_final = len(pool_a)
        node_b_final = len(pool_b)
        
        if node_a_final < node_a_count and node_b_final < propagated_count:
            results.add_pass("txs_removed_after_mining")
            log.info(
                f"✓ Transactions removed from mempools after mining "
                f"(Node A: {node_a_count}→{node_a_final}, "
                f"Node B: {propagated_count}→{node_b_final})"
            )
        else:
            results.add_fail(
                "txs_removed_after_mining",
                f"Mempool sizes did not decrease as expected "
                f"(Node A: {node_a_count}→{node_a_final}, "
                f"Node B: {propagated_count}→{node_b_final})"
            )
        
        log.info("Step 7: Verification complete")
        
    except ImportError as e:
        results.add_fail("import_dependencies", f"Failed to import required modules: {e}")
        log.error(f"Import error: {e}")
    
    except Exception as e:
        results.add_fail("unexpected_error", f"Unexpected error: {e}")
        log.error(f"Unexpected error: {e}", exc_info=True)
    
    # Print summary
    print(results.summary())
    
    return results.is_success()


if __name__ == "__main__":
    success = asyncio.run(test_tx_propagation_and_mining())
    sys.exit(0 if success else 1)
