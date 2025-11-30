# -*- coding: utf-8 -*-
"""
Deploy & quick-test script for the Multisig example (N-of-M, PQ-aware permits).

What this does (happy path, on a devnet):
  1) Compiles the Multisig contract using the local Python VM toolchain.
  2) Builds a deploy package (manifest + code hash + code bytes).
  3) Uses the Python SDK to sign & send a deploy transaction.
  4) Calls a couple of read methods (get_config, get_nonce).
  5) Demonstrates how to build PERMIT SignBytes for two owners and
     executes a no-op action via execute_with_permits() to validate threshold logic.

Requirements:
  - Python SDK:      `pip install omni-sdk` (or from this repo: sdk/python)
  - Python VM (opt): `pip install animica-vm-py` (or use contracts.tools.build_package)
  - A running node RPC: RPC_URL (http), WS optional
  - A funded PQ account (Dilithium3 or SPHINCS+) on the target network

Environment Variables (defaults assume local devnet):
  RPC_URL           (default: http://127.0.0.1:8545)
  CHAIN_ID          (default: 1337)
  DEPLOYER_MNEMONIC (BIP-39-like mnemonic for the deployer; REQUIRED)
  PQ_ALG            (default: dilithium3)  # one of: dilithium3, sphincs_shake_128s
  GAS_LIMIT_DEPLOY  (default: 2_500_000)
  GAS_LIMIT_CALL    (default: 250_000)
  EXPIRY_DELTA      (default: 100)  # blocks in the future for permit expiry

Usage:
  python -m contracts.examples.multisig.deploy_and_test

Notes:
  - This script is defensive about imports and will explain missing pieces.
  - It prefers using the repo-local builder (contracts.tools.build_package) to create a
    canonical deploy bundle; if not available, it falls back to vm_py loader if present.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from hashlib import sha3_256
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
CONTRACT_SRC = HERE / "contract.py"
MANIFEST_JSON = HERE / "manifest.json"
REPO_ROOT = HERE.parents[3]  # add repo root to sys.path for `contracts.tools` imports


# ------------------------------------------------------------------------------------
# Helpers: robust imports (SDK, builder, VM)
# ------------------------------------------------------------------------------------


def _import_sdk():
    try:
        from omni_sdk.rpc.http import HttpClient as RpcClient
    except Exception:
        try:
            # alt export name variant
            from omni_sdk.rpc.http import Client as RpcClient
        except Exception as e:  # pragma: no cover
            raise SystemExit(
                "Could not import omni_sdk RPC client. Install the Python SDK:\n"
                "    pip install omni-sdk\n"
                "or ensure sdk/python is on PYTHONPATH."
            ) from e

    try:
        from omni_sdk.wallet.mnemonic import Mnemonic
        from omni_sdk.wallet.signer import Signer
    except Exception as e:  # pragma: no cover
        raise SystemExit(
            "Could not import omni_sdk wallet modules. Ensure omni-sdk is installed."
        ) from e

    try:
        from omni_sdk.contracts.client import ContractClient
        from omni_sdk.contracts.deployer import Deployer
    except Exception as e:  # pragma: no cover
        raise SystemExit(
            "Could not import omni_sdk contracts helpers (Deployer/ContractClient). "
            "Ensure omni-sdk is up to date."
        ) from e

    try:
        from omni_sdk.tx import build as tx_build
        from omni_sdk.tx import send as tx_send
    except Exception:
        tx_build = None
        tx_send = None  # not strictly needed if Deployer covers deploy

    return RpcClient, Mnemonic, Signer, Deployer, ContractClient, tx_build, tx_send


def _import_builder_or_vm():
    """Prefer repo-local builder; otherwise try vm_py loader as a fallback."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from contracts.tools.build_package import build_package  # type: ignore

        return ("builder", build_package)
    except Exception:
        pass

    try:
        # Fallback: compile & package via vm_py loader
        from vm_py.compiler.encode import encode as ir_encode  # type: ignore
        from vm_py.runtime.loader import load as vm_load  # type: ignore

        def _vm_builder(src_path: Path, manifest_path: Path) -> Dict[str, Any]:
            # Minimal, example-grade bundle builder using vm_py directly
            loader_out = vm_load(
                manifest_path=str(manifest_path),
                source_path=str(src_path),
            )
            # Expect loader_out to give us IR/module bytes and a normalized manifest dict
            code_bytes = loader_out.get("code_bytes")
            manifest = loader_out.get("manifest")
            if not isinstance(code_bytes, (bytes, bytearray)) or not isinstance(
                manifest, dict
            ):
                raise RuntimeError("vm_py loader returned unexpected structure")
            # Tie a code hash (sha3_256) for content-addressed deploys
            code_hash = sha3_256(code_bytes).hexdigest()
            return {
                "code": bytes(code_bytes),
                "code_hash": "0x" + code_hash,
                "manifest": manifest,
            }

        return ("vm", _vm_builder)
    except Exception:
        pass

    raise SystemExit(
        "Neither repo-local builder (contracts.tools.build_package) nor vm_py loader available.\n"
        "Install either:\n"
        "  pip install -e .   # from repo root so 'contracts.tools' is importable\n"
        "or\n"
        "  pip install animica-vm-py\n"
    )


