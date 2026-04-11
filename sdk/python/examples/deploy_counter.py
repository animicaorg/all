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
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from omni_sdk.address import from_pubkey
from omni_sdk.contracts.deployer import build_deploy_tx, deploy_package, make_package_bytes
from omni_sdk.errors import RpcError
from omni_sdk.rpc.http import RpcClient
from omni_sdk.tx import build as tx_build
from omni_sdk.tx import encode as tx_encode
from omni_sdk.tx import send as tx_send
from omni_sdk.tx import signing as tx_signing
from omni_sdk.types.abi import decode_return, encode_call
from omni_sdk.wallet.signer import PQSigner

ALG_BY_ID = {
    0x1001: "dilithium3",
    0x1002: "sphincs_shake_128s",
}

ALG_ALIASES = {
    "dilithium": "dilithium3",
    "dilithium-3": "dilithium3",
    "dilithium3": "dilithium3",
    "ml-dsa-65": "dilithium3",
    "mldsa65": "dilithium3",
    "sphincs": "sphincs_shake_128s",
    "sphincs+": "sphincs_shake_128s",
    "sphincs+-shake-128s": "sphincs_shake_128s",
    "sphincs_shake_128s": "sphincs_shake_128s",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_rpc_url() -> str:
    for key in ("OMNI_RPC_URL", "OMNI_SDK_RPC_URL", "ANIMICA_RPC_URL"):
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return "http://127.0.0.1:8545/rpc"


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


def _address_from_pubkey_canonical(public_key: bytes, alg_id: int) -> str:
    try:
        from pq.py.address import address_from_pubkey

        return address_from_pubkey(public_key, int(alg_id), hrp="anim")
    except Exception:
        return from_pubkey(public_key, alg_id=int(alg_id), hrp="anim")


def _sender_from_signer(signer: PQSigner) -> str:
    return signer.address or _address_from_pubkey_canonical(
        signer.public_key, signer.alg_id
    )


def _normalize_hex(value: str, *, field: str) -> str:
    raw = value.strip()
    if raw.startswith(("0x", "0X")):
        raw = raw[2:]
    if not raw:
        sys.exit(f"error: {field} cannot be empty")
    if len(raw) % 2 != 0:
        sys.exit(f"error: {field} must contain an even number of hex characters")
    try:
        bytes.fromhex(raw)
    except Exception:  # noqa: BLE001
        sys.exit(f"error: {field} must be valid hex")
    return raw


def _parse_seed_hex(seed_input: str) -> bytes:
    raw = _normalize_hex(seed_input, field="--seed-hex")
    seed = bytes.fromhex(raw)
    if len(seed) != 32:
        hint = ""
        if len(seed) == 64:
            hint = (
                "; this looks like 64-byte key material, not a 32-byte seed. "
                "Use --wallet-label/--wallet-file for existing wallets."
            )
        sys.exit(
            f"error: --seed-hex expects a 32-byte seed (64 hex chars); got {len(seed)} bytes{hint}"
        )
    return seed


def _normalize_alg_name(raw_name: str) -> str:
    normalized = ALG_ALIASES.get(raw_name.strip().lower(), raw_name.strip().lower())
    if normalized not in ("dilithium3", "sphincs_shake_128s"):
        sys.exit(
            f"error: unsupported algorithm '{raw_name}' "
            "(supported: dilithium3, sphincs_shake_128s)"
        )
    return normalized


def _parse_alg_id(raw_value: Any) -> Optional[int]:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        value = raw_value.strip().lower()
        if not value:
            return None
        try:
            return int(value, 16) if value.startswith("0x") else int(value)
        except Exception:  # noqa: BLE001
            sys.exit(f"error: invalid wallet alg_id '{raw_value}'")
    try:
        return int(raw_value)
    except Exception:  # noqa: BLE001
        sys.exit(f"error: invalid wallet alg_id '{raw_value}'")


def _wallet_file_path(path_arg: Optional[Path]) -> Path:
    if path_arg is not None:
        return path_arg.expanduser()
    env_path = os.getenv("ANIMICA_WALLETS_FILE")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".animica" / "wallets.json"


def _wallet_entries(path: Path) -> List[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"error: wallet file not found: {path}")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"error: invalid JSON in wallet file {path}: {exc}")

    if isinstance(raw, dict) and isinstance(raw.get("wallets"), list):
        entries = raw["wallets"]
    elif isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = []
        for label, value in raw.items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item.setdefault("label", str(label))
            entries.append(item)
    else:
        sys.exit(
            f"error: unsupported wallet file format in {path}; expected an object with 'wallets' list"
        )

    return [item for item in entries if isinstance(item, dict)]


