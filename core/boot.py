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
import sys
from pathlib import Path
from typing import Optional, Tuple

from core.chain.head import finalize_genesis, read_head
from core.db import open_kv
from core.db.block_db import BlockDB
from core.db.state_db import StateDB
from core.errors import AnimicaError
from core.genesis.loader import load_genesis
# Core deps
from core.logging import setup_logging
from core.types.header import Header
from core.types.params import ChainParams

DEFAULT_GENESIS = "core/genesis/genesis.json"
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
        help=f"Path to genesis file (default: {DEFAULT_GENESIS})",
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
    args = ap.parse_args(argv)

    setup_logging(level=args.log.upper())
    genesis_path = Path(args.genesis)
    if not genesis_path.exists():
        print(f"[boot] genesis file not found: {genesis_path}", file=sys.stderr)
        return 2

    try:
        params, genesis_header = _load_genesis(genesis_path)
        block_db, state_db = _open_dbs(args.db)

        # Ensure canonical head exists and points at our genesis if DB is fresh.
        head_height, head_hash = finalize_genesis(block_db, params, genesis_header)

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