# ------------------------------------------------------------------------------------
# Permit SignBytes helpers (matches tests_local.py logic)
# ------------------------------------------------------------------------------------

PERMIT_DOMAIN = sha3_256(b"ANIMICA::MULTISIG::PERMIT::V1").digest()  # 32 bytes


def _uvarint(n: int) -> bytes:
    if n < 0:
        raise ValueError("uvarint cannot encode negative")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(0x80 | b)
        else:
            out.append(b)
            break
    return bytes(out)


def _enc_u64(n: int) -> bytes:
    return n.to_bytes(8, "little", signed=False)


def _enc_u128(n: int) -> bytes:
    return n.to_bytes(16, "little", signed=False)


def _enc_bytes(b: bytes) -> bytes:
    return _uvarint(len(b)) + b


def _enc_address(addr20: bytes) -> bytes:
    if not isinstance(addr20, (bytes, bytearray)) or len(addr20) != 20:
        raise ValueError("address must be 20 bytes")
    return bytes(addr20)


def build_permit_signbytes(
    *,
    chain_id: int,
    contract_addr: bytes,
    to: bytes,
    value: int,
    data: bytes,
    gas_limit: int,
    nonce: int,
    expiry_height: int,
) -> bytes:
    sb = bytearray()
    sb += PERMIT_DOMAIN  # 32 bytes
    sb += _enc_u64(chain_id)
    sb += _enc_address(contract_addr)
    sb += _enc_address(to)
    sb += _enc_u128(value)
    sb += _enc_u64(gas_limit)
    sb += _enc_u64(expiry_height)
    sb += _enc_u128(nonce)
    sb += _enc_bytes(data)
    return bytes(sb)


# ------------------------------------------------------------------------------------
# Configuration / CLI
# ------------------------------------------------------------------------------------


@dataclass
class Config:
    rpc_url: str
    chain_id: int
    mnemonic: str
    pq_alg: str
    gas_deploy: int
    gas_call: int
    expiry_delta: int


def load_config() -> Config:
    rpc_url = os.getenv("RPC_URL", "http://127.0.0.1:8545")
    chain_id = int(os.getenv("CHAIN_ID", "1337"))
    mnemonic = os.getenv("DEPLOYER_MNEMONIC")
    if not mnemonic:
        raise SystemExit(
            "DEPLOYER_MNEMONIC is required. Example:\n"
            "  export DEPLOYER_MNEMONIC='satoshi ... (test words) ...'\n"
            "  python -m contracts.examples.multisig.deploy_and_test"
        )
    pq_alg = os.getenv("PQ_ALG", "dilithium3")  # or sphincs_shake_128s
    gas_deploy = int(os.getenv("GAS_LIMIT_DEPLOY", "2500000"))
    gas_call = int(os.getenv("GAS_LIMIT_CALL", "250000"))
    expiry_delta = int(os.getenv("EXPIRY_DELTA", "100"))
    return Config(
        rpc_url=rpc_url,
        chain_id=chain_id,
        mnemonic=mnemonic,
        pq_alg=pq_alg.lower(),
        gas_deploy=gas_deploy,
        gas_call=gas_call,
        expiry_delta=expiry_delta,
    )


# ------------------------------------------------------------------------------------
# Main flow
# ------------------------------------------------------------------------------------