def _find_wallet_by_label(path: Path, label: str) -> Dict[str, Any]:
    needle = label.strip().lower()
    if not needle:
        sys.exit("error: --wallet-label cannot be empty")
    for item in _wallet_entries(path):
        item_label = str(item.get("label") or "").strip().lower()
        if item_label == needle:
            return item
    sys.exit(f"error: wallet label '{label}' not found in {path}")


def _wallet_alg_name(entry: Dict[str, Any]) -> str:
    name_raw = entry.get("alg_name") or entry.get("algName")
    alg_id = _parse_alg_id(entry.get("alg_id", entry.get("algId", entry.get("alg"))))
    name_from_id = ALG_BY_ID.get(int(alg_id)) if alg_id is not None else None
    if isinstance(name_raw, str) and name_raw.strip():
        normalized_name = _normalize_alg_name(name_raw)
        if name_from_id and name_from_id != normalized_name:
            sys.exit(
                "error: wallet algorithm mismatch: "
                f"alg_id {alg_id} maps to {name_from_id}, but alg_name is {name_raw!r}"
            )
        return normalized_name
    if name_from_id:
        return name_from_id
    sys.exit(
        "error: wallet entry missing supported alg_id/alg_name "
        "(expected dilithium3 or sphincs_shake_128s)"
    )


def _wallet_key_bytes(
    entry: Dict[str, Any], *, field_name: str, aliases: tuple[str, ...]
) -> bytes:
    for key in aliases:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return bytes.fromhex(_normalize_hex(value, field=f"wallet.{field_name}"))
    sys.exit(f"error: wallet entry missing {field_name}")


def _make_signer_from_seed(alg: str, seed_hex: str) -> PQSigner:
    seed = _parse_seed_hex(seed_hex)
    try:
        signer = PQSigner.from_seed(alg, seed=seed)
    except ValueError as exc:
        sys.exit(f"error: {exc}")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"error: failed to create signer: {exc}")
    return signer


def _make_signer_from_wallet(path: Path, label: str) -> PQSigner:
    entry = _find_wallet_by_label(path, label)
    alg_name = _wallet_alg_name(entry)
    public_key = _wallet_key_bytes(
        entry,
        field_name="public_key_hex",
        aliases=("public_key_hex", "publicKeyHex", "pubkey", "pk"),
    )
    secret_key = _wallet_key_bytes(
        entry,
        field_name="secret_key_hex",
        aliases=("secret_key_hex", "secretKeyHex"),
    )

    try:
        signer = PQSigner.from_keypair(
            alg_name=alg_name,
            secret_key=secret_key,
            public_key=public_key,
        )
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"error: failed to create signer from wallet '{label}': {exc}")

    derived = _sender_from_signer(signer)
    wallet_address = str(entry.get("address") or "").strip()
    if not wallet_address:
        sys.exit(
            f"error: wallet '{label}' in {path} is missing address; cannot verify derived address"
        )
    if derived.lower() != wallet_address.lower():
        sys.exit(
            "error: wallet address mismatch: "
            f"wallet '{label}' has {wallet_address}, derived signer address is {derived}"
        )

    wallet_alg_id = _parse_alg_id(
        entry.get("alg_id", entry.get("algId", entry.get("alg")))
    )
    if wallet_alg_id is not None and int(wallet_alg_id) != int(signer.alg_id):
        sys.exit(
            "error: wallet algorithm ID mismatch: "
            f"wallet '{label}' has alg_id={wallet_alg_id}, derived signer has alg_id={signer.alg_id}"
        )
    return signer


def _rpc_nonce(rpc: RpcClient, sender: str, override: Optional[int]) -> int:
    if override is not None:
        return int(override)
    errors: List[str] = []
    for params in ([sender, "pending"], [sender]):
        try:
            return int(rpc.request("state.getNonce", params))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"state.getNonce({params!r}) failed: {exc}")
    sys.exit(f"error: failed to fetch nonce for {sender}: {'; '.join(errors)}")


