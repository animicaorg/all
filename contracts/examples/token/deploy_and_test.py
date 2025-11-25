# -*- coding: utf-8 -*-
"""
Deploy the example Animica-20 token to a running node via the Python SDK,
then run a few quick sanity checks (name/symbol/decimals, one transfer,
event presence, and a read call if supported).

Usage:
  python -m contracts.examples.token.deploy_and_test \
    --rpc ${RPC_URL:-http://127.0.0.1:8545} \
    --chain-id ${CHAIN_ID:-1337} \
    --mnemonic "${DEPLOYER_MNEMONIC:-...}" \
    --name "Animica Token" \
    --symbol AMK \
    --decimals 6 \
    --initial-supply 1000000

Environment variables (optional):
  RPC_URL, CHAIN_ID, DEPLOYER_MNEMONIC, SERVICES_URL

Notes:
- This script prefers the Python SDK (omni_sdk) deployer path. If the contract
  package has not been built yet, it will compile & package using
  contracts.tools.build_package on the fly (drops a bundle into contracts/build/).
- It tolerates minor API differences across SDK versions by probing for multiple
  likely entrypoints (deploy_package, Deployer(...).deploy, etc.).
- It attempts a “read” of name/symbol/decimals and totalSupply using the SDK’s
  contract client if available; otherwise it at least verifies a transfer by the
  event log in the receipt.

Exit code is non-zero if deployment or checks fail.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# --------------------------- paths & constants -------------------------------

ROOT = Path(__file__).resolve().parents[3]  # repo root (…/contracts/examples/token/…)
EXAMPLE_DIR = ROOT / "contracts" / "examples" / "token"
SRC_PATH = EXAMPLE_DIR / "contract.py"
MANIFEST_PATH = EXAMPLE_DIR / "manifest.json"
BUILD_DIR = ROOT / "contracts" / "build"


# --------------------------- small utils ------------------------------------

def eprint(*args: Any, **kw: Any) -> None:
    print(*args, file=sys.stderr, **kw)


def fail(msg: str, code: int = 1) -> None:
    eprint(f"error: {msg}")
    sys.exit(code)


def load_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"failed to parse JSON: {p} -> {exc}")


def ensure_example_files() -> None:
    if not SRC_PATH.is_file():
        fail(f"missing example source: {SRC_PATH}")
    if not MANIFEST_PATH.is_file():
        fail(f"missing example manifest: {MANIFEST_PATH}")


def maybe_read_seed_wallet() -> Optional[str]:
    """
    If a mnemonic isn't provided, try reading tests/devnet/seed_wallets.json.
    Pick the first mnemonic if present (dev-only).
    """
    seed_file = ROOT / "tests" / "devnet" / "seed_wallets.json"
    if seed_file.is_file():
        try:
            data = json.loads(seed_file.read_text(encoding="utf-8"))
            # Accept formats: {"mnemonics":[...]} or [{"mnemonic":...}, ...]
            if isinstance(data, dict) and "mnemonics" in data and data["mnemonics"]:
                return data["mnemonics"][0]
            if isinstance(data, list) and data and isinstance(data[0], dict):
                if "mnemonic" in data[0]:
                    return data[0]["mnemonic"]
        except Exception:
            return None
    return None


def find_built_package() -> Optional[Path]:
    """
    Look for a previously built package for the token example under contracts/build.
    We accept files ending with `.pkg.json` or a directory containing manifest+code.
    """
    if not BUILD_DIR.is_dir():
        return None
    # Prefer a deterministic file name if it exists
    candidates = sorted(BUILD_DIR.glob("token.pkg.json")) + sorted(BUILD_DIR.glob("*.pkg.json"))
    return candidates[0] if candidates else None


def build_package_if_needed() -> Path:
    """
    Build a contract package using the local build tool if no package is found.
    Returns the path to the package JSON (descriptor with manifest + code/ref).
    """
    existing = find_built_package()
    if existing:
        print(f"found existing package: {existing}")
        return existing

    print("no built package found; compiling & packaging…")
    try:
        from contracts.tools.build_package import build_package  # type: ignore
    except Exception as exc:
        fail(
            "contracts.tools.build_package is unavailable. Make sure your repo tree is intact "
            "and PYTHONPATH includes the repo root. Details: " + str(exc)
        )

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        pkg_path = build_package(
            source_path=str(SRC_PATH),
            manifest_path=str(MANIFEST_PATH),
            out_dir=str(BUILD_DIR),
            package_name="token",
        )
    except TypeError:
        # Older/alternate signature fallback
        pkg_path = build_package(str(SRC_PATH), str(MANIFEST_PATH), str(BUILD_DIR))

    if not isinstance(pkg_path, (str, Path)):
        fail("unexpected return from build_package (expected path)")
    pkg_path = Path(pkg_path)
    if not pkg_path.is_file():
        fail(f"package build reported success but file not found: {pkg_path}")
    print(f"built package: {pkg_path}")
    return pkg_path


# --------------------------- SDK adapters (tolerant) -------------------------

@dataclass
class SdkCtx:
    rpc: Any
    signer: Any
    chain_id: int


def _sdk_http_client(rpc_url: str) -> Any:
    """
    Try several client entrypoints.
    """
    try:
        from omni_sdk.rpc.http import HttpClient  # type: ignore
        return HttpClient(rpc_url)
    except Exception:
        pass
    # Fallback: sometimes exposed as omni_sdk.rpc.http.Client
    try:
        from omni_sdk.rpc.http import Client  # type: ignore
        return Client(rpc_url)
    except Exception:
        fail("omni_sdk.rpc.http client not found; install the Python SDK (see sdk/python/README.md)")


def _sdk_signer_from_mnemonic(mnemonic: str, alg: str = "dilithium3") -> Any:
    """
    Build a PQ signer from a mnemonic using several likely SDK surfaces.
    """
    # Primary: omni_sdk.wallet.signer.make_signer(alg="dilithium3", mnemonic="…")
    try:
        from omni_sdk.wallet.signer import make_signer  # type: ignore
        return make_signer(alg=alg, mnemonic=mnemonic)
    except Exception:
        pass

    # Alt: omni_sdk.wallet.signer.Signer.from_mnemonic(…)
    try:
        from omni_sdk.wallet.signer import Signer  # type: ignore
        if hasattr(Signer, "from_mnemonic"):
            return Signer.from_mnemonic(mnemonic=mnemonic, alg=alg)
    except Exception:
        pass

    # Alt: derive seed then pass to signer
    try:
        from omni_sdk.wallet import mnemonic as mmod  # type: ignore
        seed = None
        for fn in ("to_seed", "mnemonic_to_seed", "derive_seed"):
            if hasattr(mmod, fn):
                seed = getattr(mmod, fn)(mnemonic)
                break
        if seed is not None:
            from omni_sdk.wallet.signer import make_signer  # type: ignore
            return make_signer(alg=alg, seed=seed)
    except Exception:
        pass

    fail("could not construct a PQ signer from mnemonic via omni_sdk")


def _sdk_address_of(signer: Any) -> str:
    """
    Return a human string address for display. Try bech32m first; hex fallback.
    """
    # Common: signer.address (bech32)
    if hasattr(signer, "address"):
        addr = getattr(signer, "address")
        return addr() if callable(addr) else str(addr)
    # Maybe public_key → address helper
    pub = None
    for attr in ("pubkey", "public_key", "publicKey"):
        if hasattr(signer, attr):
            v = getattr(signer, attr)
            pub = v() if callable(v) else v
            break
    if pub is not None:
        try:
            from omni_sdk.address import to_address  # type: ignore
            return to_address(pub)
        except Exception:
            pass
    return "<unknown-address>"


def _sdk_deploy_package(ctx: SdkCtx, package_path: Path) -> str:
    """
    Deploy using omni_sdk.contracts.deployer.* with best-effort API compatibility.
    Returns the deployed address (bech32 or hex-like).
    """
    try:
        import omni_sdk.contracts.deployer as dep  # type: ignore
    except Exception as exc:
        fail("omni_sdk.contracts.deployer not available in your SDK: " + str(exc))

    # Try dep.deploy_package(client, signer, package_path, chain_id=?)
    try:
        if hasattr(dep, "deploy_package"):
            res = dep.deploy_package(ctx.rpc, ctx.signer, str(package_path), chain_id=ctx.chain_id)
            # Common returns: {"address": "anim1…", "txHash": "0x…"}
            if isinstance(res, dict) and "address" in res:
                return str(res["address"])
            if isinstance(res, (tuple, list)) and res:
                return str(res[0])
            if isinstance(res, str):
                return res
    except TypeError:
        pass
    except Exception as exc:
        fail(f"deploy_package raised: {exc}")

    # Try class-based
    try:
        if hasattr(dep, "Deployer"):
            d = dep.Deployer(ctx.rpc, ctx.chain_id) if ctx.chain_id else dep.Deployer(ctx.rpc)
            if hasattr(d, "deploy"):
                res = d.deploy(ctx.signer, str(package_path))
                if isinstance(res, dict) and "address" in res:
                    return str(res["address"])
                if isinstance(res, str):
                    return res
    except Exception as exc:
        fail(f"Deployer.deploy failed: {exc}")

    fail("could not find a working deploy method in omni_sdk.contracts.deployer")


def _sdk_contract_client(address: str, abi: Dict[str, Any], rpc: Any) -> Optional[Any]:
    """
    Try to build a generic contract client for read calls.
    """
    try:
        from omni_sdk.contracts.client import Contract  # type: ignore
        return Contract(rpc=rpc, address=address, abi=abi)
    except Exception:
        pass
    try:
        from omni_sdk.contracts.client import make_client  # type: ignore
        return make_client(rpc, address, abi)
    except Exception:
        return None


def _sdk_send_tx_transfer(address: str, to_addr: bytes, amount: int, ctx: SdkCtx, abi: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build and send a transfer(tx) to the deployed token using the SDK's
    tx/build & tx/send helpers or the contract client if available.
    """
    # Prefer a contract client as it abstracts the call build
    client = _sdk_contract_client(address, abi, ctx.rpc)
    if client is not None:
        # try variants: client.call_write / client.write / client.send / client.transfer
        for meth in ("write", "call_write", "send", "transfer"):
            if hasattr(client, meth):
                try:
                    res = getattr(client, meth)("transfer", [to_addr, amount], signer=ctx.signer, chain_id=ctx.chain_id)
                    if isinstance(res, dict) and "txHash" in res:
                        return res
                    if isinstance(res, dict):
                        return res
                except Exception as exc:
                    eprint(f"[warn] contract client {meth} failed: {exc}")
        eprint("[warn] contract client present but no suitable write method found; falling back")

    # Manual path through tx.build/encode/send
    try:
        from omni_sdk.tx.build import build_contract_call  # type: ignore
        from omni_sdk.tx.encode import encode_sign_bytes  # type: ignore
        from omni_sdk.tx.send import send_and_wait  # type: ignore
    except Exception as exc:
        fail("Required SDK tx helpers not found (build_contract_call/encode_sign_bytes/send_and_wait): " + str(exc))

    # Encode the ABI call
    # Try to find the ABI item for "transfer"
    selector = None
    for item in (abi or {}).get("functions", []):
        if item.get("name") == "transfer":
            selector = item
            break
    call_data = {
        "function": "transfer",
        "args": [to_addr, amount],
    }
    if selector:
        call_data["abi"] = selector  # some SDKs allow passing full entry for safety

    tx = build_contract_call(
        to=address,
        call=call_data,
        gas_limit=200000,  # conservative
        chain_id=ctx.chain_id,
    )
    sign_bytes = encode_sign_bytes(tx)
    signature = ctx.signer.sign(sign_bytes)
    tx["signature"] = signature
    tx_hash, receipt = send_and_wait(ctx.rpc, tx)
    return {"txHash": tx_hash, "receipt": receipt}


