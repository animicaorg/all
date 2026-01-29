#!/usr/bin/env python3
"""
execution.cli.apply_block — apply a CBOR-encoded block to a DB and print new head.

This CLI:
  1) Ensures the DB is initialized (optionally from a genesis file)
  2) Decodes a CBOR-encoded Block file (per core/types/block.py & spec/header_format.cddl)
  3) Runs the state transition for all txs in the block using execution.runtime.executor
  4) Persists receipts/logs and the block/header
  5) Prints the new head (height & hash)

Usage:
    python -m execution.cli.apply_block --block path/to/block.cbor \
        --db sqlite:///animica.db --genesis core/genesis/genesis.json --chain-id 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


# --- Logging (simple & deterministic) ------------------------------------------------------------
def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


# --- Imports from Animica codebase ---------------------------------------------------------------
def _import_fail(msg: str) -> None:
    eprint(f"[apply_block] {msg}")
    sys.exit(2)


# CBOR codec (deterministic)
try:
    # Prefer our canonical codec
    from core.encoding import cbor as core_cbor  # type: ignore
except Exception as ex:  # pragma: no cover
    _import_fail(f"Cannot import core.encoding.cbor: {ex}")

# DBs & genesis loader
try:
    from core.db.block_db import BlockDB  # type: ignore
    from core.db.sqlite import open_sqlite_kv  # type: ignore
    from core.db.state_db import StateDB  # type: ignore
    from core.genesis.loader import ensure_db_with_genesis  # type: ignore
except Exception as ex:  # pragma: no cover
    _import_fail(f"Cannot import core DB/genesis modules: {ex}")

# Types/helpers
try:
    from core.types.block import Block  # type: ignore
except Exception:
    Block = Any  # fallback typing

# Execution adapters & executor
try:
    from execution.adapters.block_db import \
        persist_block_with_receipts  # type: ignore
    from execution.adapters.params import load_chain_params  # type: ignore
    from execution.adapters.state_db import StateAdapter  # type: ignore
except Exception as ex:  # pragma: no cover
    _import_fail(f"Cannot import execution adapters: {ex}")

try:
    from execution.runtime.executor import \
        apply_block as exec_apply_block  # type: ignore
except Exception as ex:  # pragma: no cover
    _import_fail(f"Cannot import execution.runtime.executor.apply_block: {ex}")


# --- Helpers -------------------------------------------------------------------------------------
def _cbor_load_file(path: Path) -> Any:
    data = path.read_bytes()
    # Try multiple canonical entrypoints for resilience
    for fn_name in ("decode", "loads", "decode_canonical"):
        fn = getattr(core_cbor, fn_name, None)
        if fn:
            return fn(data)
    raise RuntimeError(
        "core.encoding.cbor does not provide decode/loads/decode_canonical"
    )


def _coerce_block(obj: Any) -> Block:
    """
    Accept either already-typed Block or a dict compatible with Block(**dict).
    """
    # If it's already our dataclass (duck: has 'header' and 'txs'), pass through
    if hasattr(obj, "header") and hasattr(obj, "txs"):
        return obj  # type: ignore
    if isinstance(obj, Mapping):
        try:
            return Block(**obj)  # type: ignore
        except Exception:
            # Some encoders use keys slightly different; rely on executor's tolerant path
            return obj  # type: ignore
    return obj  # type: ignore


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Apply a CBOR-encoded block to the local DB."
    )
    p.add_argument(
        "--block", required=True, type=Path, help="Path to CBOR-encoded block file"
    )
    p.add_argument(
        "--db",
        default="sqlite:///animica.db",
        help="KV DB URI (e.g., sqlite:///animica.db)",
    )
    p.add_argument(
        "--genesis", type=Path, default=None, help="Genesis JSON (used if DB is empty)"
    )
    p.add_argument(
        "--chain-id", type=int, default=1, help="Expected chainId for sanity checks"
    )
    p.add_argument(
        "--print-receipts", action="store_true", help="Print receipts JSON to stdout"
    )
    p.add_argument(
        "--quiet", action="store_true", help="Only print the final head line"
    )
    return p.parse_args(argv)


# --- Main ----------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(argv or sys.argv[1:])

    if not ns.block.exists():
        eprint(f"[apply_block] Block file not found: {ns.block}")
        return 2

    # 1) Ensure DB exists & is initialized (genesis if needed)
    kv = open_sqlite_kv(ns.db)
    ensure_db_with_genesis(kv, ns.genesis, expected_chain_id=ns.chain_id)

    state_db = StateDB(kv)
    block_db = BlockDB(kv)
    state_adapter = StateAdapter(state_db)

    # 2) Load chain params (gas tables/limits etc.)
    chain_params = load_chain_params(kv)

    # 3) Decode CBOR block
    if not ns.quiet:
        eprint(f"[apply_block] Decoding CBOR: {ns.block}")
    block_obj = _cbor_load_file(ns.block)
    block_typed = _coerce_block(block_obj)

    # 4) Execute & build receipts
    if not ns.quiet:
        eprint("[apply_block] Executing block…")
    exec_result = exec_apply_block(
        block=block_typed,
        state_adapter=state_adapter,
        chain_params=chain_params,
    )
    # Expect exec_result to have: receipts (list), state_root (bytes|hex), gas_used (int)
    receipts = getattr(exec_result, "receipts", None) or exec_result.get("receipts", [])
    state_root = getattr(exec_result, "state_root", None) or exec_result.get(
        "state_root"
    )
    gas_used = int(
        getattr(exec_result, "gas_used", None) or exec_result.get("gas_used", 0)
    )

    # 5) Persist block + receipts and update head
    if not ns.quiet:
        eprint("[apply_block] Persisting block & receipts…")
    persist_block_with_receipts(
        kv=kv,
        block=block_typed,
        receipts=receipts,
        computed_state_root=state_root,
        gas_used_total=gas_used,
    )

    head = block_db.get_best_head()
    head_num = getattr(head, "number", None) or head.get("number")
    head_hash = getattr(head, "hash", None) or head.get("hash")

    # 6) Output
    if ns.print_receipts:
        print(json.dumps(receipts, indent=2, sort_keys=True))

    print(f"NEW_HEAD {head_num} {head_hash}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
