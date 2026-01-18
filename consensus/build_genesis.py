#!/usr/bin/env python3
"""
Deterministic Genesis Builder
==============================
CHAIN_RESET_TOUCHPOINT: Genesis block generation tool

Builds a deterministic genesis block from explicit inputs and outputs
a committed artifact (consensus/genesis_output.json) with:
- genesis_header_bytes (CBOR)
- genesis_block_bytes (CBOR)  
- genesis_hash (sha3_256 of header)
- genesis_state_root (merkle root of allocations)
- consensus params snapshot (including target_block_time_sec)

All inputs are fixed and deterministic; running this script multiple times
with the same inputs MUST produce the same genesis_hash.

Usage:
    python consensus/build_genesis.py [--output PATH] [--chain-id ID]
    
Output:
    consensus/genesis_output.json - Complete genesis artifact with all hashes
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from consensus import params as consensus_params
    from core.encoding.cbor import cbor_dumps
    from core.types.header import Header
    from core.types.block import Block
    from core.utils.hash import sha3_256
    from core.utils import merkle as umerkle
    from core.utils.address import address_to_bytes
    from core.genesis.genesis_loader import get_genesis
    from core.genesis.loader import compute_state_root_from_alloc, load_genesis
except ImportError as e:
    print(f"Error: Failed to import required modules: {e}", file=sys.stderr)
    print("Ensure you are running from the repository root with a virtual environment activated.", file=sys.stderr)
    sys.exit(1)


ZERO32 = b"\x00" * 32


def parse_timestamp(ts_str: str) -> int:
    """Parse RFC3339-like timestamp to Unix seconds."""
    ts_str = ts_str.strip()
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    dt_obj = dt.datetime.fromisoformat(ts_str)
    return int(dt_obj.timestamp())


def build_genesis_header(
    *,
    chain_id: int,
    timestamp: int,
    state_root: bytes,
    theta_micro: int,
    alg_policy_root: bytes,
    poies_policy_root: bytes,
) -> Header:
    """
    Build a deterministic genesis header (height=0, parentHash=0x00..00).
    
    For genesis:
    - height = 0
    - parentHash = 0x00...00
    - nonce = 0
    - txsRoot, receiptsRoot, proofsRoot, daRoot = empty roots
    - mixSeed = deterministic seed
    """
    empty_root = sha3_256(b"")
    genesis_mix_seed = sha3_256(b"genesis-mix-seed-v1|" + chain_id.to_bytes(4, "big"))
    
    header = Header.genesis(
        chain_id=chain_id,
        timestamp=timestamp,
        state_root=state_root,
        txs_root=empty_root,
        receipts_root=empty_root,
        proofs_root=empty_root,
        da_root=empty_root,
        mix_seed=genesis_mix_seed,
        poies_policy_root=poies_policy_root,
        pq_alg_policy_root=alg_policy_root,
        theta_micro=theta_micro,
        work_type=0,
        extra=consensus_params.GENESIS_MESSAGE.encode("utf-8"),
    )
    return header


def build_genesis_block(header: Header) -> Block:
    """Build genesis block (header + empty transactions)."""
    # Genesis block has no transactions
    return Block(
        header=header,
        txs=(),
        proofs=(),
        receipts=None,
    )


def compute_genesis_identity(
    genesis_hash: bytes,
    chain_id: int,
    timestamp: int,
    target_block_time_sec: float,
) -> dict[str, Any]:
    """
    Compute genesis identity including fork_id and consensus fingerprint.
    """
    import zlib
    
    # fork_id is derived from genesis_hash (CRC32)
    fork_id = zlib.crc32(genesis_hash) & 0xFFFFFFFF
    
    # Consensus ID fingerprint
    payload = bytearray(b"animica-consensus-id-v1|")
    payload.extend(f"chain_id:{chain_id}|".encode())
    payload.extend(b"genesis:")
    payload.extend(genesis_hash)
    payload.extend(f"|target_block_time:{target_block_time_sec}".encode())
    consensus_id = "consensus/" + sha3_256(bytes(payload)).hex()
    
    return {
        "fork_id": fork_id,
        "fork_id_hex": f"0x{fork_id:08x}",
        "consensus_id": consensus_id,
        "protocol_version": "1.0",
    }


def load_genesis_allocations() -> list[dict[str, Any]]:
    """
    Load genesis allocations from core/genesis/genesis.json.
    
    Falls back to a single premine address if file not found.
    """
    repo_root = Path(__file__).parent.parent
    genesis_json_path = repo_root / "core" / "genesis" / "genesis.json"
    
    if genesis_json_path.exists():
        with open(genesis_json_path) as f:
            genesis = json.load(f)
            alloc = genesis.get("alloc", [])
            if alloc:
                return alloc
    
    # Fallback: single premine address
    return [
        {
            "address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
            "nonce": 0,
            "balance": str(consensus_params.GENESIS_PREMINE_TOTAL),
        }
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Build deterministic genesis block with committed parameters"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("consensus/genesis_output.json"),
        help="Output path for genesis artifact JSON (default: consensus/genesis_output.json)",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=consensus_params.CHAIN_ID,
        help=f"Chain ID (default: {consensus_params.CHAIN_ID})",
    )
    parser.add_argument(
        "--timestamp",
        type=str,
        default=consensus_params.GENESIS_TIMESTAMP_UTC,
        help=f"Genesis timestamp UTC (default: {consensus_params.GENESIS_TIMESTAMP_UTC})",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify output matches committed genesis hash in consensus.params",
    )
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("Animica Deterministic Genesis Builder")
    print("=" * 80)
    print()
    
    genesis_path = Path(__file__).parent.parent / "core" / "genesis" / "genesis.json"
    bundle = get_genesis(genesis_path)
    genesis = bundle.genesis

    # Parse inputs (prefer genesis file as the source of truth)
    chain_id = int(genesis.get("chainId", args.chain_id))
    _params, header = load_genesis(str(genesis_path))
    timestamp = int(getattr(header, "timestamp", parse_timestamp(args.timestamp)))
    target_block_time_sec = float(
        (genesis.get("economics") or {}).get("targetBlockTimeSec", consensus_params.TARGET_BLOCK_TIME_SEC)
    )
    theta_micro = int(getattr(header, "thetaMicro", consensus_params.GENESIS_THETA_MICRO))
    genesis_message = getattr(header, "extra", b"") or b""
    if isinstance(genesis_message, (bytes, bytearray)):
        genesis_message = genesis_message.decode("utf-8", errors="replace")

    print(f"Chain ID:              {chain_id}")
    print(f"Genesis Timestamp:     {genesis.get('genesisTime', args.timestamp)} ({timestamp} unix)")
    print(f"Target Block Time:     {target_block_time_sec} seconds ({target_block_time_sec/60:.1f} minutes)")
    print(f"Initial Theta:         {theta_micro} µ-nats ({theta_micro/1e6:.6f} nats)")
    print(f"Genesis Message:       {genesis_message}")
    print()

    # Load allocations
    print("Loading genesis allocations...")
    alloc = genesis.get("alloc") or load_genesis_allocations()
    print(f"  Loaded {len(alloc)} allocation(s)")
    total_balance = sum(int(a.get("balance", 0)) for a in alloc)
    print(f"  Total premine: {total_balance} base units ({total_balance/1e9:.2f} ANM)")
    print()

    # Compute state root
    print("Computing state root...")
    state_root = getattr(header, "stateRoot", None) or compute_state_root_from_alloc(alloc)
    if isinstance(state_root, str):
        state_root = bytes.fromhex(state_root.removeprefix("0x"))
    print(f"  State root: 0x{state_root.hex()}")
    print()

    # Build header
    print("Building genesis header...")
    print(f"  Height:          {header.height}")
    print(f"  Chain ID:        {header.chainId}")
    print(f"  Timestamp:       {header.timestamp}")
    print(f"  Theta (µ-nats):  {header.thetaMicro}")
    print()
    
    # Compute genesis hash
    print("Computing genesis hash...")
    header_cbor = cbor_dumps(header)
    genesis_hash = sha3_256(header_cbor)
    genesis_hash_hex = "0x" + genesis_hash.hex()
    print(f"  Genesis hash: {genesis_hash_hex}")
    print()
    
    # Build block
    print("Building genesis block...")
    block = build_genesis_block(header)
    block_cbor = cbor_dumps(block)
    print(f"  Header size: {len(header_cbor)} bytes")
    print(f"  Block size:  {len(block_cbor)} bytes")
    print()
    
    # Compute identity
    print("Computing genesis identity...")
    identity = compute_genesis_identity(
        genesis_hash=genesis_hash,
        chain_id=chain_id,
        timestamp=timestamp,
        target_block_time_sec=target_block_time_sec,
    )
    print(f"  Fork ID:        {identity['fork_id_hex']}")
    print(f"  Consensus ID:   {identity['consensus_id'][:60]}...")
    print()
    
    # Build output artifact
    output = {
        "meta": {
            "version": "1.0",
            "builder": "consensus/build_genesis.py",
            "description": "Deterministic genesis for Animica reset 2026",
        },
        "inputs": {
            "chain_id": chain_id,
            "timestamp_utc": genesis.get("genesisTime", args.timestamp),
            "timestamp_unix": timestamp,
            "target_block_time_sec": target_block_time_sec,
            "genesis_message": genesis_message,
            "theta_micro": theta_micro,
            "premine_total": total_balance,
        },
        "outputs": {
            "genesis_hash": genesis_hash_hex,
            "genesis_header_cbor_hex": "0x" + header_cbor.hex(),
            "genesis_block_cbor_hex": "0x" + block_cbor.hex(),
            "state_root": "0x" + state_root.hex(),
        },
        "identity": identity,
        "consensus_params": consensus_params.get_consensus_params_dict(),
        "allocations": alloc,
    }
    
    # Write output
    print(f"Writing genesis artifact to {args.output}...")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"  Wrote {args.output}")
    print()
    
    # Verify if requested
    if args.verify:
        print("Verifying against committed genesis hash...")
        committed_hash = consensus_params.GENESIS_HASH_HEX
        if genesis_hash_hex == committed_hash:
            print(f"  ✅ MATCH: Genesis hash matches committed constant")
        else:
            print(f"  ❌ MISMATCH:")
            print(f"     Computed:  {genesis_hash_hex}")
            print(f"     Committed: {committed_hash}")
            print()
            print("  ACTION REQUIRED: Update consensus.params.GENESIS_HASH_HEX with the computed value:")
            print(f'  GENESIS_HASH_HEX = "{genesis_hash_hex}"')
            return 1  # Return error code instead of sys.exit
    else:
        print("NOTE: To commit this genesis, update consensus.params.GENESIS_HASH_HEX:")
        print(f'  GENESIS_HASH_HEX = "{genesis_hash_hex}"')
    
    print()
    print("=" * 80)
    print("Genesis build complete!")
    print("=" * 80)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
