#!/usr/bin/env python3
"""
Deploy the canonical counter example and optionally call it.

This script demonstrates the current SDK surface:
  - deploy via `omni_sdk.contracts.deployer.deploy_package`
  - send state-changing calls via tx build/encode/send helpers
  - read via simulate RPC methods when available
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from omni_sdk.address import from_pubkey
from omni_sdk.contracts.deployer import build_deploy_tx, deploy_package, make_package_bytes
from omni_sdk.rpc.http import RpcClient
from omni_sdk.tx import build as tx_build
from omni_sdk.tx import encode as tx_encode
from omni_sdk.tx import send as tx_send
from omni_sdk.types.abi import decode_return, encode_call
from omni_sdk.wallet.signer import PQSigner


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_paths() -> Dict[str, Path]:
    root = _repo_root()
    return {
        "manifest": root / "vm_py" / "examples" / "counter" / "manifest.json",
        "code": root / "vm_py" / "examples" / "counter" / "contract.py",
    }


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"error: file not found: {path}")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"error: invalid JSON in {path}: {exc}")
    if not isinstance(data, dict):
        sys.exit(f"error: expected JSON object in {path}")
    return data


def _load_code_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        sys.exit(f"error: code file not found: {path}")


def _coerce_abi_for_calls(abi_value: Any) -> Any:
    if not isinstance(abi_value, dict):
        return abi_value
    funcs = abi_value.get("functions", [])
    events = abi_value.get("events", [])
    if not isinstance(funcs, list) or not isinstance(events, list):
        return abi_value
    entries = []
    for fn in funcs:
        if isinstance(fn, dict):
            item = dict(fn)
            item.setdefault("type", "function")
            entries.append(item)
    for ev in events:
        if isinstance(ev, dict):
            item = dict(ev)
            item.setdefault("type", "event")
            entries.append(item)
    return entries


def _make_signer(alg: str, seed_hex: Optional[str]) -> PQSigner:
    seed_input = seed_hex or os.getenv("OMNI_SDK_SEED_HEX")
    if not seed_input:
        sys.exit(
            "error: signer seed missing; pass --seed-hex or set OMNI_SDK_SEED_HEX (dev/test only)"
        )
    try:
        seed = bytes.fromhex(seed_input.strip().removeprefix("0x"))
    except Exception:  # noqa: BLE001
        sys.exit("error: --seed-hex must be hex (with or without 0x)")
    try:
        signer = PQSigner.from_seed(alg, seed=seed)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"error: failed to create signer: {exc}")
    return signer


def _rpc_nonce(rpc: RpcClient, sender: str, override: Optional[int]) -> int:
    if override is not None:
        return int(override)
    try:
        return int(rpc.request("state.getNonce", [sender]))
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"error: failed to fetch nonce for {sender}: {exc}")


def _rpc_simulate(
    rpc: RpcClient, *, to: str, data: bytes, sender: Optional[str] = None
) -> Optional[bytes]:
    payload = {"to": to, "data": "0x" + data.hex()}
    if sender:
        payload["from"] = sender

    candidates = (
        ("execution.simulateCall", [payload]),
        ("state.call", [payload]),
        ("vm.simulateCall", [to, "0x" + data.hex(), sender, None]),
    )
    for method, params in candidates:
        try:
            out = rpc.request(method, params)
        except Exception:
            continue
        if isinstance(out, str) and out.startswith("0x"):
            try:
                return bytes.fromhex(out[2:])
            except Exception:
                return None
        if isinstance(out, (bytes, bytearray)):
            return bytes(out)
    return None


def _send_inc(
    *,
    rpc: RpcClient,
    signer: PQSigner,
    contract_address: str,
    chain_id: int,
    nonce: int,
    max_fee: int,
    gas_limit: Optional[int],
    abi: Any,
) -> Dict[str, Any]:
    sender = signer.address or from_pubkey(
        signer.public_key, alg_id=signer.alg_id, hrp="anim"
    )
    calldata = encode_call(abi, "inc", [])
    tx = tx_build.call(
        from_addr=sender,
        to_addr=contract_address,
        data=calldata,
        nonce=nonce,
        gas_limit=gas_limit,
        max_fee=max_fee,
        chain_id=chain_id,
        value=0,
    )
    sign_bytes = tx_encode.sign_bytes(tx)
    sig = signer.sign(sign_bytes)
    raw = tx_encode.pack_signed(
        tx,
        signature=sig,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    tx_hash = tx_send.submit_raw(rpc, raw)
    receipt = tx_send.wait_for_receipt(rpc, tx_hash, timeout_s=120.0)
    return {"txHash": tx_hash, "receipt": receipt}


def main() -> None:
    defaults = _default_paths()

    parser = argparse.ArgumentParser(
        description="Deploy vm_py/examples/counter and call inc()"
    )
    parser.add_argument(
        "--rpc",
        default=os.getenv("OMNI_SDK_RPC_URL", "http://127.0.0.1:8545"),
        help="RPC URL",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=int(os.getenv("OMNI_CHAIN_ID", "1")),
        help="Chain ID",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("OMNI_SDK_HTTP_TIMEOUT", "30")),
        help="RPC timeout seconds",
    )
    parser.add_argument("--manifest", type=Path, default=defaults["manifest"])
    parser.add_argument("--code", type=Path, default=defaults["code"])
    parser.add_argument("--seed-hex", default=os.getenv("OMNI_SDK_SEED_HEX"))
    parser.add_argument(
        "--sender",
        default=None,
        help="Optional sender address override (useful for --dry-run without PQ keys)",
    )
    parser.add_argument(
        "--alg",
        default="dilithium3",
        choices=("dilithium3", "sphincs_shake_128s"),
    )
    parser.add_argument(
        "--max-fee",
        type=int,
        default=1,
        help="Transaction max_fee to use for deploy/call",
    )
    parser.add_argument("--gas-limit", type=int, default=None)
    parser.add_argument(
        "--nonce",
        type=int,
        default=None,
        help="Optional deploy nonce override (call nonce will use deploy nonce+1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build deploy transaction locally without sending to RPC",
    )
    parser.add_argument(
        "--skip-call",
        action="store_true",
        help="Only deploy; skip post-deploy read/inc/read flow",
    )
    args = parser.parse_args()

    manifest = _load_json(args.manifest)
    code = _load_code_bytes(args.code)
    abi = _coerce_abi_for_calls(manifest.get("abi", []))

    signer: Optional[PQSigner]
    if args.dry_run and args.sender:
        signer = None
        sender = args.sender
    else:
        signer = _make_signer(args.alg, args.seed_hex)
        sender = signer.address or from_pubkey(
            signer.public_key, alg_id=signer.alg_id, hrp="anim"
        )

    if args.dry_run:
        dry_nonce = int(args.nonce if args.nonce is not None else 0)
        package = make_package_bytes(manifest=manifest, code=code)
        tx = build_deploy_tx(
            from_addr=sender,
            chain_id=int(args.chain_id),
            nonce=dry_nonce,
            max_fee=int(args.max_fee),
            package_bytes=package,
            gas_limit=args.gas_limit,
        )
        sign_bytes = tx_encode.sign_bytes(tx)
        print(
            json.dumps(
                {
                    "dryRun": True,
                    "sender": sender,
                    "chainId": int(args.chain_id),
                    "nonce": dry_nonce,
                    "packageBytes": len(package),
                    "signBytesLen": len(sign_bytes),
                },
                indent=2,
            )
        )
        return

    rpc = RpcClient(args.rpc, timeout=args.timeout)

    if signer is None:
        sys.exit("error: signer is required for non-dry-run deployment")

    deploy_nonce = _rpc_nonce(rpc, sender, args.nonce)
    contract_addr, deploy_receipt = deploy_package(
        rpc=rpc,
        signer=signer,
        manifest=manifest,
        code=code,
        chain_id=args.chain_id,
        nonce=deploy_nonce,
        max_fee=int(args.max_fee),
        gas_limit=args.gas_limit,
        await_receipt=True,
        timeout_s=120.0,
    )

    if not contract_addr:
        sys.exit(
            "error: deploy completed but receipt did not include a contract address"
        )

    summary: Dict[str, Any] = {
        "sender": sender,
        "deploy": {
            "txHash": deploy_receipt.get("txHash"),
            "status": deploy_receipt.get("status"),
            "gasUsed": deploy_receipt.get("gasUsed"),
            "contractAddress": contract_addr,
        },
    }

    if not args.skip_call:
        get_data = encode_call(abi, "get", [])
        before_raw = _rpc_simulate(rpc, to=contract_addr, data=get_data, sender=sender)
        before = decode_return(abi, "get", before_raw) if before_raw is not None else None

        call_nonce = deploy_nonce + 1
        call_out = _send_inc(
            rpc=rpc,
            signer=signer,
            contract_address=contract_addr,
            chain_id=args.chain_id,
            nonce=call_nonce,
            max_fee=int(args.max_fee),
            gas_limit=args.gas_limit,
            abi=abi,
        )

        after_raw = _rpc_simulate(rpc, to=contract_addr, data=get_data, sender=sender)
        after = decode_return(abi, "get", after_raw) if after_raw is not None else None

        summary["call"] = {
            "incTxHash": call_out["txHash"],
            "incStatus": call_out["receipt"].get("status"),
            "incGasUsed": call_out["receipt"].get("gasUsed"),
            "getBefore": before,
            "getAfter": after,
            "simulateAvailable": before_raw is not None or after_raw is not None,
        }

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
