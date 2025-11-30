#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy_all.py — deploy every compiled contract package in this workspace.

What it does
------------
- Reads RPC_URL and CHAIN_ID (from environment or a local .env file).
- Loads the deployer's mnemonic (DEPLOYER_MNEMONIC) or exits with a helpful message.
- Discovers built contract packages under ./build/<name>/ produced by build_all.py.
- For each package:
    * Loads manifest.json and code.ir (compiled IR bytes).
    * Computes/reads code_hash (sha3-256 of IR).
    * Skips if deployments.json already holds the same code_hash for this contract (unless --force).
    * Deploys via the Python SDK (omni_sdk) if available; otherwise tries the SDK CLI.
    * Waits for receipt (unless --no-wait) and records address, tx_hash, code_hash.

Outputs
-------
- ./build/deployments.json — map of contract name → {address, tx_hash, code_hash, chain_id}
- A console summary table (or JSON with --summary json)

Usage
-----
$ python3 scripts/deploy_all.py
$ RPC_URL=http://127.0.0.1:8545 CHAIN_ID=1337 DEPLOYER_MNEMONIC="..." python3 scripts/deploy_all.py
$ python3 scripts/deploy_all.py --build-dir ./build --summary json --force

Notes
-----
- Deterministic & idempotent: by default, if code_hash matches an existing deployment record,
  the contract is skipped unless you pass --force.
- The script prefers the Python SDK, with a safe CLI fallback (omni-sdk deploy).
- No private keys are stored on disk; a mnemonic is used to derive a signing key in-process.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from hashlib import sha3_256
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# -----------------------------------------------------------------------------


@dataclass
class PackageInfo:
    name: str
    dir: Path
    manifest_path: Path
    ir_path: Path
    manifest: Dict
    code_hash_hex: str


@dataclass
class DeployResult:
    name: str
    code_hash_hex: str
    tx_hash: Optional[str]
    address: Optional[str]
    chain_id: Optional[int]
    skipped: bool
    warnings: List[str]
    errors: List[str]

    @property
    def ok(self) -> bool:
        return self.skipped or (self.address is not None and not self.errors)


# -----------------------------------------------------------------------------


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy all compiled contract packages.")
    p.add_argument(
        "--build-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "build",
        help="Directory containing per-contract build outputs (default: ./build)",
    )
    p.add_argument(
        "--summary",
        choices=("table", "json"),
        default="table",
        help="How to print the final summary.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Force deployment even if deployments.json already has a matching code_hash.",
    )
    p.add_argument(
        "--no-wait",
        action="store_true",
        help="Submit the deploy tx and do not wait for the receipt/address.",
    )
    p.add_argument(
        "--alg",
        choices=("dilithium3", "sphincs_shake_128s"),
        default=os.environ.get("PQ_ALG", "dilithium3"),
        help="Post-quantum signature algorithm to use for deployment.",
    )
    p.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".env",
        help="Optional path to a .env file with RPC_URL, CHAIN_ID, DEPLOYER_MNEMONIC.",
    )
    return p.parse_args(argv)


# -----------------------------------------------------------------------------


def parse_env_file(path: Path) -> Dict[str, str]:
    """
    Minimal .env parser (no external deps). Returns {KEY: VALUE}.
    Lines beginning with '#' are ignored. Supports simple KEY=VALUE pairs.
    """
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'").strip('"')
    return env


def load_runtime_env(env_file: Path) -> Tuple[str, int, str]:
    # Load .env first (if present), then overlay actual os.environ (wins).
    env_from_file = parse_env_file(env_file)
    for k, v in env_from_file.items():
        os.environ.setdefault(k, v)

    rpc_url = os.environ.get("RPC_URL")
    chain_id_raw = os.environ.get("CHAIN_ID")
    mnemonic = os.environ.get("DEPLOYER_MNEMONIC")

    if not rpc_url:
        sys.exit("Missing RPC_URL (set in environment or .env)")
    if not chain_id_raw:
        sys.exit("Missing CHAIN_ID (set in environment or .env)")
    try:
        chain_id = int(chain_id_raw, 0)
    except Exception:
        chain_id = int(chain_id_raw)

    if not mnemonic:
        sys.exit("Missing DEPLOYER_MNEMONIC (set in environment or .env)")

    return rpc_url, chain_id, mnemonic