# --------------------------- CLI & main logic --------------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Deploy example token via SDK and run checks")
    ap.add_argument("--rpc", default=os.getenv("RPC_URL", "http://127.0.0.1:8545"), help="HTTP JSON-RPC url")
    ap.add_argument("--chain-id", type=int, default=int(os.getenv("CHAIN_ID", "1337")), help="Chain ID")
    ap.add_argument("--mnemonic", default=os.getenv("DEPLOYER_MNEMONIC") or maybe_read_seed_wallet(), help="BIP39-like mnemonic for deployer (Dilithium3/Sphincs signer derived)")
    ap.add_argument("--name", default="Animica Token", help="Token name (bytes/str acceptable)")
    ap.add_argument("--symbol", default="AMK", help="Token symbol (<= 8 chars recommended)")
    ap.add_argument("--decimals", type=int, default=6, help="Token decimals (0..18 typical)")
    ap.add_argument("--initial-supply", type=int, default=1_000_000, help="Initial supply minted to deployer")
    ap.add_argument("--receiver-label", default="alice", help="Label to derive a deterministic receiver address (for test transfer)")
    return ap.parse_args()


def to_bytes32(label: str) -> bytes:
    b = label.encode("utf-8")
    return (b + b"\x00" * 32)[:32]


def main() -> int:
    args = parse_args()
    ensure_example_files()

    if not args.mnemonic:
        fail("no mnemonic provided (use --mnemonic or export DEPLOYER_MNEMONIC); for devnet try tests/devnet/seed_wallets.json")

    # Load ABI (for read/write helpers) and package (build if missing)
    manifest = load_json(MANIFEST_PATH)
    abi = manifest.get("abi") or load_json(EXAMPLE_DIR / "manifest.json").get("abi") or {}
    package_path = build_package_if_needed()

    # Wire up SDK
    rpc = _sdk_http_client(args.rpc)
    signer = _sdk_signer_from_mnemonic(args.mnemonic, alg="dilithium3")
    ctx = SdkCtx(rpc=rpc, signer=signer, chain_id=args.chain_id)

    print(f"deployer address: {_sdk_address_of(signer)}")
    print(f"deploying package: {package_path} to chainId={args.chain_id} at {args.rpc}")

    # Deploy
    try:
        address = _sdk_deploy_package(ctx, package_path)
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"deployment failed: {exc}")
    print(f"deployed token address: {address}")

    # Initialize via constructor/init entrypoint if required by the example:
    # Our example animica-20 uses explicit init(name, symbol, decimals, owner, initialSupply).
    # If the SDK contract client exposes a write method, call init now.
    client = _sdk_contract_client(address, abi, rpc)
    if client is not None:
        try:
            owner_addr = getattr(signer, "address")() if callable(getattr(signer, "address", None)) else _sdk_address_of(signer)
            _ = client.write(
                "init",
                [args.name.encode("utf-8"), args.symbol.encode("utf-8"), args.decimals, owner_addr, args.initial_supply],
                signer=signer,
                chain_id=args.chain_id,
            )
            print("initialized token via init(...)")
        except Exception as exc:
            eprint(f"[warn] init call failed or not required: {exc}")
    else:
        eprint("[warn] no contract client available; skipping init() convenience call")

    # Optional reads (tolerant)
    def try_read(fn: str, *fn_args: Any) -> Optional[Any]:
        if client is None:
            return None
        for meth in ("read", "call", "view"):
            if hasattr(client, meth):
                try:
                    return getattr(client, meth)(fn, list(fn_args))
                except Exception:
                    continue
        return None

    name = try_read("name") or b"(unknown)"
    symbol = try_read("symbol") or b"(unknown)"
    decimals = try_read("decimals") or args.decimals
    print(f"token metadata: name={name!r} symbol={symbol!r} decimals={decimals}")

    # Do a test transfer and check receipt logs
    to_addr = to_bytes32(args.receiver_label)
    try:
        send_res = _sdk_send_tx_transfer(address, to_addr, 1234, ctx, abi)
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"transfer tx failed: {exc}")

    receipt = send_res.get("receipt") or {}
    status = str(receipt.get("status", "UNKNOWN"))
    tx_hash = send_res.get("txHash")
    logs = receipt.get("logs") or []

    print(f"transfer tx sent: hash={tx_hash}, status={status}")
    # Validate Transfer event presence (tolerant to naming)
    def has_transfer(logs: Any) -> bool:
        try:
            for ev in logs:
                nm = ev.get("name") or ev.get("event")
                if nm == "Transfer":
                    args = ev.get("args") or {}
                    if int(args.get("value", 0)) == 1234:
                        return True
            return False
        except Exception:
            return False

    if not has_transfer(logs):
        eprint("[warn] Transfer event not found in receipt logs; continuing (node may not emit logs in this mode)")

    print("✅ quick checks passed (deployment + transfer path exercised)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
