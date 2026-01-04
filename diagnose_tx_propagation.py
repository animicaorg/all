#!/usr/bin/env python3
"""
Transaction Propagation Diagnostic Tool

Usage:
  python3 diagnose_tx_propagation.py [RPC_URL]

Example:
  python3 diagnose_tx_propagation.py http://localhost:8545/rpc
"""

import json
import sys
from typing import Any, Dict, Optional

try:
    import httpx
except ImportError:
    print("Error: httpx module required. Install with: pip install httpx")
    sys.exit(1)


class Colors:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.RESET}\n")


def print_success(text: str):
    print(f"{Colors.GREEN}✓{Colors.RESET} {text}")


def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠{Colors.RESET} {text}")


def print_error(text: str):
    print(f"{Colors.RED}✗{Colors.RESET} {text}")


def print_info(text: str):
    print(f"  {text}")


class RPCClient:
    def __init__(self, url: str):
        self.url = url
        self.client = httpx.Client(timeout=10.0)
    
    def call(self, method: str, params: Any = None) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.post(
                self.url,
                json={
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params or [],
                    "id": 1,
                },
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                print_error(f"RPC error: {data['error'].get('message', 'Unknown')}")
                return None
            return data.get("result")
        except httpx.HTTPError as e:
            print_error(f"HTTP error: {e}")
            return None
        except Exception as e:
            print_error(f"Unexpected error: {e}")
            return None


def check_node_reachable(client: RPCClient) -> bool:
    """Check if node is reachable and responding."""
    print_header("1. Node Connectivity")
    
    result = client.call("eth_chainId")
    if result is None:
        print_error("Node is not reachable")
        print_info("  Check that the node is running and RPC is enabled")
        print_info(f"  URL: {client.url}")
        return False
    
    print_success("Node is reachable")
    chain_id = int(result, 16) if isinstance(result, str) and result.startswith("0x") else result
    print_info(f"  Chain ID: {chain_id}")
    return True


def check_p2p_status(client: RPCClient) -> bool:
    """Check P2P configuration and status."""
    print_header("2. P2P Configuration")
    
    result = client.call("debug_p2p_status")
    if result is None:
        print_warning("debug_p2p_status method not available")
        print_info("  Cannot verify P2P configuration")
        return False
    
    # Check TX relay flags
    tx_relay = result.get("tx_relay", {})
    enabled = tx_relay.get("enabled", False)
    relay_flags = tx_relay.get("relay_flags", {})
    
    if enabled:
        print_success("TX relay is ENABLED")
    else:
        print_error("TX relay is DISABLED")
        print_info("  Set ANIMICA_P2P_TX_ENABLED=true")
        return False
    
    # Check individual flags
    all_enabled = True
    for flag, value in relay_flags.items():
        if value:
            print_success(f"  {flag}: enabled")
        else:
            print_warning(f"  {flag}: disabled")
            all_enabled = False
    
    # Check peers
    peers = result.get("peers", [])
    if len(peers) == 0:
        print_warning("No peers connected")
        print_info("  Add peers using ANIMICA_P2P_SEEDS environment variable")
        return False
    
    print_success(f"{len(peers)} peer(s) connected")
    
    # Check peer status
    ready_peers = 0
    for peer in peers:
        remote = peer.get("remote", "unknown")
        handshake = peer.get("handshake_complete", False)
        chain_match = peer.get("chain_match", False)
        relay_caps = peer.get("relay_caps", {})
        txs_relay = relay_caps.get("txs", False)
        
        if handshake and chain_match and txs_relay:
            ready_peers += 1
            print_success(f"  Peer {remote}: ready for TX relay")
        else:
            print_warning(f"  Peer {remote}: NOT ready")
            if not handshake:
                print_info(f"    - Handshake incomplete")
            if not chain_match:
                print_info(f"    - Chain mismatch")
            if not txs_relay:
                print_info(f"    - TX relay not enabled")
    
    if ready_peers == 0:
        print_error("No peers ready for TX relay")
        return False
    
    return all_enabled and ready_peers > 0


def check_mempool_service(client: RPCClient) -> bool:
    """Check mempool service status."""
    print_header("3. Mempool Service")
    
    # Try to get mempool size
    result = client.call("eth_getBlockByNumber", ["pending", False])
    if result is None:
        print_warning("Cannot query pending block")
        return False
    
    txs = result.get("transactions", [])
    print_info(f"  Pending transactions: {len(txs)}")
    
    # Try debug method
    debug_result = client.call("debug_mempool_status")
    if debug_result:
        queue_depth = debug_result.get("queue_depth", 0)
        print_info(f"  Mempool queue depth: {queue_depth}")
        print_success("Mempool service is operational")
        return True
    else:
        print_warning("debug_mempool_status not available")
        print_info("  Cannot verify mempool service directly")
        return False


def check_mining_config(client: RPCClient) -> bool:
    """Check mining configuration includes mempool."""
    print_header("4. Mining Configuration")
    
    result = client.call("miner_getWork", [{"include_mempool": True}])
    if result is None:
        print_warning("miner_getWork method not available")
        print_info("  Cannot verify mining configuration")
        return False
    
    mempool_enabled = result.get("mempoolEnabled", False)
    if mempool_enabled:
        print_success("Mining includes mempool transactions")
        tx_count = result.get("txCount", 0)
        print_info(f"  Transactions in template: {tx_count}")
        return True
    else:
        print_error("Mining does NOT include mempool")
        print_info("  Set include_mempool=true in getWork params")
        return False


def check_tx_relay_metrics(client: RPCClient) -> bool:
    """Check PTL and TX relay activity metrics."""
    print_header("5. PTL & TX Relay Activity")
    
    # Try PTL stats first (canonical method)
    ptl_result = client.call("debug.ptlStats", [{}])
    if ptl_result:
        print_success("PTL service is available")
        min_acks = ptl_result.get("min_peer_acks", 0)
        ttl = ptl_result.get("ttl_seconds", 0)
        print_info(f"  Min peer acks: {min_acks}")
        print_info(f"  TTL: {ttl}s")
        
        stats = ptl_result.get("stats", {})
        if stats:
            print_info("  Transactions by status:")
            for status, count in stats.items():
                print_info(f"    {status}: {count}")
        return True
    
    # Fallback to old debug_p2p_status
    result = client.call("debug_p2p_status")
    if result is None:
        print_warning("Cannot get relay metrics (PTL or legacy)")
        return False
    
    tx_relay = result.get("tx_relay", {})
    tx_relay_v2 = result.get("tx_relay_v2", {})
    
    # Check V2 (TxRelayService) metrics
    inflight = tx_relay_v2.get("inflight", 0)
    print_info(f"  Inflight requests: {inflight}")
    
    # Check general metrics
    queue_depth = tx_relay.get("queue_depth", 0)
    inflight_requests = tx_relay.get("inflight_requests", 0)
    seen_inv = tx_relay.get("seen_inv", 0)
    
    print_info(f"  Queue depth: {queue_depth}")
    print_info(f"  Seen INV messages: {seen_inv}")
    
    if seen_inv > 0:
        print_success("TX relay activity detected")
        return True
    else:
        print_warning("No TX relay activity yet")
        print_info("  This is normal if no transactions have been submitted")
        return False


def check_ptl_replication(client: RPCClient, tx_hash: Optional[str] = None) -> bool:
    """Check PTL replication status for a specific transaction."""
    if not tx_hash:
        return True
    
    print_header("6. PTL Replication Status")
    
    result = client.call("ptl.replicationStatus", [{"txid": tx_hash}])
    if result is None:
        print_warning("PTL replication status not available")
        print_info("  PTL may not be enabled on this node")
        print_info("  Set ANIMICA_PTL_ENABLE=1 to enable PTL")
        return False
    
    local_status = result.get("local_status", "unknown")
    quorum = result.get("quorum", {})
    acks = quorum.get("observed_acks", 0)
    required = quorum.get("required_acks", 0)
    quorum_met = quorum.get("quorum_met", False)
    
    print_info(f"  Transaction: {result.get('tx_hash')}")
    print_info(f"  Local Status: {local_status}")
    print_info(f"  Acks: {acks}/{required}")
    
    if quorum_met:
        print_success("Quorum met - transaction attested")
    else:
        print_warning(f"Quorum not met ({acks}/{required} acks)")
    
    peers = result.get("peers", [])
    if peers:
        print_info(f"  Peer receipts: {len(peers)}")
        for p in peers:
            status = p.get("status")
            peer_id = p.get("peer_id", "unknown")[:16]
            if status == "ack":
                print_success(f"    {peer_id}: {status}")
            else:
                print_warning(f"    {peer_id}: {status}")
    else:
        print_warning("  No peer receipts yet")
    
    return quorum_met


def suggest_fixes():
    """Suggest common fixes."""
    print_header("Troubleshooting Steps")
    
    print_info("If PTL/TX propagation is not working:")
    print_info("")
    print_info("1. Enable PTL service:")
    print_info("   export ANIMICA_PTL_ENABLE=1")
    print_info("")
    print_info("2. Enable P2P and TX relay:")
    print_info("   export ANIMICA_P2P_ENABLE=1")
    print_info("   export ANIMICA_P2P_TX_RELAY=true")
    print_info("")
    print_info("3. Connect to peers:")
    print_info("   export ANIMICA_P2P_SEEDS='/ip4/127.0.0.1/tcp/30333'")
    print_info("")
    print_info("4. Restart the node after changing configuration")
    print_info("")
    print_info("5. Submit a test transaction:")
    print_info("   animica tx send --from <addr> --to <addr> --value 0.01 --min-peers 2")
    print_info("")
    print_info("6. Check replication status:")
    print_info("   animica tx replicate <tx_hash> --json")
    print_info("")
    print_info("7. Monitor PTL stats:")
    print_info("   curl -X POST http://localhost:8545/rpc \\")
    print_info("     -H 'Content-Type: application/json' \\")
    print_info("     -d '{\"jsonrpc\":\"2.0\",\"method\":\"debug.ptlStats\",\"params\":[{}],\"id\":1}'")


def main():
    if len(sys.argv) > 2:
        rpc_url = sys.argv[1]
        tx_hash = sys.argv[2]
    elif len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.startswith("0x"):
            rpc_url = "http://localhost:8545/rpc"
            tx_hash = arg
        else:
            rpc_url = arg
            tx_hash = None
    else:
        rpc_url = "http://localhost:8545/rpc"
        tx_hash = None
    
    print_header("Transaction Propagation Diagnostic Tool")
    print_info(f"Testing node: {rpc_url}")
    if tx_hash:
        print_info(f"Transaction: {tx_hash}")
    
    client = RPCClient(rpc_url)
    
    checks = [
        ("Node reachable", lambda c: check_node_reachable(c)),
        ("P2P configured", lambda c: check_p2p_status(c)),
        ("Mempool service", lambda c: check_mempool_service(c)),
        ("Mining config", lambda c: check_mining_config(c)),
        ("PTL/TX relay activity", lambda c: check_tx_relay_metrics(c)),
    ]
    
    if tx_hash:
        checks.append(("PTL replication", lambda c: check_ptl_replication(c, tx_hash)))
    
    results = []
    for name, check_fn in checks:
        try:
            result = check_fn(client)
            results.append((name, result))
        except Exception as e:
            print_error(f"Check failed: {e}")
            results.append((name, False))
    
    # Summary
    print_header("Summary")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        if result:
            print_success(f"{name}")
        else:
            print_error(f"{name}")
    
    print_info("")
    print_info(f"Passed: {passed}/{total} checks")
    
    if passed < total:
        suggest_fixes()
    else:
        print_success("\nAll checks passed! PTL/TX propagation should be working.")
    
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
