#!/usr/bin/env python3
"""
deploy_counter.py — End-to-end example using omni_sdk (Python)

What this script does:
1) Loads the canonical Counter example (manifest + contract.py).
2) Builds and signs a *deploy* transaction with a PQ signer (Dilithium3 by default).
3) Submits it via JSON-RPC and waits for the receipt.
4) Calls `get` (read/simulate), then sends a signed `inc` transaction, waits for the receipt,
   and calls `get` again to show the increment.

Requirements
------------
- A node exposing JSON-RPC over HTTP and WebSocket (devnet/testnet is fine).
- The SDK installed (editable is OK): `python -m pip install -e ./sdk/python`

Defaults can be overridden via flags or environment:

  OMNI_SDK_RPC_URL        (default: http://127.0.0.1:8545)
  OMNI_CHAIN_ID           (default: 1)
  OMNI_SDK_HTTP_TIMEOUT   (default: 30)
  OMNI_SDK_SEED_HEX       (dev/test only; hex seed for signer)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from omni_sdk.address import Address
from omni_sdk.contracts.client import ContractClient
# SDK imports
from omni_sdk.rpc.http import RpcClient
from omni_sdk.tx.build import build_deploy_tx, estimate_deploy_gas
from omni_sdk.tx.encode import encode_tx_cbor
from omni_sdk.tx.send import await_receipt, send_raw_transaction
from omni_sdk.wallet.signer import Signer


def _repo_root() -> Path:
    # Resolve to repository root (assumes this file lives at sdk/python/examples/)
    return Path(__file__).resolve().parents[3]


def _default_paths() -> Dict[str, Path]:
    root = _repo_root()
    manifest = root / "vm_py" / "examples" / "counter" / "manifest.json"
    code = root / "vm_py" / "examples" / "counter" / "contract.py"
    return {"manifest": manifest, "code": code}


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        sys.exit(f"error: manifest not found: {path}")
    except Exception as e:
        sys.exit(f"error: invalid JSON manifest at {path}: {e}")


def _load_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as e:
        sys.exit(f"error: code file not found: {path}")


def _make_signer(alg: str, seed_hex: Optional[str]) -> Signer:
    if not seed_hex:
        seed_hex = os.getenv("OMNI_SDK_SEED_HEX")
    if not seed_hex:
        sys.exit(
            "error: signer seed missing. Pass --seed-hex or set OMNI_SDK_SEED_HEX "
            "(dev/test only; never use real keys here)."
        )
    try:
        seed = bytes.fromhex(seed_hex.strip().removeprefix("0x"))
    except Exception:
        sys.exit("error: --seed-hex must be hex (with or without 0x prefix)")
    return Signer.from_seed(seed, alg=alg)  # type: ignore[attr-defined]


def main() -> None:
    defaults = _default_paths()

    ap = argparse.ArgumentParser(
        description="Deploy the Counter contract and call inc/get"
    )
    ap.add_argument(
        "--rpc",
        default=os.getenv("OMNI_SDK_RPC_URL", "http://127.0.0.1:8545"),
        help="RPC HTTP URL",
    )
    ap.add_argument(
        "--chain-id",
        type=int,
        default=int(os.getenv("OMNI_CHAIN_ID", "1")),
        help="Chain ID",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("OMNI_SDK_HTTP_TIMEOUT", "30")),
        help="HTTP timeout (s)",
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=defaults["manifest"],
        help="Path to manifest.json",
    )
    ap.add_argument(
        "--code",
        type=Path,
        default=defaults["code"],
        help="Path to contract.py (or IR bytes)",
    )
    ap.add_argument(
        "--seed-hex",
        default=os.getenv("OMNI_SDK_SEED_HEX"),
        help="Signer seed as hex (dev/test only)",
    )
    ap.add_argument(
        "--alg",
        default="dilithium3",
        choices=["dilithium3", "sphincs_shake_128s"],
        help="PQ signature algorithm",
    )
    ap.add_argument(
        "--gas-price", type=int, default=None, help="Optional gas price override"
    )
    ap.add_argument(
        "--gas-limit", type=int, default=None, help="Optional gas limit override"
    )
    ap.add_argument(
        "--nonce", type=int, default=None, help="Optional sender nonce override"
    )
    ap.add_argument(
        "--wait-seconds",
        type=float,
        default=120.0,
        help="Max seconds to wait for each receipt",
    )
    args = ap.parse_args()

    # RPC client
    rpc = RpcClient(args.rpc, timeout=args.timeout)

    # Inputs
    manifest = _load_json(args.manifest)
    code_bytes = _load_bytes(args.code)

    # Signer & sender address
    signer = _make_signer(args.alg, args.seed_hex)
    sender = Address.from_public_key(signer.public_key_bytes(), alg=signer.alg_id).bech32  # type: ignore

    print("== Deploying Counter ==")
    print(f"rpc={args.rpc} chainId={args.chain_id} sender={sender} alg={signer.alg_id}")

    # Estimate gas if not provided
    gas_limit = args.gas_limit
    if gas_limit is None:
        try:
            ge = estimate_deploy_gas(rpc, manifest, code_bytes, sender=sender)
            gas_limit = (
                int(ge["gasLimit"])
                if isinstance(ge, dict) and "gasLimit" in ge
                else int(ge)
            )
        except Exception:
            gas_limit = 1_000_000  # conservative fallback

    # Build deploy tx
    tx = build_deploy_tx(
        chain_id=args.chain_id,
        sender=sender,
        manifest=manifest,
        code=code_bytes,
        gas_price=args.gas_price,
        gas_limit=gas_limit,
        nonce=args.nonce,
    )

    # Sign & submit
    sig = signer.sign(tx.sign_bytes, domain="tx")  # domain separation handled by Signer
    tx.attach_signature(alg_id=signer.alg_id, signature=sig)
    raw = encode_tx_cbor(tx)
    tx_hash = send_raw_transaction(rpc, raw)
    print(f"submitted deploy tx: {tx_hash}")

    # Await receipt
    receipt = await_receipt(rpc, tx_hash, timeout_seconds=args.wait_seconds)
    status = receipt.get("status")
    contract_addr = receipt.get("contractAddress")
    print(
        "deploy receipt:",
        json.dumps(
            {
                "status": status,
                "contractAddress": contract_addr,
                "gasUsed": receipt.get("gasUsed"),
            },
            indent=2,
        ),
    )

    if not contract_addr:
        sys.exit(
            "error: deploy succeeded but no contractAddress in receipt; cannot continue"
        )

    # Contract client
    abi = manifest.get("abi", [])
    cc = ContractClient(rpc, address=contract_addr, abi=abi, chain_id=args.chain_id)

    # Read current value
    before = cc.read("get")
    print("counter before:", before)

    # Estimate gas for inc
    call_gas = 200_000
    try:
        call_gas = int(cc.estimate_gas("inc", sender=sender))  # type: ignore[attr-defined]
    except Exception:
        pass

    # Build + sign + send inc()
    call_tx = cc.build_tx("inc", sender=sender, gas_limit=call_gas)  # type: ignore[attr-defined]
    sig2 = signer.sign(call_tx.sign_bytes, domain="tx")
    call_tx.attach_signature(alg_id=signer.alg_id, signature=sig2)
    call_raw = encode_tx_cbor(call_tx)
    call_hash = send_raw_transaction(rpc, call_raw)
    print(f"submitted inc() tx: {call_hash}")

    call_receipt = await_receipt(rpc, call_hash, timeout_seconds=args.wait_seconds)
    print(
        "inc receipt:",
        json.dumps(
            {
                "status": call_receipt.get("status"),
                "gasUsed": call_receipt.get("gasUsed"),
            },
            indent=2,
        ),
    )

    # Read new value
    after = cc.read("get")
    print("counter after:", after)

    print("\n== Summary ==")
    print(
        json.dumps(
            {
                "deployTx": tx_hash,
                "contract": contract_addr,
                "incTx": call_hash,
                "valueBefore": before,
                "valueAfter": after,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
