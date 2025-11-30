"""
Animica — Genesis loader

Loads a genesis JSON, validates key fields, initializes the on-disk state DB with
premine/system accounts, computes the canonical state root, builds the genesis
header (height=0), persists it, and returns the in-memory objects.

This module is intentionally conservative about cross-package dependencies and
uses only the stable core/* surfaces:
  - core.utils.hash, core.utils.merkle, core.utils.serialization
  - core.encoding.cbor
  - core.db.sqlite (or rocksdb if available) via core.db.kv.KV
  - core.db.state_db.StateDB
  - core.db.block_db.BlockDB
  - core.types.header.Header
  - core.types.block.Block

It does NOT require consensus/ or execution/ to exist to bring a node up to the
"has a canonical genesis header" state.

Usage (library):
    from core.genesis.loader import load_and_init_genesis

    env = load_and_init_genesis(
        genesis_path="core/genesis/genesis.json",
        db_uri="sqlite:///animica.db",
        override_chain_id=None,         # optional: enforce a chain id
        log=True
    )
    print("Head:", env["head_height"], env["head_hash"].hex())

Usage (CLI):
    python -m core.genesis.loader --genesis core/genesis/genesis.json --db sqlite:///animica.db
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core.db.kv import KV
from core.db.sqlite import SQLiteKV  # default backend
from core.encoding import cbor as cbor
# --- Core imports (stable surfaces) ---
from core.utils import hash as uhash
from core.utils import merkle as umerkle
from core.utils.serialization import to_canonical_json

try:
    from core.db.rocksdb import \
        RocksDBKV  # optional, used if db_uri startswith rocksdb://
except Exception:  # pragma: no cover - optional
    RocksDBKV = None  # type: ignore

from core.db.block_db import BlockDB
from core.db.state_db import StateDB
from core.types.block import Block
from core.types.header import Header
from core.types.params import ChainParams, default_params_path

# -------------------------
# Helpers & canonical rules
# -------------------------

ZERO32 = b"\x00" * 32


def _sha3_256(data: bytes) -> bytes:
    return uhash.sha3_256(data)


def empty_root() -> bytes:
    """The canonical empty-root: sha3_256(0x)."""
    return _sha3_256(b"")


def _parse_time(s: str) -> int:
    """
    Parse RFC3339-like string to unix seconds.
    We accept 'Z' or explicit offset; store as absolute epoch seconds.
    """
    s = s.strip()
    # Simple tolerant parse
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt_obj = dt.datetime.fromisoformat(s)
    return int(dt_obj.timestamp())


def _normalize_address(addr: str) -> str:
    """
    Keep addresses as given, but enforce a canonical lowercase for bech32-like
    and 'system:' prefixes. We do not validate checksums here (wallet/rpc does).
    """
    if addr.startswith("system:"):
        return addr.lower()
    return addr.lower()


# -------------------------
# Genesis validation
# -------------------------


class GenesisError(RuntimeError):
    pass


def _validate_genesis(g: Dict[str, Any], override_chain_id: int | None = None) -> None:
    required_top = ["chainId", "genesisTime", "alloc", "economics", "consensus"]
    for k in required_top:
        if k not in g:
            raise GenesisError(f"genesis missing '{k}'")

    if override_chain_id is not None and g["chainId"] != override_chain_id:
        raise GenesisError(
            f"chainId mismatch: genesis={g['chainId']} override={override_chain_id}"
        )

    if not isinstance(g["alloc"], list):
        raise GenesisError("alloc must be a list of {address, nonce, balance}")

    premine_total_units = int(g["economics"].get("premineTotal", 0))
    # Optional soft-check: sum alloc balances should be <= premineTotal (if present)
    try:
        alloc_sum = 0
        for i, a in enumerate(g["alloc"]):
            if "address" not in a:
                raise GenesisError(f"alloc[{i}] missing address")
            # nonce is optional; default 0
            n = int(a.get("nonce", 0))
            if n < 0:
                raise GenesisError(f"alloc[{i}] nonce negative")
            bal = int(a.get("balance", 0))
            if bal < 0:
                raise GenesisError(f"alloc[{i}] balance negative")
            alloc_sum += bal
        if premine_total_units and alloc_sum > premine_total_units:
            raise GenesisError(
                f"alloc sum ({alloc_sum}) exceeds premineTotal ({premine_total_units})"
            )
    except ValueError as e:
        raise GenesisError(f"alloc values must be integers: {e}")


# -------------------------
# State root computation
# -------------------------


def _account_leaf_hash(address: str, nonce: int, balance: int) -> bytes:
    """
    Canonical leaf hash for state root:
      H( "acct" || 0x00 || CBOR({ "addr": <utf8>, "nonce": uint, "balance": uint }) )
    Keys are not included to keep encoding portable across KV backends.
    """
    body = {
        "addr": address,
        "nonce": int(nonce),
        "balance": int(balance),
    }
    return _sha3_256(b"acct\x00" + cbor.encode(body))


def compute_state_root_from_alloc(alloc: Iterable[Dict[str, Any]]) -> bytes:
    """Compute a deterministic state root from the alloc list (without writing)."""
    leaves: List[bytes] = []
    for a in alloc:
        addr = _normalize_address(a["address"])
        nonce = int(a.get("nonce", 0))
        bal = int(a.get("balance", 0))
        leaves.append(_account_leaf_hash(addr, nonce, bal))
    if not leaves:
        return empty_root()
    # Sort leaves lexicographically (stable & canonical) before building a simple Merkle
    leaves.sort()
    return umerkle.merkle_root(leaves)


# -------------------------
# DB boot
# -------------------------


def _open_kv(db_uri: str) -> KV:
    """
    Open a KV backend based on URI.
      - sqlite:///path/to.db
      - rocksdb:///path/to_dir  (if compiled)
    """
    if db_uri.startswith("sqlite:///"):
        path = db_uri[len("sqlite:///") :]
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        return SQLiteKV(path)
    if db_uri.startswith("rocksdb:///"):
        if RocksDBKV is None:
            raise GenesisError("rocksdb backend requested but not available")
        path = db_uri[len("rocksdb:///") :]
        os.makedirs(path, exist_ok=True)
        return RocksDBKV(path)  # type: ignore
    raise GenesisError(f"Unsupported DB URI: {db_uri}")


def _init_state_from_alloc(state: StateDB, alloc: Iterable[Dict[str, Any]]) -> None:
    """
    Write accounts (nonce, balance) to the state DB in one batch if available.
    StateDB interface is expected to expose upsert_account(address, nonce, balance).
    """
    # Use batch API if the backend provides it for fewer fsyncs.
    if hasattr(state, "batch"):
        with state.batch() as b:
            for a in alloc:
                addr = _normalize_address(a["address"])
                nonce = int(a.get("nonce", 0))
                bal = int(a.get("balance", 0))
                b.upsert_account(addr, nonce=nonce, balance=bal)
    else:  # pragma: no cover
        for a in alloc:
            addr = _normalize_address(a["address"])
            nonce = int(a.get("nonce", 0))
            bal = int(a.get("balance", 0))
            state.upsert_account(addr, nonce=nonce, balance=bal)


# -------------------------
# Header/Block builders
# -------------------------


def _build_genesis_header(
    genesis: Dict[str, Any],
    state_root: bytes,
) -> Header:
    """Compose the genesis Header dataclass with canonical empty roots elsewhere."""
    parent_hash = ZERO32  # no parent at height 0
    txs_root = empty_root()
    receipts_root = empty_root()
    proofs_root = empty_root()
    da_root = empty_root()

    theta_micro = int(genesis["consensus"].get("initialThetaMicro", 1_000_000))
    mix_seed = bytes.fromhex(
        genesis.get("beacon", {}).get("seed", "00" * 32).removeprefix("0x")
    )
    if len(mix_seed) != 32:
        mix_seed = ZERO32

    def _hex32_or_zero(val: str | None) -> bytes:
        if not val:
            return ZERO32
        try:
            b = bytes.fromhex(val.removeprefix("0x"))
            if len(b) == 32:
                return b
        except Exception:
            pass
        return ZERO32

    return Header.genesis(
        chain_id=int(genesis["chainId"]),
        timestamp=_parse_time(genesis["genesisTime"]),
        state_root=state_root,
        txs_root=txs_root,
        receipts_root=receipts_root,
        proofs_root=proofs_root,
        da_root=da_root,
        mix_seed=mix_seed,
        poies_policy_root=_hex32_or_zero(genesis.get("algPolicyRoot")),
        pq_alg_policy_root=ZERO32,
        theta_micro=theta_micro,
        extra=b"",
    )


def _build_genesis_block(h: Header) -> Block:
    """Create an empty genesis block (no txs, no proofs, receipts optional)."""
    return Block(header=h, txs=[], proofs=[], receipts=None)


# -------------------------
# Public API
# -------------------------


def _load_chain_params(
    genesis: Dict[str, Any], params_override: Optional[Mapping[str, Any]]
) -> ChainParams:
    """
    Resolve and load ChainParams referenced by the genesis file.

    The genesis JSON may include a paramsRef.path field; if missing we fall back
    to the repository default params.yaml. Optional overrides are applied
    shallowly using dataclasses.replace for convenience (best-effort).
    """

    params_path = None
    params_ref = genesis.get("paramsRef") or {}
    if isinstance(params_ref, dict) and "path" in params_ref:
        params_path = Path(str(params_ref["path"])).expanduser()
    if params_path is None:
        params_path = default_params_path()

    params = ChainParams.load_yaml(
        params_path, chain_id_hint=int(genesis.get("chainId", 0) or 0)
    )

    if params_override:
        # Only override fields that exist on the dataclass; ignore extras.
        overrides = {k: v for k, v in params_override.items() if hasattr(params, k)}
        if overrides:
            params = replace(params, **overrides)

    return params


def load_genesis(
    genesis_path: str | os.PathLike[str] | None,
    kv: KV | None = None,
    block_db: BlockDB | None = None,
    *,
    params_override: Optional[Mapping[str, Any]] = None,
    log: bool = False,
) -> Tuple[ChainParams, Header]:
    """
    Compatibility wrapper that loads a genesis JSON and returns (ChainParams, Header).

    If a KV is provided, the state DB is pre-seeded with alloc accounts for
    convenience. If a BlockDB is provided, it will be used for any optional
    persistence helpers (current implementation is state-only; head setup is
    handled by core.chain.head.finalize_genesis).
    """

    if genesis_path is None:
        # Default to the bundled genesis.json next to this file.
        here = Path(__file__).resolve().parent
        genesis_path = here / "genesis.json"

    with open(genesis_path, "r", encoding="utf-8") as f:
        genesis = json.load(f)

    params = _load_chain_params(genesis, params_override)

    # Compute state root and header.
    state_root = compute_state_root_from_alloc(genesis["alloc"])
    header = _build_genesis_header(genesis, state_root)

    # Optionally seed state DB for callers that pass a KV handle.
    if kv is not None:
        state = StateDB(kv)
        _init_state_from_alloc(state, genesis["alloc"])

    if log:
        print("[genesis] chainId=%s stateRoot=%s", genesis["chainId"], state_root.hex())

    return params, header


def load_and_init_genesis(
    genesis_path: str,
    db_uri: str,
    *,
    override_chain_id: int | None = None,
    log: bool = False,
) -> Dict[str, Any]:
    """
    Load the genesis file, validate, initialize state, compute state root, build
    and persist the genesis header/block, set canonical head, and return a summary.

    Returns:
        {
          "kv": KV,
          "state": StateDB,
          "blocks": BlockDB,
          "genesis": dict,
          "state_root": bytes,
          "genesis_header": Header,
          "genesis_block": Block,
          "head_height": int,
          "head_hash": bytes
        }
    """
    with open(genesis_path, "r", encoding="utf-8") as f:
        genesis = json.load(f)

    _validate_genesis(genesis, override_chain_id=override_chain_id)

    # Compute state root directly from alloc (pure) to have a deterministic target,
    # then write alloc to the DB and (optionally) re-check root if desired later.
    computed_state_root = compute_state_root_from_alloc(genesis["alloc"])

    # Open KV and wrap DB helpers
    kv = _open_kv(db_uri)
    state = StateDB(kv)
    blocks = BlockDB(kv)

    # Initialize state from alloc
    _init_state_from_alloc(state, genesis["alloc"])

    # Build header & block
    header = _build_genesis_header(genesis, computed_state_root)
    block = _build_genesis_block(header)

    # Persist genesis
    # BlockDB is expected to provide put_genesis(block) that returns (height, hash),
    # otherwise we fall back to put_block(..., height=0) & set_head.
    if hasattr(blocks, "put_genesis"):
        head_height, head_hash = blocks.put_genesis(block)  # type: ignore[attr-defined]
    else:
        # Portable path: encode header canonically, hash, and write under height=0.
        # Expect BlockDB.write_header(height, header_bytes) & set_canonical_head.
        # We keep both branches for compatibility with earlier iterations.
        header_bytes = cbor.encode(asdict(header))
        header_hash = _sha3_256(header_bytes)
        if hasattr(blocks, "write_header"):
            blocks.write_header(0, header)  # type: ignore[attr-defined]
        elif hasattr(blocks, "put_header"):
            blocks.put_header(0, header)  # type: ignore[attr-defined]
        if hasattr(blocks, "set_canonical_head"):
            blocks.set_canonical_head(0, header_hash)  # type: ignore[attr-defined]
        head_height, head_hash = 0, header_hash

    if log:
        print(
            "[genesis] chainId=%s height=%d stateRoot=%s headHash=%s"
            % (
                genesis["chainId"],
                head_height,
                computed_state_root.hex(),
                head_hash.hex(),
            )
        )

    return {
        "kv": kv,
        "state": state,
        "blocks": blocks,
        "genesis": genesis,
        "state_root": computed_state_root,
        "genesis_header": header,
        "genesis_block": block,
        "head_height": head_height,
        "head_hash": head_hash,
    }


# -------------------------
# CLI
# -------------------------


def _main() -> None:  # pragma: no cover - tiny CLI
    ap = argparse.ArgumentParser(description="Animica genesis loader")
    ap.add_argument("--genesis", required=True, help="Path to genesis.json")
    ap.add_argument(
        "--db",
        required=True,
        help="DB URI (sqlite:///path/to.db or rocksdb:///path)",
    )
    ap.add_argument(
        "--chain-id",
        type=int,
        default=None,
        help="Override expected chain id; fail if mismatch",
    )
    args = ap.parse_args()

    env = load_and_init_genesis(
        genesis_path=args.genesis,
        db_uri=args.db,
        override_chain_id=args.chain_id,
        log=True,
    )

    # Pretty-print a minimal head summary as canonical JSON
    out = {
        "chainId": env["genesis"]["chainId"],
        "headHeight": env["head_height"],
        "headHash": "0x" + env["head_hash"].hex(),
        "stateRoot": "0x" + env["state_root"].hex(),
    }
    print(to_canonical_json(out))


if __name__ == "__main__":  # pragma: no cover
    _main()