def main() -> None:
    # 0) Load config & libs
    cfg = load_config()
    RpcClient, Mnemonic, Signer, Deployer, ContractClient, tx_build, tx_send = (
        _import_sdk()
    )
    builder_kind, builder_fn = _import_builder_or_vm()

    print(f"[i] RPC_URL={cfg.rpc_url}  CHAIN_ID={cfg.chain_id}")
    print(f"[i] Using builder: {builder_kind}")

    # 1) Build deploy package (manifest+code+hash)
    if not CONTRACT_SRC.is_file() or not MANIFEST_JSON.is_file():
        raise SystemExit("Missing contract.py or manifest.json next to this script.")

    pkg = builder_fn(CONTRACT_SRC, MANIFEST_JSON)
    # Normalize structure
    if isinstance(pkg, tuple) and len(pkg) == 2:
        # Some builders might return (package_dict, out_path)
        pkg = pkg[0]
    assert (
        isinstance(pkg, dict) and "manifest" in pkg and "code" in pkg
    ), "invalid package"
    code_hash = pkg.get("code_hash") or ("0x" + sha3_256(pkg["code"]).hexdigest())

    print(f"[i] Package ready. code_hash={code_hash}")

    # 2) Initialize SDK client & signer
    rpc = RpcClient(cfg.rpc_url, chain_id=cfg.chain_id)  # type: ignore[arg-type]
    wallet = Mnemonic.from_phrase(
        cfg.mnemonic
    )  # must expose deterministic seed derivation
    if cfg.pq_alg not in ("dilithium3", "sphincs_shake_128s"):
        raise SystemExit("PQ_ALG must be one of: dilithium3, sphincs_shake_128s")
    signer = Signer.from_mnemonic(wallet, alg=cfg.pq_alg)  # type: ignore[attr-defined]

    deployer_addr = (
        signer.address()
    )  # 20-byte (raw) or bech32m; ContractClient accepts either
    print(
        f"[i] Deployer address: {getattr(deployer_addr, 'hex', lambda: str(deployer_addr))()}"
    )

    # 3) Deploy
    print("[i] Sending deploy tx...")
    d = Deployer(rpc)  # type: ignore[call-arg]
    # Deployer API should accept (signer, package, gas). Some variants may allow tip/price too.
    deploy_receipt = d.deploy(signer=signer, package=pkg, gas_limit=cfg.gas_deploy)  # type: ignore[attr-defined]
    if (deploy_receipt or {}).get("status") != "SUCCESS":
        raise SystemExit(f"Deploy failed: {json.dumps(deploy_receipt, indent=2)}")

    contract_addr = deploy_receipt.get("contractAddress") or deploy_receipt.get(
        "address"
    )
    if not contract_addr:
        raise SystemExit("Deploy receipt did not include contract address.")
    print(f"[✓] Deployed at: {contract_addr}")

    # 4) Basic reads via ContractClient
    c = ContractClient(rpc, abi=pkg["manifest"]["abi"], address=contract_addr)  # type: ignore[call-arg]
    cfg_out = c.call(
        "get_config", {}
    )  # expects owners (List[bytes20]), threshold (u16)
    owners = cfg_out.get("owners", [])
    threshold = cfg_out.get("threshold", 0)
    print(f"[i] get_config → owners={len(owners)} threshold={threshold}")

    nonce_out = c.call("get_nonce", {})
    nonce = int(nonce_out.get("nonce", 0))
    print(f"[i] get_nonce → {nonce}")

    # 5) Build a no-op action & PERMIT SignBytes for two owners (2-of-M)
    # Target: call our own contract with empty payload. Value=0 (no transfer).
    head = rpc.call("chain.getHead", {})  # to fetch height for expiry planning
    height = int(head.get("number", 0))
    expiry_height = height + max(2, cfg.expiry_delta)

    # Extract the canonical 20-byte address payloads for signbytes.
    def _addr20(a: Any) -> bytes:
        # SDK addresses may be raw 20-bytes, hex, or bech32m. Normalize.
        if isinstance(a, (bytes, bytearray)) and len(a) == 20:
            return bytes(a)
        if isinstance(a, str):
            s = a.lower()
            if s.startswith("0x") and len(s) == 42:
                return bytes.fromhex(s[2:])
            # if bech32m, rely on sdk utils
            try:
                from omni_sdk.utils.bech32 import decode  # type: ignore

                hrp, data = decode(a)
                if len(data) == 20:
                    return data
            except Exception:
                pass
        raise ValueError("Unsupported address format for 20-byte normalization")

    contract_addr20 = _addr20(contract_addr)
    to_addr20 = contract_addr20  # no-op call to self

    action_data = b""  # empty payload (no-op)
    sb = build_permit_signbytes(
        chain_id=cfg.chain_id,
        contract_addr=contract_addr20,
        to=to_addr20,
        value=0,
        data=action_data,
        gas_limit=cfg.gas_call,
        nonce=nonce,
        expiry_height=expiry_height,
    )
    action_hash = "0x" + sha3_256(sb).hexdigest()
    print(f"[i] Action SignBytes digest: {action_hash}")

    # For demo purposes we assemble "permits" that the contract's example logic will treat
    # as approvals keyed by signer address. In hardened setups, off-chain signatures
    # over `sb` would accompany each approval (and be verified host-side or via a precompile).
    if len(owners) < max(2, threshold):
        raise SystemExit(
            "The deployed config has too few owners to demonstrate threshold."
        )
    permit_list: List[Dict[str, Any]] = [
        {"signer_addr": owners[0], "sig": b"DEMO_SIG_0", "alg_id": 0x0001},
        {"signer_addr": owners[1], "sig": b"DEMO_SIG_1", "alg_id": 0x0001},
    ]

    # 6) Execute the no-op action using execute_with_permits
    print("[i] Calling execute_with_permits() with two approvals...")
    tx_res = c.send(  # write-call
        "execute_with_permits",
        {
            "to": to_addr20,
            "value": 0,
            "data": action_data,
            "gas_limit": cfg.gas_call,
            "nonce": nonce,
            "expiry_height": expiry_height,
            "permits": permit_list,
        },
        signer=signer,
        gas_limit=cfg.gas_call,
    )
    if (tx_res or {}).get("status") != "SUCCESS":
        raise SystemExit(f"execute_with_permits failed: {json.dumps(tx_res, indent=2)}")

    print("[✓] execute_with_permits success; threshold approvals accepted.")

    # Optional: fetch and print recent events
    try:
        events = c.events("Executed", from_block=height, to_block="latest")  # type: ignore[attr-defined]
        print(f"[i] Recent Executed events: {len(events)}")
        for ev in events[-3:]:
            print("   -", ev)
    except Exception:
        pass

    print("\nAll done. Multisig deployed and basic permit flow exercised.\n")


if __name__ == "__main__":
    main()
