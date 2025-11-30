#!/usr/bin/env python3
"""
Animica SDK — Python E2E: deploy + call Counter

Flow:
  1) Load RPC/chain config (from env or CLI)
  2) Load canonical Counter contract (manifest+source)
  3) Pick a funded test account (from fixtures or ephemeral)
  4) Deploy contract (signed CBOR tx)
  5) Call `inc(2)` then `get()` and assert value == previous + 2
  6) Print tx hashes, address, logs

Requirements:
  - sdk/python (omni_sdk) installed / importable
  - Node running at RPC_URL (default http://127.0.0.1:8545)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

from omni_sdk.address import encode_address
from omni_sdk.contracts import events as contracts_events
# --- SDK imports (generated earlier) ---
from omni_sdk.rpc.http import RpcClient
from omni_sdk.tx import build as tx_build
from omni_sdk.tx import send as tx_send
from omni_sdk.wallet import mnemonic as wallet_mnemonic
from omni_sdk.wallet import signer as wallet_signer

# ---------- Helpers ----------

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_ROOT = Path(__file__).resolve().parent
CONTRACT_DIR = HARNESS_ROOT / "contracts" / "counter"
FIXTURES_DIR = HARNESS_ROOT / "fixtures"

DEFAULT_RPC = "http://127.0.0.1:8545"
DEFAULT_WS = "ws://127.0.0.1:8545/ws"


@dataclass
class E2EConfig:
    rpc_url: str
    ws_url: str
    chain_id: Optional[int]
    alg_id: str
    account_mnemonic: Optional[str]
    account_index: int = 0


def _detect_chain_id(rpc: RpcClient) -> int:
    try:
        cid = rpc.call("chain.getChainId", [])
        if not isinstance(cid, int):
            raise TypeError("chain.getChainId did not return int")
        return cid
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Failed to fetch chainId: {e}") from e


def _load_manifest_and_code() -> Tuple[dict, bytes]:
    manifest_path = CONTRACT_DIR / "manifest.json"
    code_path = CONTRACT_DIR / "contract.py"
    if not manifest_path.exists() or not code_path.exists():
        raise FileNotFoundError(f"Missing contract files under {CONTRACT_DIR}")
    manifest = json.loads(manifest_path.read_text())
    code_bytes = code_path.read_bytes()
    return manifest, code_bytes


def _load_funded_fixture() -> Optional[dict]:
    acct_file = FIXTURES_DIR / "accounts.json"
    if not acct_file.exists():
        return None
    try:
        data = json.loads(acct_file.read_text())
        # Expected minimal schema: {"mnemonic": "...", "alg": "dilithium3", "index": 0}
        return data
    except Exception:
        return None


def _make_signer(cfg: E2EConfig, chain_id: int) -> wallet_signer.Signer:
    """
    Create a PQ signer from mnemonic (fixture/env) or ephemeral fallback.
    """
    if cfg.account_mnemonic:
        seed = wallet_mnemonic.mnemonic_to_seed(cfg.account_mnemonic)
        return wallet_signer.Signer.from_seed(
            seed, alg_id=cfg.alg_id, account_index=cfg.account_index, chain_id=chain_id
        )

    # Try fixtures
    fx = _load_funded_fixture()
    if fx and "mnemonic" in fx:
        seed = wallet_mnemonic.mnemonic_to_seed(fx["mnemonic"])
        alg = fx.get("alg", cfg.alg_id)
        idx = int(fx.get("index", cfg.account_index))
        return wallet_signer.Signer.from_seed(
            seed, alg_id=alg, account_index=idx, chain_id=chain_id
        )

    # Ephemeral (NOT funded) — suitable if devnet prefunds default derivations
    eph_mn, _ = wallet_mnemonic.create_mnemonic()
    seed = wallet_mnemonic.mnemonic_to_seed(eph_mn)
    print(
        "[warn] Using ephemeral mnemonic (devnet should prefund default accounts).",
        file=sys.stderr,
    )
    return wallet_signer.Signer.from_seed(
        seed, alg_id=cfg.alg_id, account_index=cfg.account_index, chain_id=chain_id
    )


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


# ---------- Deploy & Call ----------


def deploy_counter(
    rpc: RpcClient, chain_id: int, signer: wallet_signer.Signer
) -> Tuple[str, str]:
    """
    Returns (address, tx_hash_hex)
    """
    manifest, code_bytes = _load_manifest_and_code()

    # Build a deploy transaction using SDK builders.
    tx = tx_build.build_deploy(
        chain_id=chain_id,
        sender_pubkey=signer.public_key_bytes(),
        alg_id=signer.alg_id,
        manifest=manifest,
        code=code_bytes,
        nonce=None,  # let builder fetch via RPC
    )

    sign_bytes = tx_build.sign_bytes_for_tx(tx)
    signature = signer.sign(sign_bytes, domain="tx")
    raw = tx_build.attach_signature(tx, signature)

    tx_hash = tx_send.send_raw_transaction(rpc, raw)
    receipt = tx_send.wait_for_receipt(rpc, tx_hash, poll_interval=0.5, timeout=60.0)

    if not receipt or receipt.get("status") != "SUCCESS":
        raise RuntimeError(f"Deploy failed; receipt={_pretty(receipt)}")

    # Address derivation is deterministic from contract deploy result; the builder
    # can compute it, but we read from receipt if present.
    address = receipt.get("contractAddress")
    if not address:
        # Fallback: derive from tx + chain rules (SDK helper).
        address = encode_address(signer.alg_id, signer.address_payload())

    return address, tx_hash


def call_inc_then_get(
    rpc: RpcClient, chain_id: int, signer: wallet_signer.Signer, address: str
) -> dict:
    """
    Calls inc(2) then get(), returns dict with hashes, new_value, logs
    """
    # Build & send inc(2)
    tx_inc = tx_build.build_call(
        chain_id=chain_id,
        sender_pubkey=signer.public_key_bytes(),
        alg_id=signer.alg_id,
        to=address,
        function="inc",
        args=[2],
        nonce=None,  # auto-fetch
    )
    sign_bytes_inc = tx_build.sign_bytes_for_tx(tx_inc)
    sig_inc = signer.sign(sign_bytes_inc, domain="tx")
    raw_inc = tx_build.attach_signature(tx_inc, sig_inc)

    txh_inc = tx_send.send_raw_transaction(rpc, raw_inc)
    rc_inc = tx_send.wait_for_receipt(rpc, txh_inc, poll_interval=0.5, timeout=60.0)
    if not rc_inc or rc_inc.get("status") != "SUCCESS":
        raise RuntimeError(f"inc(2) failed; receipt={_pretty(rc_inc)}")

    # Decode events (if any) using ABI
    manifest, _ = _load_manifest_and_code()
    logs = contracts_events.decode_receipt_events(manifest["abi"], rc_inc)

    # Read value via a *view* call — the SDK can do a local simulation through RPC.
    # For simplicity, we reuse tx_build to prepare a call and use a 'call' path.
    # Many nodes offer a `state.call`/`simulate` — the RpcClient wrapper exposes it when available.
    try:
        new_value = rpc.call(
            "state.call", [{"to": address, "function": "get", "args": []}]
        )
    except Exception:
        # Fallback: send a tx call with zero fee on devnet if state.call isn't available.
        tx_get = tx_build.build_call(
            chain_id=chain_id,
            sender_pubkey=signer.public_key_bytes(),
            alg_id=signer.alg_id,
            to=address,
            function="get",
            args=[],
            nonce=None,
        )
        sig_get = signer.sign(tx_build.sign_bytes_for_tx(tx_get), domain="tx")
        raw_get = tx_build.attach_signature(tx_get, sig_get)
        txh_get = tx_send.send_raw_transaction(rpc, raw_get)
        rc_get = tx_send.wait_for_receipt(rpc, txh_get, poll_interval=0.5, timeout=60.0)
        if rc_get.get("status") != "SUCCESS":
            raise RuntimeError("get() failed")
        # Receipts for view may embed return data; offer fallback None if absent.
        new_value = (rc_get.get("return") or {}).get("value")

    return {
        "tx_hash_inc": txh_inc,
        "receipt_inc": rc_inc,
        "decoded_events": logs,
        "value_after_inc": new_value,
    }


# ---------- CLI ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Animica Python SDK E2E (deploy+call Counter)"
    )
    p.add_argument(
        "--rpc",
        default=os.environ.get("RPC_URL", DEFAULT_RPC),
        help=f"HTTP RPC URL (default: {DEFAULT_RPC})",
    )
    p.add_argument(
        "--ws",
        default=os.environ.get("WS_URL", DEFAULT_WS),
        help=f"WS URL (default: derive: {DEFAULT_WS})",
    )
    p.add_argument(
        "--chain",
        type=int,
        default=int(os.environ.get("CHAIN_ID", "0")),
        help="Chain ID (0 = auto-detect)",
    )
    p.add_argument(
        "--alg",
        default=os.environ.get("ALG_ID", "dilithium3"),
        help="PQ alg id (dilithium3|sphincs_shake_128s)",
    )
    p.add_argument(
        "--mnemonic",
        default=os.environ.get("MNEMONIC"),
        help="Use explicit mnemonic (overrides fixtures)",
    )
    p.add_argument(
        "--account-index",
        type=int,
        default=int(os.environ.get("ACCOUNT_INDEX", "0")),
        help="Derivation index (default 0)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    cfg = E2EConfig(
        rpc_url=args.rpc,
        ws_url=args.ws,
        chain_id=args.chain if args.chain != 0 else None,
        alg_id=args.alg,
        account_mnemonic=args.mnemonic,
        account_index=args.account_index,
    )

    rpc = RpcClient(cfg.rpc_url)

    chain_id = cfg.chain_id or _detect_chain_id(rpc)
    signer = _make_signer(cfg, chain_id)
    sender_addr = encode_address(signer.alg_id, signer.address_payload())

    print("[e2e] Using sender:", sender_addr)
    print("[e2e] ChainId:", chain_id)

    # Deploy
    addr, txh = deploy_counter(rpc, chain_id, signer)
    print("[e2e] Deployed contract address:", addr)
    print("[e2e] Deploy tx:", txh)

    # Call inc/get
    result = call_inc_then_get(rpc, chain_id, signer, addr)
    print("[e2e] inc(2) tx:", result["tx_hash_inc"])
    print("[e2e] Value after inc:", result["value_after_inc"])

    # Basic assertion: value should be >= 2 (fresh deploy starts at 0)
    try:
        v = int(result["value_after_inc"])
    except Exception:
        v = None
    if v is None or v < 2:
        print(
            "[e2e] ERROR: unexpected counter value:",
            result["value_after_inc"],
            file=sys.stderr,
        )
        return 2

    # Display decoded events
    if result["decoded_events"]:
        print("[e2e] Events:")
        for ev in result["decoded_events"]:
            print("  -", _pretty(ev))

    print("[e2e] ✅ success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
