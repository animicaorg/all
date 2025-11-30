# -*- coding: utf-8 -*-
"""
Deploy the Oracle example contract to a running Animica devnet and run a quick
sanity check (configure a price pair, submit a value, read it back).

This script is intentionally defensive about SDK import names and signatures so
it can run against early snapshots. It supports two flows:

A) Pure SDK (recommended)
   - Uses omni_sdk to build a deploy tx, sign it (Dilithium3 or SPHINCS+),
     send via RPC, then calls methods through the ABI.

B) Offline build + SDK deploy (fallback)
   - Compiles and bundles the contract using contracts/tools/build_package.py,
     then deploys the packaged artifact with omni_sdk.contracts.deployer.

Prereqs
-------
- A node + RPC at RPC_URL (default http://127.0.0.1:8545)
- Chain ID in CHAIN_ID (default 1337)
- A funded post-quantum account mnemonic in DEPLOYER_MNEMONIC (or MNEMONIC)
  on that network (see tests/devnet/seed_wallets.json for devnet)

Usage
-----
  python contracts/examples/oracle/deploy_and_test.py

Env
---
  RPC_URL            = http://127.0.0.1:8545
  CHAIN_ID           = 1337
  DEPLOYER_MNEMONIC  = "abandon ... art"
  GAS_PRICE          = optional numeric (tip)
  TIMEOUT_SEC        = RPC wait timeout (default 60)

What it does
------------
1) Builds or reads the contract package (manifest+code hash).
2) Creates a PQ signer from the mnemonic (Dilithium3 preferred).
3) Deploys the contract; prints tx hash and address.
4) Calls:
   - init(owner)
   - set_feeder(owner, allowed=True)
   - set_pair_decimals("ETH/USD", 8)
   - submit value from feeder
   - get_latest and print the tuple

If anything goes wrong, a concise hint is printed with next steps.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# --------------------------------------------------------------------------------------
# Config & helpers
# --------------------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # repo root (…/contracts)
MANIFEST = HERE / "manifest.json"
SOURCE = HERE / "contract.py"
BUILD_DIR = ROOT / "build"
BUILD_DIR.mkdir(exist_ok=True, parents=True)

DEFAULT_RPC = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
DEFAULT_CHAIN_ID = int(os.environ.get("CHAIN_ID", "1337"))
TIMEOUT_SEC = int(os.environ.get("TIMEOUT_SEC", "60"))

MNEMONIC = os.environ.get("DEPLOYER_MNEMONIC") or os.environ.get("MNEMONIC")


def _die(msg: str, code: int = 2) -> None:
    print(f"[deploy] {msg}", file=sys.stderr)
    sys.exit(code)


def _b32_from_label(label: str) -> bytes:
    b = label.encode("ascii")
    if len(b) > 32:
        raise ValueError("pair label must be <= 32 ASCII bytes")
    return b + b"\x00" * (32 - len(b))


def _addr_from_byte(b: int) -> bytes:
    return bytes([b]) * 20


# --------------------------------------------------------------------------------------
# Tolerant imports for omni_sdk (names may evolve slightly; we try several)
# --------------------------------------------------------------------------------------


@dataclass
class Sdk:
    RpcClient: Any
    send_and_wait: Any
    build_deploy: Optional[Any]
    build_call: Optional[Any]
    Deployer: Optional[Any]
    ContractClient: Optional[Any]
    mnemonic_to_signer: Any
    derive_address: Any
    canonical_cbor: Any
    hex_bytes: Any


def _import_sdk() -> Sdk:
    try:
        # base modules
        from omni_sdk.rpc.http import HttpClient as RpcClient  # newer naming
    except Exception:
        from omni_sdk.rpc.http import RpcClient  # type: ignore

    # send/await
    try:
        from omni_sdk.tx.send import send_and_wait
    except Exception:
        # compat shim
        from omni_sdk.tx.send import send as send_and_wait  # type: ignore

    # builders (optional; we can deploy via Deployer instead)
    try:
        from omni_sdk.tx.build import build_call, build_deploy
    except Exception:
        build_deploy = None
        build_call = None

    # higher-level deployer & client (optional)
    try:
        from omni_sdk.contracts.deployer import \
            deploy_package as Deployer  # function
    except Exception:
        Deployer = None

    try:
        from omni_sdk.contracts.client import ContractClient
    except Exception:
        ContractClient = None

    # signer from mnemonic (several options):
    def mnemonic_to_signer(mnemonic: str):
        # Try Dilithium3 first, fall back to SPHINCS+
        try:
            from omni_sdk.wallet.signer import Dilithium3Signer

            return Dilithium3Signer.from_mnemonic(mnemonic)  # type: ignore[attr-defined]
        except Exception:
            from omni_sdk.wallet.signer import SphincsSigner  # type: ignore

            return SphincsSigner.from_mnemonic(mnemonic)  # type: ignore

    # address derivation
    def derive_address(pubkey: bytes) -> str:
        try:
            from omni_sdk.address import address_from_public_key

            return address_from_public_key(pubkey)  # type: ignore
        except Exception:
            from omni_sdk.address import derive_address as _d

            return _d(pubkey)  # type: ignore

    # utils
    try:
        from omni_sdk.utils.cbor import dumps as canonical_cbor
    except Exception:
        canonical_cbor = None

    try:
        from omni_sdk.utils.bytes import to_hex as hex_bytes
    except Exception:

        def hex_bytes(b: bytes) -> str:  # type: ignore
            return "0x" + b.hex()

    return Sdk(
        RpcClient=RpcClient,
        send_and_wait=send_and_wait,
        build_deploy=build_deploy,
        build_call=build_call,
        Deployer=Deployer,
        ContractClient=ContractClient,
        mnemonic_to_signer=mnemonic_to_signer,
        derive_address=derive_address,
        canonical_cbor=canonical_cbor,
        hex_bytes=hex_bytes,
    )


# --------------------------------------------------------------------------------------
# Optional build step: contracts/tools/build_package.py
# --------------------------------------------------------------------------------------


def _optional_build_package() -> Optional[Path]:
    """
    If the local build tool exists, compile + bundle the contract into BUILD_DIR and
    return the path to the emitted package (JSON or CBOR). Otherwise, return None.
    """
    sys.path.append(str(ROOT))  # so 'tools' can import
    try:
        from tools.build_package import build_package  # type: ignore
    except Exception:
        return None

    try:
        pkg_path = build_package(
            manifest_path=str(MANIFEST),
            source_path=str(SOURCE),
            out_dir=str(BUILD_DIR),
            overwrite=True,
        )
        # build_package may return str
        return Path(pkg_path)
    except Exception as exc:
        print(
            f"[build] build_package failed (continuing with direct deploy path): {exc}"
        )
        return None


# --------------------------------------------------------------------------------------
# ABI loader and basic client call helper
# --------------------------------------------------------------------------------------


def _load_abi() -> Dict[str, Any]:
    with MANIFEST.open("r", encoding="utf-8") as f:
        mf = json.load(f)
    abi = mf.get("abi")
    if not abi:
        _die("manifest.json missing 'abi' key; re-generate manifest/package.")
    return abi


def _invoke_contract(
    sdk: Sdk, address: str, abi: Dict[str, Any], rpc: Any, method: str, **kwargs
) -> Any:
    """
    Try to call a contract method using whatever client the SDK exposes.
    """
    if sdk.ContractClient is None:
        _die("omni_sdk.contracts.client.ContractClient not found in this SDK build.")

    client = sdk.ContractClient(rpc, address=address, abi=abi)  # type: ignore
    fn = getattr(client, method, None)
    if callable(fn):
        return fn(**kwargs)  # type: ignore

    # Fallback generic 'call'
    if hasattr(client, "call"):
        return client.call(method, **kwargs)  # type: ignore

    _die("ContractClient does not expose method-call helpers in this SDK version.")
    return None


# --------------------------------------------------------------------------------------
# Deploy flows
# --------------------------------------------------------------------------------------


def _deploy_via_deployer(
    sdk: Sdk,
    rpc_url: str,
    chain_id: int,
    signer: Any,
    package_path: Path,
    gas_price: Optional[int],
) -> Tuple[str, str]:
    """
    Deploy a pre-built package via omni_sdk.contracts.deployer (function).
    Returns (address, tx_hash hex).
    """
    if sdk.Deployer is None:
        _die("omni_sdk.contracts.deployer.deploy_package not available in this SDK.")

    rpc = sdk.RpcClient(rpc_url)
    with package_path.open("rb") as f:
        package_bytes = f.read()

    # Some builds accept raw bytes, others a file path.
    try:
        result = sdk.Deployer(
            rpc=rpc,
            chain_id=chain_id,
            signer=signer,
            package=package_bytes,
            gas_price=gas_price,
        )  # type: ignore
    except TypeError:
        result = sdk.Deployer(
            rpc=rpc,
            chain_id=chain_id,
            signer=signer,
            package_path=str(package_path),
            gas_price=gas_price,
        )  # type: ignore

    # Expected shape: {"address": "anim1…", "tx_hash": "0x…"} or tuple
    if isinstance(result, dict):
        addr = result.get("address") or result.get("contract_address")
        txh = result.get("tx_hash") or result.get("transaction_hash")
    else:
        try:
            addr, txh = result  # type: ignore
        except Exception:
            _die(f"Unexpected deployer return value: {result!r}")
            raise

    if not addr or not txh:
        _die(f"Deployer returned incomplete result: {result!r}")

    print(f"[deploy] tx   : {txh}")
    print(f"[deploy] addr : {addr}")
    return addr, txh


def _deploy_direct_build(
    sdk: Sdk, rpc_url: str, chain_id: int, signer: Any, gas_price: Optional[int]
) -> Tuple[str, str]:
    """
    Build a deploy transaction directly via sdk.tx.build (if available).
    Returns (address, tx_hash hex).
    """
    if sdk.build_deploy is None:
        _die(
            "omni_sdk.tx.build.build_deploy not available; try the packager + deployer path."
        )

    rpc = sdk.RpcClient(rpc_url)
    with MANIFEST.open("rb") as f:
        manifest_bytes = f.read()
    with SOURCE.open("rb") as f:
        source_bytes = f.read()

    # Let the SDK handle compilation/packaging internally if supported
    tx = sdk.build_deploy(
        manifest=manifest_bytes,
        source=source_bytes,
        chain_id=chain_id,
        gas_price=gas_price,
        nonce=None,  # infer from network
    )

    sent = sdk.send_and_wait(rpc=rpc, tx=tx, signer=signer, timeout=TIMEOUT_SEC)  # type: ignore
    # Expecting {'tx_hash': '0x..', 'contract_address': 'anim1..'} or similar
    if isinstance(sent, dict):
        txh = sent.get("tx_hash") or sent.get("transaction_hash")
        address = sent.get("contract_address") or sent.get("address")
    else:
        _die(f"Unexpected send result: {sent!r}")
        raise AssertionError

    if not address or not txh:
        _die(f"Missing address/tx_hash in send result: {sent!r}")

    print(f"[deploy] tx   : {txh}")
    print(f"[deploy] addr : {address}")
    return address, txh


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def main() -> None:
    if not MANIFEST.is_file() or not SOURCE.is_file():
        _die(
            "Missing manifest.json or contract.py in examples/oracle/. Generate them first."
        )

    if not MNEMONIC:
        _die(
            "Set DEPLOYER_MNEMONIC (or MNEMONIC) in your environment with a funded devnet account."
        )

    gas_price: Optional[int] = None
    if os.environ.get("GAS_PRICE"):
        try:
            gas_price = int(os.environ["GAS_PRICE"])
        except ValueError:
            _die("GAS_PRICE must be an integer (in native units).")

    print(f"[cfg] rpc={DEFAULT_RPC} chain_id={DEFAULT_CHAIN_ID} timeout={TIMEOUT_SEC}s")

    sdk = _import_sdk()
    signer = sdk.mnemonic_to_signer(MNEMONIC)

    # Try to build a package first (if tool exists); otherwise go direct
    pkg_path = _optional_build_package()

    if pkg_path is not None and pkg_path.exists():
        print(f"[build] package ready at {pkg_path}")
        address, txh = _deploy_via_deployer(
            sdk, DEFAULT_RPC, DEFAULT_CHAIN_ID, signer, pkg_path, gas_price
        )
    else:
        print("[build] no local packager available; trying direct SDK build path")
        address, txh = _deploy_direct_build(
            sdk, DEFAULT_RPC, DEFAULT_CHAIN_ID, signer, gas_price
        )

    # ---- quick functional test -------------------------------------------------
    abi = _load_abi()
    rpc = sdk.RpcClient(DEFAULT_RPC)

    # Derive owner/feeder addresses (same signer address is fine for smoke)
    try:
        pub = signer.public_key  # bytes
    except Exception:
        # try common property
        pub = signer.pubkey  # type: ignore

    owner_addr = sdk.derive_address(pub)
    feeder_addr = owner_addr  # self-feeding for the demo

    def call(method: str, **kwargs) -> Any:
        return _invoke_contract(sdk, address, abi, rpc, method, **kwargs)

    # 1) init(owner)
    print("[call] init(owner)")
    call(
        "init",
        owner=(
            bytes.fromhex(owner_addr[4:]) if owner_addr.startswith("0x") else owner_addr
        ),
    )

    # 2) set_feeder(owner, allowed=True)
    print("[call] set_feeder(owner, True)")
    call(
        "set_feeder",
        addr=(
            bytes.fromhex(owner_addr[4:]) if owner_addr.startswith("0x") else owner_addr
        ),
        allowed=True,
    )

    # 3) set_pair_decimals("ETH/USD", 8)
    pair = _b32_from_label("ETH/USD")
    print("[call] set_pair_decimals('ETH/USD', 8)")
    call("set_pair_decimals", pair=pair, decimals=8)

    # 4) submit a value as feeder
    ts = int(time.time())
    value = 3250_12345678  # 8 decimals
    source = b"COINBASE".ljust(32, b"\x00")
    commitment = b"\xaa" * 32
    print("[call] submit ETH/USD value")
    round_id = call(
        "submit", pair=pair, value=value, ts=ts, source=source, commitment=commitment
    )
    print(f"[ok] round_id={round_id}")

    # 5) read latest
    latest = call("get_latest", pair=pair)
    print("[result] latest =", latest)

    print("\nSUCCESS: oracle deployed and responded to calls.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _die("interrupted", 130)