# -----------------------------------------------------------------------------


def discover_packages(build_dir: Path) -> List[PackageInfo]:
    """
    A valid package directory contains:
      - manifest.json
      - code.ir
    Optionally:
      - package.json with { code_hash }
    """
    pkgs: List[PackageInfo] = []
    if not build_dir.is_dir():
        return pkgs

    for child in sorted(p for p in build_dir.iterdir() if p.is_dir()):
        manifest_path = child / "manifest.json"
        ir_path = child / "code.ir"
        if not (manifest_path.is_file() and ir_path.is_file()):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Prefer package.json code_hash if present; else compute from IR bytes.
        code_hash_hex: Optional[str] = None
        pkg_json = child / "package.json"
        if pkg_json.is_file():
            try:
                meta = json.loads(pkg_json.read_text(encoding="utf-8"))
                code_hash_str = meta.get("code_hash") or meta.get("codeHash")
                if isinstance(code_hash_str, str):
                    code_hash_hex = code_hash_str.lower().removeprefix("0x")
            except Exception:
                code_hash_hex = None
        if not code_hash_hex:
            code_hash_hex = sha3_256(ir_path.read_bytes()).hexdigest()

        name = manifest.get("name", child.name)
        pkgs.append(
            PackageInfo(
                name=name,
                dir=child,
                manifest_path=manifest_path,
                ir_path=ir_path,
                manifest=manifest,
                code_hash_hex=code_hash_hex,
            )
        )
    return pkgs


# -----------------------------------------------------------------------------


def read_deployments_index(build_dir: Path) -> Dict[str, Dict[str, object]]:
    idx_path = build_dir / "deployments.json"
    if not idx_path.exists():
        return {}
    try:
        return json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_deployments_index(
    build_dir: Path, index: Dict[str, Dict[str, object]]
) -> None:
    idx_path = build_dir / "deployments.json"
    tmp = json.dumps(index, indent=2, sort_keys=True) + "\n"
    idx_path.write_text(tmp, encoding="utf-8")


# -----------------------------------------------------------------------------


def make_signer_from_mnemonic(mnemonic: str, alg: str):
    """
    Try several SDK shapes to derive a signer from a mnemonic.
    Returns an object acceptable by omni_sdk deploy helpers, or raises on failure.
    """
    # Newer SDKs may expose a convenience factory:
    try:
        from omni_sdk.wallet.signer import from_mnemonic  # type: ignore

        return from_mnemonic(mnemonic, alg=alg)
    except Exception:
        pass

    # Alternate API: Mnemonic → seed → specific signer class
    try:
        from omni_sdk.wallet import mnemonic as sdk_mn  # type: ignore
        from omni_sdk.wallet import signer as sdk_signer  # type: ignore

        # Find a symbol that looks like a constructor (class or function) for the selected alg.
        if hasattr(sdk_signer, "Signer"):
            # One-size-fits-all factory
            return sdk_signer.Signer.from_mnemonic(mnemonic, alg=alg)

        if alg == "dilithium3" and hasattr(sdk_signer, "Dilithium3Signer"):
            return sdk_signer.Dilithium3Signer.from_mnemonic(mnemonic)
        if alg == "sphincs_shake_128s" and hasattr(sdk_signer, "SphincsSigner"):
            return sdk_signer.SphincsSigner.from_mnemonic(mnemonic)

        # Last resort: a generic helper
        if hasattr(sdk_mn, "from_mnemonic") and hasattr(sdk_signer, "from_seed"):
            seed = sdk_mn.from_mnemonic(mnemonic)  # type: ignore
            return sdk_signer.from_seed(seed, alg=alg)  # type: ignore
    except Exception:
        pass

    raise RuntimeError(
        "Could not construct a signer from mnemonic with the installed omni_sdk. "
        "Please update the SDK, or provide a compatible signer factory."
    )


