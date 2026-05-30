#!/usr/bin/env python3
"""
Deploy the Animica NFT Marketplace stack to mainnet (or any configured
network):

  1. AnimicaNFT721Standard               — the founders pass collection
  2. AnimicaNFTMarketplace               — the fixed-price marketplace
  3. AnimicaFoundersPass                 — the first drop launchpad
  4. Set the launchpad as the collection's minter so public mint works.

Outputs a JSON manifest with the deployed addresses + the env vars to
write into /etc/animica/animica-xyz.env so the website + indexer pick
them up:

    NFT_MARKETPLACE_ADDR=...
    FOUNDERS_PASS_ADDR=...
    FOUNDERS_PASS_COLLECTION_ADDR=...

Run with:

    python contracts/scripts/deploy_nft_marketplace.py \\
        --network mainnet \\
        --owner-wallet anim1... \\
        --treasury    anim1... \\
        --price-anm 25000 \\
        --supply 1000 \\
        --royalty-bps 500

The script uses the `animica` CLI for tx signing + broadcast — same path
buy.animica.org and the chat-server bridge use, so credentials live in
the same `animica wallet` store.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
ANIMICA_CLI = os.environ.get("ANIMICA_CLI", str(REPO_ROOT / "animica"))


def run(args: list[str]) -> str:
    print(f"$ {' '.join(shlex.quote(a) for a in args)}")
    proc = subprocess.run(
        args,
        check=True,
        text=True,
        capture_output=True,
        env={**os.environ, "NO_COLOR": "1"},
    )
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.stdout.strip()


def deploy_contract(
    *,
    network: str,
    owner_wallet: str,
    contract_dir: Path,
    init_args: list[Any],
) -> str:
    """Deploy a contract from a standard contract dir (contract.py +
    manifest.json) and return the new contract's address."""
    manifest = json.loads((contract_dir / "manifest.json").read_text())
    out = run([
        ANIMICA_CLI, "contract", "deploy",
        "--network", network,
        "--wallet", owner_wallet,
        "--source", str(contract_dir / "contract.py"),
        "--manifest", str(contract_dir / "manifest.json"),
        "--init-args", json.dumps(init_args),
        "--json",
    ])
    j = json.loads(out)
    addr = j.get("contract_address") or j.get("address")
    if not addr:
        raise SystemExit(f"deploy of {manifest['name']} returned no address: {j}")
    print(f"  ✓ {manifest['name']} deployed at {addr}")
    return addr.lower()


def contract_call(
    *,
    network: str,
    wallet: str,
    contract: str,
    method: str,
    args: list[Any],
    value_nanos: int = 0,
) -> str:
    """Call a contract method that mutates state. Returns tx hash."""
    out = run([
        ANIMICA_CLI, "contract", "call",
        "--network", network,
        "--wallet", wallet,
        "--to", contract,
        "--method", method,
        "--args", json.dumps(args),
        "--value-nanos", str(value_nanos),
        "--json",
    ])
    j = json.loads(out)
    return j.get("tx_hash", "")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy the Animica NFT marketplace contracts."
    )
    parser.add_argument("--network", required=True, help="mainnet / devnet / local")
    parser.add_argument(
        "--owner-wallet",
        required=True,
        help="Label of the `animica wallet` to sign + own the contracts",
    )
    parser.add_argument(
        "--treasury",
        required=True,
        help="bech32 address that receives marketplace fees + FP proceeds",
    )
    parser.add_argument(
        "--marketplace-fee-bps",
        type=int,
        default=250,
        help="marketplace fee in basis points (default 250 = 2.5%%; max 1000)",
    )
    parser.add_argument(
        "--price-anm",
        type=int,
        default=25000,
        help="Founders Pass price in whole ANM (default 25000)",
    )
    parser.add_argument(
        "--supply",
        type=int,
        default=1000,
        help="Founders Pass total supply (default 1000)",
    )
    parser.add_argument(
        "--royalty-bps",
        type=int,
        default=500,
        help="Royalty in basis points on the FP collection (default 500 = 5%%; max 1000)",
    )
    parser.add_argument(
        "--base-uri",
        default="https://meta.animica.xyz/founders/",
        help="Token URI prefix",
    )
    parser.add_argument(
        "--output",
        default="contracts/deployments/nft_marketplace.deploy.json",
        help="Where to write the deployment manifest",
    )
    args = parser.parse_args()

    nanos_per_anm = 1_000_000_000
    price_nanos = args.price_anm * nanos_per_anm

    owner_addr = run([
        ANIMICA_CLI, "wallet", "address",
        "--label", args.owner_wallet,
        "--network", args.network,
    ]).strip()
    if not owner_addr.startswith("anim1"):
        raise SystemExit(f"unexpected wallet address: {owner_addr}")
    print(f"deploying as owner={owner_addr}")

    # 1. Founders Pass collection (ANM-721)
    collection_addr = deploy_contract(
        network=args.network,
        owner_wallet=args.owner_wallet,
        contract_dir=REPO_ROOT / "contracts/standards/animica_nft721",
        init_args=[
            "Animica Founders Pass",       # name
            "AFP",                         # symbol
            owner_addr,                    # owner
            args.base_uri,                 # base_uri
            args.supply,                   # max_supply
            owner_addr,                    # royalty receiver
            args.royalty_bps,              # royalty bps
        ],
    )
    time.sleep(1)

    # 2. Marketplace
    marketplace_addr = deploy_contract(
        network=args.network,
        owner_wallet=args.owner_wallet,
        contract_dir=REPO_ROOT / "contracts/standards/animica_nft_marketplace",
        init_args=[owner_addr, args.treasury, args.marketplace_fee_bps],
    )
    time.sleep(1)

    # 3. Founders Pass launchpad
    fp_addr = deploy_contract(
        network=args.network,
        owner_wallet=args.owner_wallet,
        contract_dir=REPO_ROOT / "contracts/launchpads/animica_founders_pass",
        init_args=[
            owner_addr,
            collection_addr,
            args.treasury,
            price_nanos,
            args.supply,
        ],
    )
    time.sleep(1)

    # 4. Authorise the launchpad as the collection's minter.
    set_minter_tx = contract_call(
        network=args.network,
        wallet=args.owner_wallet,
        contract=collection_addr,
        method="set_minter",
        args=[fp_addr],
    )
    print(f"  ✓ set_minter tx: {set_minter_tx}")

    manifest = {
        "network": args.network,
        "owner": owner_addr,
        "treasury": args.treasury,
        "marketplace": {
            "address": marketplace_addr,
            "fee_bps": args.marketplace_fee_bps,
        },
        "founders_pass": {
            "launchpad_address": fp_addr,
            "collection_address": collection_addr,
            "supply": args.supply,
            "price_nanos": price_nanos,
            "price_anm": args.price_anm,
            "royalty_bps": args.royalty_bps,
        },
        "env": {
            "NFT_MARKETPLACE_ADDR": marketplace_addr,
            "FOUNDERS_PASS_ADDR": fp_addr,
            "FOUNDERS_PASS_COLLECTION_ADDR": collection_addr,
        },
    }
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nDeployment manifest: {out_path}")
    print("\nEnv to add to /etc/animica/animica-xyz.env:")
    for k, v in manifest["env"].items():
        print(f"  {k}={v}")


if __name__ == "__main__":
    main()