def _normalize_tx_hash(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    out = value.strip()
    if not out:
        return None
    if not out.startswith("0x"):
        out = "0x" + out
    return out


def _extract_tx_hash_from_text(text: str) -> Optional[str]:
    match = re.search(r"0x[0-9a-fA-F]{64}", text)
    if not match:
        return None
    return _normalize_tx_hash(match.group(0))


def _extract_replay_info(exc: Exception) -> Optional[Dict[str, Any]]:
    if not isinstance(exc, RpcError):
        return None

    payload = exc.data if isinstance(exc.data, dict) else {}
    mempool_error = (
        payload.get("mempoolError")
        if isinstance(payload.get("mempoolError"), dict)
        else {}
    )
    ctx = mempool_error.get("context") if isinstance(mempool_error.get("context"), dict) else {}
    reason = str(
        mempool_error.get("reason_code")
        or mempool_error.get("reason")
        or payload.get("reason")
        or exc.message
        or ""
    ).lower()
    if "replay" not in reason and "duplicate" not in reason:
        return None

    tx_hash = _normalize_tx_hash(
        ctx.get("tx_hash")
        or mempool_error.get("tx_hash")
        or payload.get("tx_hash")
        or payload.get("hash")
        or payload.get("txHash")
    )
    return {
        "reason": reason,
        "txHash": tx_hash,
        "mempoolError": mempool_error,
    }


def _collect_replay_diagnostics(
    rpc: RpcClient,
    *,
    sender: str,
    tx_hash: Optional[str],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"txHash": tx_hash}
    if tx_hash:
        try:
            receipt = rpc.request("tx.getTransactionReceipt", [tx_hash])
            out["receipt"] = receipt
            out["receiptFound"] = bool(receipt)
        except Exception as exc:  # noqa: BLE001
            out["receiptError"] = str(exc)
        try:
            out["txStatus"] = rpc.request("tx.getStatus", [tx_hash])
        except Exception as exc:  # noqa: BLE001
            out["txStatusError"] = str(exc)
    try:
        out["nonceLatest"] = int(rpc.request("state.getNonce", [sender]))
    except Exception as exc:  # noqa: BLE001
        out["nonceLatestError"] = str(exc)
    try:
        out["noncePending"] = int(rpc.request("state.getNonce", [sender, "pending"]))
    except Exception as exc:  # noqa: BLE001
        out["noncePendingError"] = str(exc)
    return out


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
    sender = _sender_from_signer(signer)
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
    signed = tx_signing.sign_transaction_with_rpc_context(
        tx,
        signer,
        chain_id=int(chain_id),
        rpc=rpc,
    )
    tx_hash = tx_send.submit_raw(rpc, signed.raw_tx)
    receipt = tx_send.wait_for_receipt(rpc, tx_hash, timeout_s=120.0)
    return {"txHash": tx_hash, "receipt": receipt}


def main() -> None:
    defaults = _default_paths()

    parser = argparse.ArgumentParser(
        description="Deploy vm_py/examples/counter and call inc()"
    )
    parser.add_argument(
        "--rpc",
        default=_default_rpc_url(),
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
    parser.add_argument(
        "--seed-hex",
        default=None,
        help="Signer seed as hex; expects exactly 32 bytes (64 hex chars)",
    )
    parser.add_argument(
        "--wallet-file",
        type=Path,
        default=None,
        help="Path to wallets.json (default: $ANIMICA_WALLETS_FILE or ~/.animica/wallets.json)",
    )
    parser.add_argument(
        "--wallet-label",
        default=None,
        help="Use signer from an existing wallet label in wallets.json",
    )
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

    if args.sender and not args.dry_run:
        sys.exit("error: --sender is only valid with --dry-run")
    if args.seed_hex and args.wallet_label:
        sys.exit("error: choose exactly one signer source: --seed-hex or --wallet-label")
    if args.sender and (args.seed_hex or args.wallet_label):
        sys.exit("error: --sender cannot be combined with --seed-hex or --wallet-label")

    signer: Optional[PQSigner] = None
    signer_source = "unknown"
    env_seed = os.getenv("OMNI_SDK_SEED_HEX")

    if args.wallet_label:
        wallet_file = _wallet_file_path(args.wallet_file)
        signer = _make_signer_from_wallet(wallet_file, args.wallet_label)
        signer_source = f"wallet:{args.wallet_label}@{wallet_file}"
        sender = _sender_from_signer(signer)
    elif args.seed_hex:
        signer = _make_signer_from_seed(args.alg, args.seed_hex)
        signer_source = "seed"
        sender = _sender_from_signer(signer)
    elif args.dry_run and args.sender:
        signer_source = "sender-override"
        sender = args.sender
    elif env_seed:
        signer = _make_signer_from_seed(args.alg, env_seed)
        signer_source = "seed:OMNI_SDK_SEED_HEX"
        sender = _sender_from_signer(signer)
    else:
        sys.exit(
            "error: signer material missing; use --seed-hex (32-byte seed) "
            "or --wallet-label/--wallet-file for an existing wallet"
        )

    if signer is not None:
        print(
            f"signer source={signer_source} alg={signer.alg_name} alg_id={signer.alg_id} sender={sender}"
        )
    else:
        print(f"signer source={signer_source} sender={sender}")

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
                    "signerSource": signer_source,
                    "sender": sender,
                    "chainId": int(args.chain_id),
                    "nonce": dry_nonce,
                    "packageBytes": len(package),
                    "signBytesLen": len(sign_bytes),
                    "algName": signer.alg_name if signer is not None else None,
                    "algId": signer.alg_id if signer is not None else None,
                },
                indent=2,
            )
        )
        return

    rpc = RpcClient(args.rpc, timeout=args.timeout)

    if signer is None:
        sys.exit("error: signer is required for non-dry-run deployment")

    deploy_nonce = _rpc_nonce(rpc, sender, args.nonce)
    try:
        contract_addr, deploy_receipt = deploy_package(
            rpc=rpc,
            signer=signer,
            from_addr=sender,
            manifest=manifest,
            code=code,
            chain_id=args.chain_id,
            nonce=deploy_nonce,
            max_fee=int(args.max_fee),
            gas_limit=args.gas_limit,
            await_receipt=True,
            timeout_s=120.0,
        )
    except TimeoutError as exc:
        timeout_text = str(exc)
        tx_hash = _extract_tx_hash_from_text(timeout_text)
        diagnostics = _collect_replay_diagnostics(
            rpc,
            sender=sender,
            tx_hash=tx_hash,
        )
        print(
            json.dumps(
                {
                    "error": "receipt_timeout",
                    "message": "deployment transaction was accepted but receipt is not indexed yet",
                    "sender": sender,
                    "nonceUsed": deploy_nonce,
                    "txHash": tx_hash,
                    "details": timeout_text,
                    "diagnostics": diagnostics,
                    "nextSteps": [
                        "Use txHash with tx.getStatus and tx.getTransactionReceipt until receipt appears.",
                        "If txStatus is pending, wait for inclusion and retry receipt query.",
                        "If txStatus is accepted/finalized but receipt is still missing, inspect node indexing logs.",
                    ],
                },
                indent=2,
            )
        )
        raise SystemExit(3)
    except Exception as exc:  # noqa: BLE001
        replay_info = _extract_replay_info(exc)
        if replay_info is None:
            raise
        diagnostics = _collect_replay_diagnostics(
            rpc,
            sender=sender,
            tx_hash=replay_info.get("txHash"),
        )
        next_steps = [
            "If receiptFound=true, deployment already confirmed; reuse contractAddress from receipt/logs.",
            "If txStatus.status=pending, wait for inclusion and re-run receipt check.",
            "If noncePending equals nonceLatest and no receipt/status, inspect node mempool/indexing logs.",
        ]
        print(
            json.dumps(
                {
                    "error": "replay",
                    "message": "deploy transaction appears to already be known by the node",
                    "sender": sender,
                    "nonceUsed": deploy_nonce,
                    "reason": replay_info.get("reason"),
                    "txHash": replay_info.get("txHash"),
                    "diagnostics": diagnostics,
                    "nextSteps": next_steps,
                },
                indent=2,
            )
        )
        raise SystemExit(2)

    if not contract_addr:
        sys.exit(
            "error: deploy completed but receipt did not include a contract address"
        )

    summary: Dict[str, Any] = {
        "signerSource": signer_source,
        "algName": signer.alg_name,
        "algId": signer.alg_id,
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