def deploy_via_python_sdk(
    pkg: PackageInfo, rpc_url: str, chain_id: int, signer, wait_for_receipt: bool
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    Deploy using omni_sdk's Python API.
    Returns: (tx_hash, address, warnings)
    """
    warnings: List[str] = []
    # Try several possible public interfaces for compatibility.
    # Preferred: a Deployer class with deploy(manifest, ir_bytes, ...)
    try:
        from omni_sdk.contracts.deployer import Deployer  # type: ignore
        from omni_sdk.rpc.http import HttpClient  # type: ignore

        client = (
            HttpClient(rpc_url)
            if "http" in HttpClient.__name__.lower()
            else HttpClient(rpc_url)
        )
        with pkg.ir_path.open("rb") as fh:
            ir_bytes = fh.read()

        deployer = Deployer(client=client, signer=signer, chain_id=chain_id)
        result = deployer.deploy(
            manifest=pkg.manifest,
            code_ir=ir_bytes,
            wait=wait_for_receipt,
        )
        # Flexible extraction:
        tx_hash = (
            getattr(result, "tx_hash", None)
            or getattr(result, "txHash", None)
            or result.get("tx_hash")
        )
        address = getattr(result, "address", None) or result.get("address")
        return tx_hash, address, warnings
    except Exception as e:
        warnings.append(f"Python SDK Deployer path failed: {e}")

    # Alternate shape: functional helper deploy_package(...)
    try:
        from omni_sdk.contracts.deployer import deploy_package  # type: ignore

        tx_hash, address = deploy_package(
            rpc_url=rpc_url,
            signer=signer,
            chain_id=chain_id,
            manifest=pkg.manifest,
            code_ir=pkg.ir_path.read_bytes(),
            wait=wait_for_receipt,
        )
        return tx_hash, address, warnings
    except Exception as e:
        warnings.append(f"Python SDK functional path failed: {e}")

    raise RuntimeError("No compatible Python SDK deploy function found.")


def deploy_via_cli(
    pkg: PackageInfo,
    rpc_url: str,
    chain_id: int,
    mnemonic: str,
    alg: str,
    wait_for_receipt: bool,
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    Fallback path: shell out to the omni-sdk CLI.
    Returns: (tx_hash, address, warnings)
    """
    warnings: List[str] = []

    candidates = [
        # Module path
        [sys.executable, "-m", "omni_sdk.cli.deploy"],
        # Console script
        ["omni-sdk", "deploy"],
    ]

    for base in candidates:
        try:
            cmd = base + [
                "--rpc-url",
                rpc_url,
                "--chain-id",
                str(chain_id),
                "--mnemonic",
                mnemonic,
                "--alg",
                alg,
                "--manifest",
                str(pkg.manifest_path),
                "--code-ir",
                str(pkg.ir_path),
            ]
            if not wait_for_receipt:
                cmd.append("--no-wait")

            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if proc.returncode != 0:
                warnings.append(
                    f"CLI deploy failed with {' '.join(base)} "
                    f"(exit {proc.returncode}).\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
                )
                continue

            # Heuristic: CLI prints JSON on stdout
            stdout = (proc.stdout or "").strip()
            tx_hash, address = None, None
            try:
                payload = json.loads(stdout)
                tx_hash = payload.get("tx_hash") or payload.get("txHash")
                address = payload.get("address")
            except Exception:
                # Try to grep basic tokens from stdout
                for line in stdout.splitlines():
                    if "tx" in line.lower() and "0x" in line:
                        tx_hash = tx_hash or line.strip().split()[-1]
                    if "address" in line.lower() and "anim1" in line.lower():
                        address = address or line.strip().split()[-1]
            return tx_hash, address, warnings
        except FileNotFoundError:
            warnings.append(f"CLI not found: {' '.join(base)}")
        except Exception as e:
            warnings.append(f"CLI error ({' '.join(base)}): {e}")

    raise RuntimeError("All CLI deployment attempts failed.")


# -----------------------------------------------------------------------------


def print_summary(results: List[DeployResult], mode: str = "table") -> None:
    if mode == "json":
        output = [
            {
                "name": r.name,
                "ok": r.ok,
                "skipped": r.skipped,
                "code_hash": f"0x{r.code_hash_hex}",
                "tx_hash": r.tx_hash,
                "address": r.address,
                "chain_id": r.chain_id,
                "warnings": r.warnings,
                "errors": r.errors,
            }
            for r in results
        ]
        print(json.dumps(output, indent=2))
        return

    # table
    from shutil import get_terminal_size

    cols = get_terminal_size((120, 24)).columns
    print("=" * cols)
    print("Deployment Summary".center(cols))
    print("=" * cols)
    header = (
        f"{'CONTRACT':20}  {'STATUS':8}  {'CODE_HASH (sha3-256)':66}  {'ADDRESS':42}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        status = "SKIP" if r.skipped else ("OK" if r.ok else "FAIL")
        ch = f"0x{r.code_hash_hex}"
        addr = r.address or "-"
        print(f"{r.name:20}  {status:8}  {ch:66}  {addr:42}")
        for w in r.warnings:
            print(f"  ⚠ {w}")
        for e in r.errors:
            print(f"  ✖ {e}")
    print("-" * len(header))
    failures = [r for r in results if (not r.skipped and not r.ok)]
    if failures:
        print(f"Result: {len(failures)} failure(s).", file=sys.stderr)
    else:
        print("Result: all deployments succeeded or were skipped as up-to-date.")


# -----------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)

    rpc_url, chain_id, mnemonic = load_runtime_env(args.env_file)

    # Discover packages
    packages = discover_packages(args.build_dir)
    if not packages:
        print(
            f"No packages found under {args.build_dir}. Run scripts/build_all.py first.",
            file=sys.stderr,
        )
        return 1

    # Read (or create) deployments index
    deploy_index = read_deployments_index(args.build_dir)

    results: List[DeployResult] = []

    # Prepare signer (Python SDK path)
    signer = None
    signer_error: Optional[str] = None
    try:
        signer = make_signer_from_mnemonic(mnemonic, args.alg)
    except Exception as e:
        signer_error = str(e)

    for pkg in packages:
        warnings: List[str] = []
        errors: List[str] = []

        # Idempotency: skip if the same code hash is already deployed for this name
        prior = deploy_index.get(pkg.name)
        if (
            prior
            and isinstance(prior, dict)
            and str(prior.get("chain_id")) == str(chain_id)
            and isinstance(prior.get("code_hash"), str)
            and prior["code_hash"].lower().removeprefix("0x")
            == pkg.code_hash_hex.lower()
            and not args.force
        ):
            results.append(
                DeployResult(
                    name=pkg.name,
                    code_hash_hex=pkg.code_hash_hex,
                    tx_hash=prior.get("tx_hash"),
                    address=prior.get("address"),
                    chain_id=chain_id,
                    skipped=True,
                    warnings=warnings,
                    errors=errors,
                )
            )
            continue

        tx_hash: Optional[str] = None
        address: Optional[str] = None

        # Try Python SDK path first (preferred)
        if signer is not None:
            try:
                tx_hash, address, warns = deploy_via_python_sdk(
                    pkg,
                    rpc_url=rpc_url,
                    chain_id=chain_id,
                    signer=signer,
                    wait_for_receipt=(not args.no_wait),
                )
                warnings.extend(warns)
            except Exception as e:
                warnings.append(f"Python SDK path failed: {e}")
        else:
            warnings.append(f"No Python SDK signer available: {signer_error}")

        # CLI fallback if needed
        if address is None and tx_hash is None:
            try:
                tx_hash, address, warns = deploy_via_cli(
                    pkg,
                    rpc_url=rpc_url,
                    chain_id=chain_id,
                    mnemonic=mnemonic,
                    alg=args.alg,
                    wait_for_receipt=(not args.no_wait),
                )
                warnings.extend(warns)
            except Exception as e:
                errors.append(f"CLI fallback failed: {e}")

        # Record result
        res = DeployResult(
            name=pkg.name,
            code_hash_hex=pkg.code_hash_hex,
            tx_hash=tx_hash,
            address=address,
            chain_id=chain_id,
            skipped=False,
            warnings=warnings,
            errors=errors,
        )
        results.append(res)

        # Persist/update deployments index on success or when we have at least a tx hash
        if res.ok and (res.address or res.tx_hash):
            deploy_index[pkg.name] = {
                "address": res.address,
                "tx_hash": res.tx_hash,
                "code_hash": f"0x{pkg.code_hash_hex}",
                "chain_id": chain_id,
            }
            write_deployments_index(args.build_dir, deploy_index)

    # Final summary
    print_summary(results, mode=args.summary)

    return 0 if all(r.ok for r in results) else 2


if __name__ == "__main__":
    # Encourage deterministic behavior across Python versions
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
