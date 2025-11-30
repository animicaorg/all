#!/usr/bin/env python3
"""
Deploy the canonical Counter contract using the Animica Python SDK.

Usage
-----
$ python -m {{ package_name }}.examples.deploy_counter \
    --manifest ./artifacts/counter/manifest.json \
    --code ./artifacts/counter/code.ir

Environment (defaults)
----------------------
ANIMICA_RPC_URL     (default: http://127.0.0.1:8545)
ANIMICA_CHAIN_ID    (default: 1337)
DEPLOYER_MNEMONIC   (BIP-39-like mnemonic; required unless --mnemonic provided)

Notes
-----
- This example assumes you already compiled the Counter contract and have:
    - a manifest JSON file (ABI, metadata, code hash),
    - a compiled code blob (IR) file.

  If you’re starting fresh, generate these with the contracts tooling in this repo,
  or any compatible builder that emits a manifest+code pair matching the spec.

- Signing defaults to Dilithium3 (PQ). Set --alg sphincs_shake_128s to use SPHINCS+.

- Safe to run multiple times; each run deploys a new instance (fresh address).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# --------- Small local helpers


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v is not None else default


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read()


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class DeployArtifacts:
    manifest: Dict[str, Any]
    code: bytes


def load_artifacts(
    *,
    manifest_path: Optional[Path],
    code_path: Optional[Path],
    package_path: Optional[Path],
) -> DeployArtifacts:
    """
    Load manifest+code either from (--manifest, --code) *or* from a single --package JSON.

    The package JSON form is expected to contain either:
      - {"manifest": {...}, "code_hex": "0x..."}  (preferred)
      - {"manifest": {...}, "code_base64": "..."} (fallback)

    Raises ValueError if not enough information is provided.
    """
    if package_path:
        pkg = _read_json(package_path)
        manifest = pkg.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("package JSON missing 'manifest' object")
        if "code_hex" in pkg:
            code_hex = pkg["code_hex"]
            code_hex = (
                code_hex[2:]
                if isinstance(code_hex, str) and code_hex.startswith("0x")
                else code_hex
            )
            code = bytes.fromhex(code_hex)
        elif "code_base64" in pkg:
            import base64

            code = base64.b64decode(pkg["code_base64"])
        else:
            raise ValueError("package JSON must include 'code_hex' or 'code_base64'")
        return DeployArtifacts(manifest=manifest, code=code)

    if not (manifest_path and code_path):
        raise ValueError("Provide --manifest and --code, or a single --package")

    return DeployArtifacts(
        manifest=_read_json(manifest_path), code=_read_bytes(code_path)
    )


# --------- Main deploy flow


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deploy Counter via Animica Python SDK")
    ap.add_argument(
        "--rpc",
        default=os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545"),
        help="HTTP RPC URL",
    )
    ap.add_argument(
        "--chain", type=int, default=_env_int("ANIMICA_CHAIN_ID", 1337), help="Chain ID"
    )
    ap.add_argument(
        "--mnemonic",
        default=os.getenv("DEPLOYER_MNEMONIC"),
        help="Deployer mnemonic (or set DEPLOYER_MNEMONIC)",
    )
    ap.add_argument(
        "--ws", default=None, help="Optional WebSocket URL (to stream receipt faster)"
    )
    ap.add_argument(
        "--alg",
        default="dilithium3",
        choices=["dilithium3", "sphincs_shake_128s"],
        help="Signature algorithm",
    )
    ap.add_argument(
        "--nonce", type=int, default=None, help="Explicit sender nonce (optional)"
    )
    ap.add_argument(
        "--gas-price", type=int, default=None, help="Optional gas price override"
    )
    ap.add_argument(
        "--gas-limit", type=int, default=None, help="Optional gas limit override"
    )
    # artifacts
    ap.add_argument(
        "--manifest",
        type=Path,
        default=Path("./artifacts/counter/manifest.json"),
        help="Path to manifest.json",
    )
    ap.add_argument(
        "--code",
        type=Path,
        default=Path("./artifacts/counter/code.ir"),
        help="Path to compiled code/IR blob",
    )
    ap.add_argument(
        "--package",
        type=Path,
        default=None,
        help="Single JSON package containing {manifest, code_hex|code_base64}",
    )
    args = ap.parse_args(argv)

    if not args.mnemonic:
        print(
            "error: missing mnemonic; set --mnemonic or DEPLOYER_MNEMONIC",
            file=sys.stderr,
        )
        return 2

    # SDK imports kept local so importing this file is cheap
    from omni_sdk.address import address_from_pubkey
    from omni_sdk.config import Config
    from omni_sdk.rpc.http import Client as HttpClient
    from omni_sdk.tx import build as tx_build
    from omni_sdk.tx import send as tx_send

    # signer helper
    try:
        from omni_sdk.wallet.signer import Signer

        signer = Signer.from_mnemonic(args.mnemonic, alg=args.alg)
    except Exception:  # pragma: no cover - fallback path if SDK signer surface changes
        # Conservative fallback using pq lib directly (educational)
        from pq.py.keygen import keypair_from_mnemonic  # type: ignore
        from pq.py.sign import sign as pq_sign  # type: ignore

        pub, priv = keypair_from_mnemonic(args.mnemonic, alg=args.alg)

        class SignerFallback:
            alg_id = args.alg
            pk = pub
            sk = priv

            def sign(self, msg: bytes) -> bytes:
                return pq_sign(self.sk, msg, alg=self.alg_id)

            def public_key(self) -> bytes:
                return self.pk

        signer = SignerFallback()

    cfg = Config(rpc_url=args.rpc, chain_id=args.chain, timeout=15.0)
    http = HttpClient(cfg)

    # Resolve sender address from PQ pubkey
    sender_pubkey = signer.public_key()
    sender_addr = address_from_pubkey(sender_pubkey, alg=signer.alg_id)

    # Load artifacts
    try:
        artifacts = load_artifacts(
            manifest_path=args.package and None or args.manifest,
            code_path=args.package and None or args.code,
            package_path=args.package,
        )
    except Exception as e:
        print(f"error loading artifacts: {e}", file=sys.stderr)
        return 2

    # Optionally query nonce & fee settings
    try:
        if args.nonce is None:
            sender_nonce = http.call("state.getNonce", [sender_addr])
        else:
            sender_nonce = args.nonce
        if args.gas_price is None:
            chain_params = http.call("chain.getParams", [])
            base_gas_price = int(chain_params.get("minGasPrice", 1))
        else:
            base_gas_price = args.gas_price
    except Exception as e:
        print(f"error fetching chain state/params: {e}", file=sys.stderr)
        return 2

    # Build deploy transaction (manifest+code)
    try:
        deploy_tx = tx_build.deploy(
            manifest=artifacts.manifest,
            code=artifacts.code,
            sender=sender_addr,
            nonce=sender_nonce,
            gas_price=base_gas_price,
            gas_limit=args.gas_limit,
            chain_id=args.chain,
        )
    except Exception as e:
        print(f"error building deploy tx: {e}", file=sys.stderr)
        return 2

    # Sign + send
    try:
        sign_bytes = deploy_tx["signBytes"]  # produced by builder; deterministic
        signature = signer.sign(sign_bytes)
        deploy_tx["signature"] = {
            "alg": signer.alg_id,
            "sig": signature,
            "pubkey": sender_pubkey,
        }
        raw = tx_build.encode_cbor(deploy_tx)  # canonical CBOR
        tx_hash = http.call("tx.sendRawTransaction", [raw.hex()])
    except Exception as e:
        print(f"error signing/sending tx: {e}", file=sys.stderr)
        return 2

    print(f"submitted deploy tx: {tx_hash}")
    print(f"sender: {sender_addr}")
    print("waiting for receipt...")

    # Await receipt
    try:
        receipt = tx_send.wait_for_receipt(
            http, tx_hash, timeout=60.0, poll_interval=1.0
        )
    except Exception as e:
        print(f"error awaiting receipt: {e}", file=sys.stderr)
        return 2

    status = receipt.get("status")
    gas_used = receipt.get("gasUsed")
    contract_addr = receipt.get("contractAddress") or receipt.get("to")
    print("----- DEPLOY RESULT -----")
    print(f"status:        {status}")
    print(f"gasUsed:       {gas_used}")
    print(f"contract addr: {contract_addr}")

    if status != "SUCCESS":
        print("deployment failed (see node logs / receipt)", file=sys.stderr)
        return 1

    # Optional: minimal sanity poke (if ABI has a 'get' method)
    try:
        # Many Counter ABIs expose a no-arg 'get' view function that returns the current value.
        result = http.call(
            "state.call",
            [
                {
                    "to": contract_addr,
                    "data": {
                        "method": "get",
                        "args": [],
                    },  # ABI-encoded by the node's minimal VM shim, if supported
                }
            ],
        )
        print(f"counter.get() → {result}")
    except Exception:
        # It's fine if your node doesn't expose a convenience call; skip quietly.
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
