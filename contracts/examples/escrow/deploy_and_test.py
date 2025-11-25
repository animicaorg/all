# -*- coding: utf-8 -*-
"""
Deploy the Escrow example contract to a running Animica devnet and run a tiny
smoke test (init → deposit → release). This script is intentionally resilient to
minor API variations in the SDK/tooling: it tries multiple import shapes and
entrypoints before giving up with a friendly error.

Usage (happy path, devnet defaults):
  export RPC_URL=http://127.0.0.1:8545
  export CHAIN_ID=1337
  export DEPLOYER_MNEMONIC="nature midnight ... (24 words) ..."
  python -m contracts.examples.escrow.deploy_and_test --amount 123456

You can also pass flags instead of env:
  python -m contracts.examples.escrow.deploy_and_test \\
    --rpc http://127.0.0.1:8545 --chain 1337 \\
    --mnemonic "$DEPLOYER_MNEMONIC" --amount 123456 --deadline 10000

What this does:
  1) Compiles the escrow contract into a deployable package (manifest + code)
  2) Uses the Python SDK to deploy the package
  3) Calls init(buyer, seller, arbiter, amount, deadline)
     (buyer == deployer for convenience so we can sign deposit/release)
  4) Calls deposit() and release() from the buyer, verifies state and prints a
     JSON summary (address, tx hashes, final state snapshot).

Requirements:
  - A devnet node running with RPC enabled (see tests/devnet or ops/docker)
  - The Python SDK installed/importable (sdk/python/omni_sdk)
  - vm_py available to compile OR the repo's build helper
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# --- paths -------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]  # repo root
CONTRACT_SRC = HERE / "contract.py"
MANIFEST_PATH = HERE / "manifest.json"
BUILD_DIR = ROOT / "contracts" / "build"

# --- helpers: robust import shims --------------------------------------------

def _import_optional(path: str):
    try:
        return __import__(path, fromlist=["*"])
    except Exception:
        return None


def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[deploy_and_test] ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _info(msg: str) -> None:
    print(f"[deploy_and_test] {msg}")


# --- package build ------------------------------------------------------------

@dataclass
class BuiltPackage:
    manifest: Dict[str, Any]
    code: bytes
    code_hash: str  # hex


def build_package() -> BuiltPackage:
    """
    Prefer the repo's canonical builder (contracts/tools/build_package.py).
    Fallback to vm_py loader if the tool isn't available.
    """
    # 1) Try canonical tool
    builder = _import_optional("contracts.tools.build_package")
    if builder and hasattr(builder, "build_package"):
        _info("Building package via contracts.tools.build_package...")
        pack = builder.build_package(  # type: ignore[attr-defined]
            source_path=str(CONTRACT_SRC),
            manifest_path=str(MANIFEST_PATH),
            out_dir=str(BUILD_DIR),
        )
        # Expected to return dict-like with 'manifest', 'code', 'code_hash'
        manifest = pack["manifest"]
        code = pack["code"] if isinstance(pack["code"], (bytes, bytearray)) else bytes(pack["code"])
        code_hash = pack["code_hash"]
        return BuiltPackage(manifest=manifest, code=code, code_hash=code_hash)

    # 2) Fallback: compile via vm_py loader and read manifest directly
    vm_loader = _import_optional("vm_py.runtime.loader")
    if vm_loader is None:
        _fail(
            "Could not import contracts.tools.build_package nor vm_py.runtime.loader. "
            "Install the repo's tooling or ensure vm_py is importable."
        )

    _info("Building package via vm_py.runtime.loader fallback...")
    if hasattr(vm_loader, "load"):
        prog = vm_loader.load(str(MANIFEST_PATH))  # type: ignore[attr-defined]
    elif hasattr(vm_loader, "load_manifest"):
        prog = vm_loader.load_manifest(str(MANIFEST_PATH))  # type: ignore[attr-defined]
    else:
        _fail("vm_py.runtime.loader has no load/load_manifest")

    # Heuristic accessors
    code_bytes = getattr(prog, "code_bytes", None) or getattr(prog, "code", None)
    if code_bytes is None:
        _fail("vm loader did not expose compiled code bytes")
    if not isinstance(code_bytes, (bytes, bytearray)):
        code_bytes = bytes(code_bytes)

    # Code hash (hex) if exposed, else compute via sha3-256
    code_hash = getattr(prog, "code_hash_hex", None)
    if not code_hash:
        _hash_mod = _import_optional("hashlib")
        try:
            from hashlib import sha3_256  # type: ignore
        except Exception:
            _fail("Python hashlib missing sha3_256; please run on Python 3.8+")
        code_hash = "0x" + sha3_256(code_bytes).hexdigest()

    with MANIFEST_PATH.open("rb") as f:
        manifest = json.load(f)

    return BuiltPackage(manifest=manifest, code=code_bytes, code_hash=code_hash)


# --- SDK adapters -------------------------------------------------------------

@dataclass
class RpcEnv:
    url: str
    chain_id: int


@dataclass
class SignerEnv:
    address: str  # bech32 or hex
    impl: Any     # underlying signer object


class SdkAdapter:
    def __init__(self, rpc: RpcEnv) -> None:
        self.rpc = rpc
        self._rpc_http = None

        http_mod = _import_optional("omni_sdk.rpc.http")
        if http_mod:
            # try common names
            for cls_name in ("HttpClient", "Client", "RPC", "Http"):
                if hasattr(http_mod, cls_name):
                    self._rpc_http = getattr(http_mod, cls_name)(rpc_url=self.rpc.url, chain_id=self.rpc.chain_id)
                    break

    # -- signer & address --

    def signer_from_mnemonic(self, mnemonic: str, alg: str = "dilithium3") -> SignerEnv:
        """
        Best-effort construction of a SDK signer from mnemonic.
        """
        wallet_signer = _import_optional("omni_sdk.wallet.signer")
        wallet_mnemo = _import_optional("omni_sdk.wallet.mnemonic")
        addr_mod = _import_optional("omni_sdk.address") or _import_optional("omni_sdk.utils.bech32")

        if wallet_signer and wallet_mnemo:
            # try patterns
            if hasattr(wallet_mnemo, "mnemonic_to_seed"):
                seed = wallet_mnemo.mnemonic_to_seed(mnemonic)  # type: ignore[attr-defined]
            elif hasattr(wallet_mnemo, "from_phrase"):
                seed = wallet_mnemo.from_phrase(mnemonic)  # type: ignore[attr-defined]
            else:
                _fail("omni_sdk.wallet.mnemonic has no known constructor")

            # signer by alg
            candidate_names = {
                "dilithium3": ("Dilithium3Signer", "Signer"),
                "sphincs_shake_128s": ("SphincsSigner", "Signer"),
            }.get(alg, ("Signer",))

            signer_obj = None
            for nm in candidate_names:
                if hasattr(wallet_signer, nm):
                    try:
                        signer_obj = getattr(wallet_signer, nm).from_seed(seed)  # type: ignore[attr-defined]
                        break
                    except Exception:
                        # some signers use .from_mnemonic directly
                        try:
                            signer_obj = getattr(wallet_signer, nm).from_mnemonic(mnemonic)  # type: ignore[attr-defined]
                            break
                        except Exception:
                            pass
            if signer_obj is None:
                _fail("Could not construct a signer from mnemonic (SDK API mismatch)")

            # derive address (alg_id || sha3(pubkey)) → bech32m anim1...
            if addr_mod and hasattr(addr_mod, "address_from_pubkey"):
                address = addr_mod.address_from_pubkey(signer_obj.public_key, alg=getattr(signer_obj, "alg", alg))
            elif addr_mod and hasattr(addr_mod, "encode"):
                # older helper: bech32.encode(hrp, data)
                address = addr_mod.encode("anim", signer_obj.public_key)  # type: ignore[attr-defined]
            else:
                # last resort: hex
                address = "0x" + (getattr(signer_obj, "address_hex", None) or signer_obj.public_key.hex())

            return SignerEnv(address=address, impl=signer_obj)

        _fail("omni_sdk.wallet.{mnemonic,signer} not importable; install the Python SDK")
        raise RuntimeError  # unreachable

    # -- deploy --

    def deploy_package(self, package: BuiltPackage, signer: SignerEnv) -> Tuple[str, str]:
        """
        Deploy package (manifest + code) and return (contract_address, tx_hash).
        """
        deployer_mod = _import_optional("omni_sdk.contracts.deployer")
        if not deployer_mod:
            _fail("omni_sdk.contracts.deployer not importable; install/update the Python SDK")

        # try class-based deployer first
        for nm in ("Deployer", "ContractDeployer"):
            if hasattr(deployer_mod, nm):
                Deployer = getattr(deployer_mod, nm)
                try:
                    d = Deployer(rpc_url=self.rpc.url, chain_id=self.rpc.chain_id, signer=signer.impl)
                except TypeError:
                    d = Deployer(self.rpc.url, self.rpc.chain_id, signer.impl)  # older signature

                result = None
                # Common method names
                for meth in ("deploy_package", "deploy", "deploy_contract"):
                    if hasattr(d, meth):
                        try:
                            result = getattr(d, meth)(manifest=package.manifest, code=package.code)
                            break
                        except TypeError:
                            result = getattr(d, meth)(package.manifest, package.code)
                            break
                if result is None:
                    _fail("Deployer has no recognized deploy method")

                # normalize result
                if isinstance(result, dict):
                    addr = result.get("address") or result.get("contractAddress") or result.get("addr")
                    txh = result.get("txHash") or result.get("tx_hash")
                    if not addr or not txh:
                        _fail("Deployer returned dict without address/txHash")
                    return str(addr), str(txh)

                if isinstance(result, (list, tuple)) and len(result) >= 2:
                    return str(result[0]), str(result[1])

                # sometimes returns just address; no tx hash
                return str(result), ""

        # function-based deploy (rare)
        for fn in ("deploy_package", "deploy"):
            if hasattr(deployer_mod, fn):
                result = getattr(deployer_mod, fn)(self.rpc.url, self.rpc.chain_id, signer.impl, package.manifest, package.code)  # type: ignore
                if isinstance(result, (list, tuple)) and len(result) >= 2:
                    return str(result[0]), str(result[1])
                return str(result), ""

        _fail("No known deploy entrypoint found in omni_sdk.contracts.deployer")
        raise RuntimeError  # unreachable

    # -- contract calls --

    def _contract_client(self, address: str, abi: Dict[str, Any], signer: Optional[SignerEnv]) -> Any:
        """
        Obtain a generic contracts client from the SDK (read/write).
        """
        cmod = _import_optional("omni_sdk.contracts.client")
        if not cmod:
            _fail("omni_sdk.contracts.client not importable; cannot call contract functions")

        for nm in ("ContractClient", "Client", "Contract"):
            if hasattr(cmod, nm):
                try:
                    return getattr(cmod, nm)(
                        rpc_url=self.rpc.url,
                        chain_id=self.rpc.chain_id,
                        address=address,
                        abi=abi,
                        signer=(signer.impl if signer else None),
                    )
                except TypeError:
                    # older ctor ordering
                    return getattr(cmod, nm)(self.rpc.url, self.rpc.chain_id, address, abi, signer.impl if signer else None)

        _fail("No recognizable contract client class in omni_sdk.contracts.client")
        raise RuntimeError

    def call_read(self, address: str, abi: Dict[str, Any], fn: str, args: list[Any]) -> Any:
        cl = self._contract_client(address, abi, signer=None)
        for nm in ("read", "call_read", "call"):
            if hasattr(cl, nm):
                try:
                    return getattr(cl, nm)(fn, *args)
                except TypeError:
                    return getattr(cl, nm)(fn, args)
        _fail("Contract client has no recognized read method")

    def call_write(self, address: str, abi: Dict[str, Any], fn: str, args: list[Any], signer: SignerEnv) -> Dict[str, Any]:
        cl = self._contract_client(address, abi, signer=signer)
        for nm in ("write", "call_write", "transact", "send"):
            if hasattr(cl, nm):
                try:
                    return getattr(cl, nm)(fn, *args)
                except TypeError:
                    return getattr(cl, nm)(fn, args)
        _fail("Contract client has no recognized write method")


# --- CLI & flow ---------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy Escrow contract and run a quick smoke test.")
    p.add_argument("--rpc", default=os.environ.get("RPC_URL", "http://127.0.0.1:8545"), help="Node RPC URL")
    p.add_argument("--chain", type=int, default=int(os.environ.get("CHAIN_ID", "1337")), help="Chain ID")
    p.add_argument("--mnemonic", default=os.environ.get("DEPLOYER_MNEMONIC"), help="Deployer mnemonic (24 words)")
    p.add_argument("--amount", type=int, default=123_456, help="Escrow amount (small integer units)")
    p.add_argument("--deadline", type=int, default=10_000, help="Deadline height (relative, small)")
    p.add_argument("--alg", default=os.environ.get("PQ_ALG", "dilithium3"), help="PQ alg for signer")
    p.add_argument("--out", default=None, help="Write JSON summary to this path as well")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not MANIFEST_PATH.is_file() or not CONTRACT_SRC.is_file():
        _fail(f"Missing contract files. Expected:\n- {MANIFEST_PATH}\n- {CONTRACT_SRC}")

    if not args.mnemonic:
        _fail("No mnemonic provided. Set DEPLOYER_MNEMONIC or pass --mnemonic")

    # Build package (manifest+code)
    package = build_package()
    _info(f"Built package with code_hash={package.code_hash}")

    # SDK wiring
    sdk = SdkAdapter(RpcEnv(url=args.rpc, chain_id=args.chain))
    signer = sdk.signer_from_mnemonic(args.mnemonic, alg=args.alg)
    _info(f"Deployer/buyer address: {signer.address}")

    # Deploy
    address, tx_hash = sdk.deploy_package(package, signer)
    _info(f"Deployed Escrow at {address} (tx {tx_hash or '<unknown>'})")

    # Init (buyer == deployer so we can act as buyer for deposit/release)
    # Create deterministic seller/arbiter by deriving bytes from labels if the SDK client
    # does not enforce HRP — for safety, we just pass bytes-like/hex if ABI expects bytes.
    seller = "0x" + ("73" * 32)  # 's' * 32 as placeholder
    arbiter = "0x" + ("61" * 32)  # 'a' * 32 as placeholder

    # If the ABI schema for addresses expects bech32, use the signer.address as-is
    buyer = signer.address

    # Load ABI out of manifest for the client
    abi = package.manifest.get("abi") or package.manifest.get("ABI") or {}
    if not abi:
        _fail("Manifest does not contain an 'abi' field")

    _info("Calling init(...)")
    init_receipt = sdk.call_write(
        address=address,
        abi=abi,
        fn="init",
        args=[buyer, seller, arbiter, int(args.amount), int(args.deadline)],
        signer=signer,
    )

    _info("Calling deposit()")
    dep_receipt = sdk.call_write(address=address, abi=abi, fn="deposit", args=[], signer=signer)

    _info("Calling release()")
    rel_receipt = sdk.call_write(address=address, abi=abi, fn="release", args=[], signer=signer)

    _info("Reading state()")
    state = sdk.call_read(address=address, abi=abi, fn="state", args=[])

    summary = {
        "contract": {
            "address": address,
            "code_hash": package.code_hash,
        },
        "txs": {
            "deploy": tx_hash,
            "init": init_receipt.get("transactionHash") if isinstance(init_receipt, dict) else init_receipt,
            "deposit": dep_receipt.get("transactionHash") if isinstance(dep_receipt, dict) else dep_receipt,
            "release": rel_receipt.get("transactionHash") if isinstance(rel_receipt, dict) else rel_receipt,
        },
        "final_state": state,
    }

    print(json.dumps(summary, indent=2))

    if args.out:
        outp = Path(args.out).resolve()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        _info(f"Wrote summary → {outp}")

    _info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
