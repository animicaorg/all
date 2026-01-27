#!/usr/bin/env python3
"""
Update Genesis Hash Utility for Verifier Nodes
===============================================

This script helps verifier nodes update their stored genesis hash to match
the current genesis file without losing their sync state.

Usage:
    python scripts/update_genesis_hash.py --network mainnet --data-dir ~/.animica/chain-1
    python scripts/update_genesis_hash.py --network testnet --data-dir ~/.animica/chain-2
    python scripts/update_genesis_hash.py --db-uri sqlite:///path/to/animica.db --genesis-path core/genesis/mainnet.json

For Docker deployments:
    docker exec animica-node python /app/scripts/update_genesis_hash.py --network mainnet
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add repository root to path for imports
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update genesis hash in existing database to match current genesis file"
    )
    parser.add_argument(
        "--network",
        choices=["mainnet", "testnet", "devnet"],
        help="Network name (mainnet, testnet, devnet)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        help="Data directory path (e.g., ~/.animica/chain-1)",
    )
    parser.add_argument(
        "--db-uri",
        type=str,
        help="Direct database URI (e.g., sqlite:///path/to/animica.db)",
    )
    parser.add_argument(
        "--genesis-path",
        type=str,
        help="Path to genesis JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force update even if genesis hash already matches",
    )

    args = parser.parse_args()

    # Determine genesis path
    if args.genesis_path:
        genesis_path = args.genesis_path
    elif args.network:
        genesis_path = f"core/genesis/{args.network}.json"
    else:
        print("Error: Must specify either --network or --genesis-path", file=sys.stderr)
        return 1

    # Determine database URI
    if args.db_uri:
        db_uri = args.db_uri
    elif args.data_dir:
        data_dir = Path(args.data_dir).expanduser()
        db_uri = f"sqlite:///{data_dir}/animica.db"
    elif args.network:
        # Default data directory based on network
        chain_ids = {"mainnet": 1, "testnet": 2, "devnet": 1337}
        chain_id = chain_ids[args.network]
        data_dir = Path.home() / ".animica" / f"chain-{chain_id}"
        db_uri = f"sqlite:///{data_dir}/animica.db"
    else:
        print("Error: Must specify --db-uri, --data-dir, or --network", file=sys.stderr)
        return 1

    # Import core modules
    try:
        from core.db.block_db import BlockDB
        from core.db.sqlite import SQLiteKV
        from core.genesis.loader import compute_genesis_identity
    except ImportError as e:
        print(f"Error importing core modules: {e}", file=sys.stderr)
        print("Make sure you're running from the repository root", file=sys.stderr)
        return 1

    # Check if database exists
    if db_uri.startswith("sqlite:///"):
        db_path = Path(db_uri.replace("sqlite:///", ""))
        if not db_path.exists():
            print(f"Database not found: {db_path}", file=sys.stderr)
            print("Nothing to update - node may not have been initialized yet", file=sys.stderr)
            return 0

    # Compute expected genesis identity
    try:
        identity = compute_genesis_identity(genesis_path)
        expected_hash = identity.genesis_block_hash
        expected_file_hash = identity.genesis_file_hash
        chain_id = identity.chain_id
    except Exception as e:
        print(f"Error computing genesis identity: {e}", file=sys.stderr)
        return 1

    print(f"Genesis file: {genesis_path}")
    print(f"Chain ID: {chain_id}")
    print(f"Expected genesis hash: 0x{expected_hash.hex()}")
    print(f"Database URI: {db_uri}")
    print()

    # Open database
    try:
        if db_uri.startswith("sqlite:///"):
            db_path_str = db_uri.replace("sqlite:///", "")
            kv = SQLiteKV(db_path_str)
        else:
            print(f"Unsupported DB URI: {db_uri}", file=sys.stderr)
            return 1

        block_db = BlockDB(kv)
    except Exception as e:
        print(f"Error opening database: {e}", file=sys.stderr)
        return 1

    # Get current genesis hash
    try:
        current_hash = block_db.get_genesis_hash()
        current_file_hash = block_db.get_genesis_sha256() if hasattr(block_db, "get_genesis_sha256") else None
        current_chain_id = block_db.get_chain_id() if hasattr(block_db, "get_chain_id") else None
    except Exception as e:
        print(f"Error reading current genesis: {e}", file=sys.stderr)
        current_hash = None
        current_file_hash = None
        current_chain_id = None

    if current_hash:
        print(f"Current genesis hash: 0x{current_hash.hex()}")
    else:
        print("Current genesis hash: <not set>")

    if current_chain_id:
        print(f"Current chain ID: {current_chain_id}")

    # Check if update is needed
    needs_update = False
    if current_hash is None:
        print("\nGenesis hash not set in database - will initialize")
        needs_update = True
    elif current_hash != expected_hash:
        print(f"\n⚠️  Genesis hash MISMATCH detected!")
        print(f"    Current:  0x{current_hash.hex()}")
        print(f"    Expected: 0x{expected_hash.hex()}")
        needs_update = True
    elif args.force:
        print("\n--force specified, will update even though hash matches")
        needs_update = True
    else:
        print("\n✓ Genesis hash already matches - no update needed")

    if current_chain_id and current_chain_id != chain_id:
        print(f"\n⚠️  WARNING: Chain ID mismatch!")
        print(f"    Database has chain_id={current_chain_id}")
        print(f"    Genesis file has chain_id={chain_id}")
        print(f"    This may indicate you're trying to use the wrong genesis file")
        if not args.force:
            print("\nUse --force to proceed anyway (not recommended)")
            return 1

    if not needs_update:
        return 0

    if args.dry_run:
        print("\n[DRY RUN] Would update genesis hash to:", f"0x{expected_hash.hex()}")
        if expected_file_hash:
            print("[DRY RUN] Would update genesis file hash to:", f"0x{expected_file_hash.hex()}")
        return 0

    # Perform update
    print("\nUpdating genesis hash...")
    try:
        block_db.set_genesis_hash(expected_hash)
        print(f"✓ Set genesis hash to: 0x{expected_hash.hex()}")

        if expected_file_hash and hasattr(block_db, "set_genesis_sha256"):
            block_db.set_genesis_sha256(expected_file_hash)
            print(f"✓ Set genesis file hash to: 0x{expected_file_hash.hex()}")

        if hasattr(block_db, "set_chain_id"):
            block_db.set_chain_id(chain_id)
            print(f"✓ Set chain ID to: {chain_id}")

        print("\n✓ Genesis hash updated successfully!")
        print("\nYou can now restart your node. It will sync from the correct genesis.")
        return 0
    except Exception as e:
        print(f"\n✗ Error updating genesis hash: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
