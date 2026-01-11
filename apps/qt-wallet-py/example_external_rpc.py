#!/usr/bin/env python3
"""
Sample script demonstrating external RPC interface to Animica wallet.

This script shows how external applications can request wallet actions
through the walletd external RPC interface.
"""
import json
import sys
import time
from pathlib import Path

import requests


def load_token(data_dir: Path | None = None) -> str:
    """Load the walletd authentication token."""
    if data_dir is None:
        # Use default data directory
        if sys.platform == "darwin":
            data_dir = Path.home() / "Library" / "Application Support" / "AnimicaWallet"
        elif sys.platform == "win32":
            data_dir = Path.home() / "AppData" / "Roaming" / "AnimicaWallet"
        else:
            data_dir = Path.home() / ".animica-wallet"
    
    token_path = data_dir / "walletd.token"
    if not token_path.exists():
        raise RuntimeError(f"Token file not found at {token_path}. Is walletd running?")
    
    return token_path.read_text(encoding="utf-8").strip()


def rpc_call(method: str, params: dict | None = None, rpc_url: str = "http://127.0.0.1:17834/external") -> dict:
    """Make an RPC call to the external wallet interface."""
    token = load_token()
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    response = requests.post(rpc_url, json=payload, headers=headers, timeout=150)
    response.raise_for_status()
    
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error'].get('message', 'Unknown error')}")
    
    return data.get("result", {})


def request_accounts() -> list[str]:
    """Request access to wallet accounts."""
    print("Requesting wallet accounts...")
    print("⚠️  Please approve the request in the wallet UI")
    
    result = rpc_call("wallet_requestAccounts")
    return result


def get_chain_id() -> int:
    """Get the current chain ID."""
    result = rpc_call("wallet_getChainId")
    return result


def send_transaction(from_addr: str, to_addr: str, value: int) -> str:
    """Send a transaction."""
    print(f"Sending transaction: {value} wei from {from_addr} to {to_addr}")
    print("⚠️  Please approve the transaction in the wallet UI")
    
    # Build transaction
    tx = {
        "from": from_addr,
        "to": to_addr,
        "value": value,
        "gas_limit": 21000,
        "max_fee": 1_000_000_000,  # 1 gwei
        "data": "",
    }
    
    result = rpc_call("wallet_sendTransaction", {"transaction": tx, "from": from_addr})
    return result


def main() -> int:
    print("=" * 60)
    print("Animica Wallet External RPC Demo")
    print("=" * 60)
    print()
    
    try:
        # Get chain ID (no approval required)
        print("1. Getting chain ID...")
        chain_id = get_chain_id()
        print(f"   ✓ Chain ID: {chain_id}")
        print()
        
        # Request accounts (requires approval)
        print("2. Requesting wallet accounts...")
        accounts = request_accounts()
        print(f"   ✓ Got {len(accounts)} account(s):")
        for addr in accounts:
            print(f"     - {addr}")
        print()
        
        if not accounts:
            print("No accounts available. Please create an account in the wallet first.")
            return 1
        
        # Send a test transaction (requires approval)
        print("3. Sending test transaction...")
        from_addr = accounts[0]
        to_addr = "anim1qyfeats5ck0ceh70xr7yfcdvmcyep5nwqxw8z"  # Example address
        value = 100  # 100 wei
        
        tx_hash = send_transaction(from_addr, to_addr, value)
        print(f"   ✓ Transaction sent!")
        print(f"     TX Hash: {tx_hash}")
        print()
        
        print("=" * 60)
        print("Demo completed successfully!")
        print("=" * 60)
        
        return 0
    
    except KeyboardInterrupt:
        print("\nDemo cancelled by user.")
        return 130
    
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
