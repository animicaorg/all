from __future__ import annotations

"""
Animica core.boot
=================
Tiny bring-up CLI that initializes a node DB from a genesis file and ensures
the canonical head is set. After running, your DB is ready for RPC/P2P to start.

Usage
-----
python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///animica.db
"""

import argparse
import time
import sys
import os
from pathlib import Path
from typing import Optional, Tuple

from core.chain.head import finalize_genesis, read_head
from core.db import open_kv
from core.db.block_db import BlockDB
from core.db.state_db import StateDB
from core.errors import AnimicaError
from core.genesis.genesis_loader import GenesisNotFoundError, resolve_genesis_path
from core.genesis.loader import load_genesis, compute_genesis_identity
from core.network_params import enforce_pinned_genesis
# Core deps
from core.logging import setup_logging
from core.types.header import Header
from core.types.params import ChainParams

DEFAULT_GENESIS = None
DEFAULT_DB = "sqlite:///animica.db"


def _load_genesis(genesis_path: Path) -> Tuple[ChainParams, Header]:
    """
    Load and validate the genesis bundle. The loader returns ChainParams and
    a fully-formed Header for height=0. (Any initial state pre-seeding is done
    by the loader or later by execution, depending on your setup.)
    """
    params, genesis_header = load_genesis(genesis_path)
    return params, genesis_header


def _open_dbs(db_uri: str) -> tuple[BlockDB, StateDB]:
    """
    Open the KV backend from a URI and construct BlockDB/StateDB adapters.
    Supported URIs (by core.db.kv):
      - sqlite:///path/to/file.db
      - rocksdb:///path/to/dir        (optional; if compiled in)
      - memory://                     (for testing)
    """
    kv = open_kv(db_uri)
    block_db = BlockDB(kv)
    state_db = StateDB(kv)
    return block_db, state_db


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="animica-core-boot",
        description="Initialize Animica DB from genesis and set canonical head.",
    )
    ap.add_argument(
        "--genesis",
        type=str,
        nargs="?",
        default=DEFAULT_GENESIS,
        const=DEFAULT_GENESIS,
        help="Path to genesis file (default: auto-detect bundled genesis.json)",
    )
    ap.add_argument(
        "--db",
        type=str,
        nargs="?",
        default=DEFAULT_DB,
        const=DEFAULT_DB,
        help=f"Database URI (default: {DEFAULT_DB})",
    )
    ap.add_argument(
        "--log",
        type=str,
        default="info",
        choices=["debug", "info", "warn", "error"],
        help="Log level (default: info)",
    )
    ap.add_argument(
        "--force-reset-db",
        action="store_true",
        help="Force reset database if genesis mismatch detected (destructive operation)",
    )
    args = ap.parse_args(argv)

    setup_logging(level=args.log.upper())
    try:
        genesis_path = resolve_genesis_path(args.genesis)
    except GenesisNotFoundError as exc:
        print(f"[boot] genesis file not found: {exc}", file=sys.stderr)
        print(
            "\nFor mainnet genesis creation, run the bootstrap command:\n"
            "    python -m core.bootstrap --network mainnet --genesis-sample <path> --db <uri>",
            file=sys.stderr,
        )
        return 2

    try:
        identity = compute_genesis_identity(genesis_path)
        enforce_pinned_genesis(
            chain_id=identity.chain_id,
            genesis_block_hash=identity.genesis_block_hash,
            genesis_path=str(identity.genesis_path),
            network_name=os.getenv("ANIMICA_NETWORK"),
        )

        params, genesis_header = _load_genesis(genesis_path)
        block_db, state_db = _open_dbs(args.db)
        
        # CHAIN_RESET_TOUCHPOINT: Check for genesis mismatch
        try:
            # Check if DB has existing genesis that doesn't match
            stored_genesis_hash = block_db.get_genesis_hash()
            stored_chain_id = block_db.get_chain_id()
            
            if stored_genesis_hash is not None:
                # Compare with expected genesis
                expected_hash = identity.genesis_block_hash
                if stored_genesis_hash != expected_hash:
                    expected_hex = "0x" + expected_hash.hex()
                    found_hex = "0x" + stored_genesis_hash.hex()
                    
                    if args.force_reset_db:
                        print("=" * 80, file=sys.stderr)
                        print("WARNING: Genesis mismatch detected!", file=sys.stderr)
                        print(f"  Expected: {expected_hex}", file=sys.stderr)
                        print(f"  Found:    {found_hex}", file=sys.stderr)
                        print("", file=sys.stderr)
                        print("Resetting database (--force-reset-db specified)...", file=sys.stderr)
                        print("=" * 80, file=sys.stderr)
                        
                        # Close and delete the database
                        import shutil
                        db_path = args.db.replace("sqlite:///", "")
                        if os.path.exists(db_path):
                            os.remove(db_path)
                            print(f"Deleted: {db_path}", file=sys.stderr)
                        
                        # Re-open fresh database
                        block_db, state_db = _open_dbs(args.db)
                    else:
                        print("=" * 80, file=sys.stderr)
                        print("ERROR: Genesis mismatch detected!", file=sys.stderr)
                        print(f"  Expected: {expected_hex}", file=sys.stderr)
                        print(f"  Found:    {found_hex}", file=sys.stderr)
                        print("", file=sys.stderr)
                        print("This database was initialized with a different genesis.", file=sys.stderr)
                        print("To reset the database and start fresh, use:", file=sys.stderr)
                        print(f"  python -m core.boot --genesis {genesis_path} --db {args.db} --force-reset-db", file=sys.stderr)
                        print("", file=sys.stderr)
                        print("WARNING: This will delete all blockchain data!", file=sys.stderr)
                        print("=" * 80, file=sys.stderr)
                        return 3
            
            if stored_chain_id is not None and stored_chain_id != params.chain_id:
                if not args.force_reset_db:
                    print("=" * 80, file=sys.stderr)
                    print("ERROR: Chain ID mismatch!", file=sys.stderr)
                    print(f"  Expected: {params.chain_id}", file=sys.stderr)
                    print(f"  Found:    {stored_chain_id}", file=sys.stderr)
                    print("=" * 80, file=sys.stderr)
                    return 3
        except Exception as e:
            # If metadata read fails, continue (might be fresh DB)
            pass

        # Ensure canonical head exists and points at our genesis if DB is fresh.
        genesis_sha256 = identity.genesis_file_hash
        head_height, head_hash = finalize_genesis(
            block_db,
            params,
            genesis_header,
            genesis_sha256=genesis_sha256,
            genesis_path=str(genesis_path),
            created_at=int(time.time()),
        )

        # A tiny sanity read just to prove we can fetch it back:
        head = read_head(block_db)
        if head is None:
            raise AnimicaError(
                "head pointer missing after finalize_genesis (unexpected)"
            )
        h_height, h_hash = head

        print("=== Animica boot complete ===")
        print(f"DB:            {args.db}")
        print(f"Genesis:       {genesis_path}")
        print(f"Chain ID:      {params.chain_id}")
        print(f"Fork ID:       {identity.fork_id}")
        print(f"Consensus ID:  {identity.consensus_id}")
        print(f"Protocol Ver:  {identity.protocol_version}")
        print(f"Head height:   {h_height}")
        print(f"Head hash:     0x{h_hash.hex()}")
        if h_height == 0:
            print("Status:        Fresh DB initialized at genesis.")
        else:
            print("Status:        Existing DB detected; left head unchanged.")
        return 0

    except AnimicaError as e:
        print(f"[boot] Animica error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[boot] Unhandled error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
