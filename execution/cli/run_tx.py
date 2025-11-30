#!/usr/bin/env python3
"""
execution.cli.run_tx — run a single CBOR-encoded tx against a temporary state and print ApplyResult.

This CLI:
  1) Creates a temporary SQLite-backed state DB (unless --db is provided)
  2) Ensures the DB is initialized from a genesis file if empty
  3) Decodes one CBOR-encoded Transaction (per core/types/tx.py & spec/tx_format.cddl)
  4) Applies the tx via execution.runtime.executor.apply_tx
  5) Prints the ApplyResult (status, gasUsed, logs count, stateRoot) and optionally full JSON

Usage:
    python -m execution.cli.run_tx --tx path/to/tx.cbor \
        --genesis core/genesis/genesis.json --chain-id 1

Options:
    --db            Optional persistent DB URI (e.g., sqlite:///animica_tmp.db).
                    If omitted, a temp DB is created and deleted automatically.
    --persist-db    Keep the temporary DB directory for inspection (prints path).
    --json          Print full JSON of the ApplyResult (best-effort serialization).
    --quiet         Suppress progress messages on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


# --- Logging -------------------------------------------------------------------------------------
def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _import_fail(msg: str) -> None:
    eprint(f"[run_tx] {msg}")
    sys.exit(2)


# --- Animica imports -----------------------------------------------------------------------------
# Canonical CBOR codec
try:
    from core.encoding import cbor as core_cbor  # type: ignore
except Exception as ex:  # pragma: no cover
    _import_fail(f"Cannot import core.encoding.cbor: {ex}")

# DBs & genesis
try:
    from core.db.sqlite import open_sqlite_kv  # type: ignore
    from core.db.state_db import StateDB  # type: ignore
    from core.genesis.loader import ensure_db_with_genesis  # type: ignore
except Exception as ex:  # pragma: no cover
    _import_fail(f"Cannot import core DB/genesis modules: {ex}")

# Types
try:
    from core.types.tx import Tx  # type: ignore
except Exception:
    Tx = Any  # type: ignore

# Execution adapters & executor
try:
    from execution.adapters.params import load_chain_params  # type: ignore
    from execution.adapters.state_db import StateAdapter  # type: ignore
except Exception as ex:  # pragma: no cover
    _import_fail(f"Cannot import execution adapters: {ex}")

try:
    from execution.runtime.executor import \
        apply_tx as exec_apply_tx  # type: ignore
except Exception as ex:  # pragma: no cover
    _import_fail(f"Cannot import execution.runtime.executor.apply_tx: {ex}")


# --- Helpers -------------------------------------------------------------------------------------
def _cbor_load_file(path: Path) -> Any:
    data = path.read_bytes()
    for fn_name in ("decode", "loads", "decode_canonical"):
        fn = getattr(core_cbor, fn_name, None)
        if fn:
            return fn(data)
    raise RuntimeError(
        "core.encoding.cbor does not provide decode/loads/decode_canonical"
    )


def _coerce_tx(obj: Any) -> Tx:
    if hasattr(obj, "kind") and hasattr(obj, "sender"):
        return obj  # type: ignore
    if isinstance(obj, Mapping):
        try:
            return Tx(**obj)  # type: ignore
        except Exception:
            return obj  # type: ignore
    return obj  # type: ignore


def _hexify(x: Any) -> Any:
    """Convert bytes-like to 0x-hex; Enums → name; dataclasses → dict; fall back to JSON-serializable."""
    if x is None:
        return None
    if isinstance(x, (bytes, bytearray)):
        return "0x" + x.hex()
    if isinstance(x, memoryview):
        return "0x" + bytes(x).hex()
    if isinstance(x, Enum):
        return x.name
    if is_dataclass(x):
        return {k: _hexify(v) for k, v in asdict(x).items()}
    if isinstance(x, dict):
        return {k: _hexify(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        t = type(x)
        return t(_hexify(v) for v in x)
    return x


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run a single CBOR tx against a temporary state."
    )
    p.add_argument(
        "--tx", required=True, type=Path, help="Path to CBOR-encoded transaction file"
    )
    p.add_argument(
        "--db",
        default=None,
        help="KV DB URI (e.g., sqlite:///animica_tmp.db). If omitted, a temp DB is used.",
    )
    p.add_argument(
        "--genesis", type=Path, default=None, help="Genesis JSON (used if DB is empty)"
    )
    p.add_argument(
        "--chain-id", type=int, default=1, help="Expected chainId for sanity checks"
    )
    p.add_argument(
        "--persist-db",
        action="store_true",
        help="Keep the temporary DB for inspection and print its path",
    )
    p.add_argument("--json", action="store_true", help="Print full JSON ApplyResult")
    p.add_argument(
        "--quiet", action="store_true", help="Only print the final summary / JSON"
    )
    return p.parse_args(argv)


# --- Main ----------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(argv or sys.argv[1:])
    if not ns.tx.exists():
        eprint(f"[run_tx] Tx file not found: {ns.tx}")
        return 2

    tmp_dir: str | None = None
    db_uri: str
    if ns.db:
        db_uri = ns.db
        if not ns.quiet:
            eprint(f"[run_tx] Using DB: {db_uri}")
    else:
        # Create a temp directory for an ephemeral SQLite DB file
        tmp_dir = tempfile.mkdtemp(prefix="animica_run_tx_")
        db_path = Path(tmp_dir) / "state.db"
        db_uri = f"sqlite:///{db_path}"
        if not ns.quiet:
            eprint(f"[run_tx] Created temporary DB at {db_path}")

    try:
        # 1) Ensure DB is initialized (from genesis if needed)
        kv = open_sqlite_kv(db_uri)
        ensure_db_with_genesis(kv, ns.genesis, expected_chain_id=ns.chain_id)

        state_db = StateDB(kv)
        state_adapter = StateAdapter(state_db)
        chain_params = load_chain_params(kv)

        # 2) Decode the CBOR transaction
        if not ns.quiet:
            eprint(f"[run_tx] Decoding CBOR tx: {ns.tx}")
        tx_obj = _cbor_load_file(ns.tx)
        tx_typed = _coerce_tx(tx_obj)

        # 3) Apply the transaction
        if not ns.quiet:
            eprint("[run_tx] Applying transaction…")
        result = exec_apply_tx(
            tx=tx_typed,
            state_adapter=state_adapter,
            chain_params=chain_params,
        )

        # 4) Extract common fields for stable output
        status = getattr(result, "status", None) or result.get("status")
        gas_used = int(getattr(result, "gas_used", None) or result.get("gas_used", 0))
        logs = getattr(result, "logs", None) or result.get("logs", []) or []
        state_root = getattr(result, "state_root", None) or result.get("state_root")
        receipt = getattr(result, "receipt", None) or result.get("receipt")

        if ns.json:
            out = {
                "status": _hexify(status),
                "gasUsed": gas_used,
                "logs": _hexify(logs),
                "stateRoot": _hexify(state_root),
                "receipt": _hexify(receipt),
            }
            print(json.dumps(out, indent=2, sort_keys=True))
        else:
            logs_count = len(logs) if isinstance(logs, (list, tuple)) else 0
            sr_hex = _hexify(state_root)
            print(
                f"APPLY_RESULT STATUS={status} GAS_USED={gas_used} LOGS={logs_count} STATE_ROOT={sr_hex}"
            )

        if tmp_dir and ns.persist_db:
            print(f"DB_PATH {Path(tmp_dir) / 'state.db'}")

        return 0

    finally:
        # Clean up ephemeral DB unless persisted or user supplied --db
        if tmp_dir and not ns.persist_db:
            try:
                shutil.rmtree(tmp_dir)
            except Exception:
                pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
