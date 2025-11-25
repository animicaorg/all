from __future__ import annotations

"""
Animica core.cli_demo
=====================

Tiny sanity CLI that prints the chain parameters and the current head pointer.
Helpful right after running:

    python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///animica.db

Usage:
    python -m core.cli_demo --db sqlite:///animica.db [--genesis core/genesis/genesis.json]
"""

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional

from core.logging import setup_logging
from core.errors import AnimicaError
from core.db.kv import open_kv
from core.db.block_db import BlockDB
from core.genesis.loader import load_genesis
from core.chain.head import read_head


DEFAULT_DB = "sqlite:///animica.db"
DEFAULT_GENESIS = "core/genesis/genesis.json"


def _params_to_dict(params: Any) -> dict[str, Any]:
    if is_dataclass(params):
        return asdict(params)
    if hasattr(params, "__dict__"):
        return dict(params.__dict__)  # type: ignore[no-any-return]
    # last resort: try JSON if it's already a mapping-like object
    try:
        return json.loads(json.dumps(params))
    except Exception:
        return {"_repr": repr(params)}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="animica-cli-demo",
        description="Print Animica chain params and current head.",
    )
    ap.add_argument("--db", type=str, default=DEFAULT_DB, help="Database URI")
    ap.add_argument(
        "--genesis",
        type=str,
        default=DEFAULT_GENESIS,
        help="Path to genesis (used here only to print canonical params)",
    )
    ap.add_argument(
        "--log",
        type=str,
        default="info",
        choices=["debug", "info", "warn", "error"],
        help="Log level",
    )
    args = ap.parse_args(argv)
    setup_logging(level=args.log.upper())

    # Load params (source of truth is the genesis file for now).
    genesis_path = Path(args.genesis)
    if not genesis_path.exists():
        print(f"[cli_demo] warning: genesis file not found: {genesis_path} — params may be missing")

    params = None
    if genesis_path.exists():
        try:
            params, _genesis_header = load_genesis(genesis_path)
        except Exception as e:
            print(f"[cli_demo] failed to load genesis params: {e}")

    # Open DB and read head pointer.
    try:
        kv = open_kv(args.db)
        bdb = BlockDB(kv)
        head = read_head(bdb)
    except AnimicaError as e:
        print(f"[cli_demo] DB error: {e}")
        return 1
    except Exception as e:
        print(f"[cli_demo] unexpected DB error: {e}")
        return 1

    # Pretty print.
    print("=== Animica — Chain Sanity ===")
    print(f"DB URI:        {args.db}")
    if params is not None:
        p = _params_to_dict(params)
        # Pull a few common fields if present to headline them.
        chain_id = p.get("chain_id", "unknown")
        network_name = p.get("network_name", "unknown")
        block_time = p.get("target_block_time_secs", p.get("block_time_target_secs", "n/a"))
        base_reward = p.get("base_block_reward", "n/a")
        premine = p.get("premine_amount", p.get("premine", "n/a"))
        print("— Params —")
        print(f"Chain ID:      {chain_id}")
        print(f"Network:       {network_name}")
        print(f"Block target:  {block_time} s")
        print(f"Base reward:   {base_reward}")
        print(f"Premine:       {premine}")
        # Full params JSON for completeness.
        print("Params (full JSON):")
        print(json.dumps(p, sort_keys=True, indent=2))
    else:
        print("— Params —")
        print("Could not load params (no genesis found or parse error).")

    print("— Head —")
    if head is None:
        print("Head:          <none> (DB appears empty). Try running core.boot first.")
    else:
        height, h = head
        print(f"Head height:   {height}")
        print(f"Head hash:     0x{h.hex()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
