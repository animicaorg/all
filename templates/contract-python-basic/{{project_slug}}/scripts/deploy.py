#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deploy the contract from this template to an Animica node.

This script is intentionally minimal and robust:
- Reads config from CLI flags or .env (RPC_URL, CHAIN_ID, DEPLOYER_MNEMONIC)
- Ensures a deterministic build exists (runs scripts/build.py if needed)
- Invokes the Python SDK CLI (omni_sdk) to perform the deploy
- Prints the deployed address and tx hash, and writes a JSON artifact to ./build/

Requirements
- Python 3.9+
- This repo's SDK in your environment (the local mono-repo works), or `pip install omni-sdk`
- A running node (devnet/testnet) at RPC_URL

Usage
  python scripts/deploy.py
  python scripts/deploy.py --rpc http://127.0.0.1:8545 --chain 1337 --mnemonic "word1 ... word12"
  python scripts/deploy.py --manifest contracts/manifest.json --no-build
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]  # {{project_slug}}/
DEFAULT_MANIFEST = ROOT / "contracts" / "manifest.json"
BUILD_DIR = ROOT / "build"
PKG_PATH = BUILD_DIR / "package.json"
DEPLOY_ARTIFACT = BUILD_DIR / "deploy_result.json"


# ------------------------------ .env loader ---------------------------------


def load_env_file(dotenv_path: Path) -> Dict[str, str]:
    """
    Tiny .env reader (no external deps). Supports lines like:
      KEY=value
      KEY="quoted value"
    Comments (# ...) and blank lines are ignored.
    """
    env: Dict[str, str] = {}
    if not dotenv_path.is_file():
        return env
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if (len(val) >= 2) and ((val[0] == val[-1]) and val[0] in ("'", '"')):
            val = val[1:-1]
        env[key] = val
    return env


def resolve_config(
    rpc_flag: Optional[str],
    chain_flag: Optional[int],
    mnemonic_flag: Optional[str],
) -> Tuple[str, int, str]:
    # Order of precedence: CLI flag > environment > .env file > defaults
    # Load .env if present
    env_path = ROOT / ".env"
    file_env = load_env_file(env_path)

    rpc = (
        rpc_flag
        or os.environ.get("RPC_URL")
        or file_env.get("RPC_URL")
        or "http://127.0.0.1:8545"
    )
    chain_str = (
        str(chain_flag) if chain_flag is not None else
        os.environ.get("CHAIN_ID")
        or file_env.get("CHAIN_ID")
        or "1337"
    )
    try:
        chain_id = int(chain_str, 10)
    except ValueError:
        raise SystemExit(f"[deploy] Invalid CHAIN_ID: {chain_str!r}")

    mnemonic = (
        mnemonic_flag
        or os.environ.get("DEPLOYER_MNEMONIC")
        or file_env.get("DEPLOYER_MNEMONIC")
        or ""
    )
    if not mnemonic.strip():
        raise SystemExit(
            "[deploy] Missing DEPLOYER_MNEMONIC. Provide via --mnemonic or set in .env"
        )

    return rpc, chain_id, mnemonic


# ------------------------------ build step ----------------------------------


def ensure_built(manifest_path: Path, no_build: bool) -> None:
    if PKG_PATH.is_file():
        return
    if no_build:
        raise SystemExit(
            f"[deploy] --no-build set but {PKG_PATH} not found. Run scripts/build.py first."
        )
    print("[deploy] No package.json found; running deterministic build...")
    cmd = [sys.executable, str(ROOT / "scripts" / "build.py"), "--manifest", str(manifest_path)]
    _run_or_die(cmd, env=None)


# ------------------------------ SDK detection -------------------------------


def _sdk_cli_available() -> bool:
    """
    We use the SDK's CLI to avoid duplicating logic here.
    """
    try:
        # `python -m omni_sdk.cli.deploy --help` should succeed
        test_cmd = [sys.executable, "-m", "omni_sdk.cli.deploy", "--help"]
        subprocess.run(
            test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True
        )
        return True
    except Exception:
        return False


def _run_or_die(cmd, env: Optional[Dict[str, str]]) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"[deploy] Command failed (rc={proc.returncode}): {' '.join(cmd)}")
    return proc


# ------------------------------ deploy --------------------------------------


def run_deploy(manifest_path: Path, rpc_url: str, chain_id: int, mnemonic: str, account_index: int, wait: bool) -> Dict:
    """
    Invoke the SDK CLI deploy command. We pass config via flags where possible,
    and also provide standard environment variables for compatibility.
    """
    if not _sdk_cli_available():
        raise SystemExit(
            "[deploy] omni_sdk CLI not found. Ensure the Python SDK is available in your environment."
        )

    env = os.environ.copy()
    env["RPC_URL"] = rpc_url
    env["CHAIN_ID"] = str(chain_id)
    env["DEPLOYER_MNEMONIC"] = mnemonic

    cmd = [
        sys.executable,
        "-m",
        "omni_sdk.cli.deploy",
        "--manifest",
        str(manifest_path),
        "--account-index",
        str(account_index),
    ]
    if wait:
        cmd.append("--wait")  # SDK CLI usually supports waiting for receipt

    print(f"[deploy] Deploying with RPC={rpc_url} chainId={chain_id} account_index={account_index}")
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Show CLI output verbatim for transparency
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0:
        if proc.stderr.strip():
            sys.stderr.write(proc.stderr)
        raise SystemExit(f"[deploy] Deploy failed (rc={proc.returncode}).")

    # Try to parse a JSON object from the CLI output (best-effort)
    deploy_info = _extract_json(proc.stdout) or {
        "message": "Deploy completed. See CLI output above.",
    }
    return deploy_info


def _extract_json(text: str) -> Optional[Dict]:
    """
    Best-effort: find the last JSON object in stdout and parse it.
    """
    # Heuristic: the SDK CLI typically prints a compact JSON at the end.
    candidates = re.findall(r"(\{.*\})", text, flags=re.DOTALL)
    for raw in reversed(candidates):
        try:
            return json.loads(raw)
        except Exception:
            continue
    return None


def write_artifact(deploy_info: Dict, rpc_url: str, chain_id: int) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "rpc_url": rpc_url,
        "chain_id": chain_id,
        "result": deploy_info,
    }
    DEPLOY_ARTIFACT.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"[deploy] Wrote deploy artifact: {DEPLOY_ARTIFACT}")


# ------------------------------ main ----------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy the contract from this template.")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Path to contract manifest.json")
    ap.add_argument("--rpc", type=str, default=None, help="RPC URL (overrides .env RPC_URL)")
    ap.add_argument("--chain", type=int, default=None, help="Chain ID (overrides .env CHAIN_ID)")
    ap.add_argument("--mnemonic", type=str, default=None, help="Deployer mnemonic (overrides .env DEPLOYER_MNEMONIC)")
    ap.add_argument("--account-index", type=int, default=0, help="Account index derived from mnemonic (default: 0)")
    ap.add_argument("--no-build", action="store_true", help="Skip building if package.json missing (error if absent)")
    ap.add_argument("--no-wait", action="store_true", help="Do not wait for receipt (fire-and-forget)")
    args = ap.parse_args()

    if not args.manifest.is_file():
        raise SystemExit(f"[deploy] manifest not found: {args.manifest}")

    rpc_url, chain_id, mnemonic = resolve_config(args.rpc, args.chain, args.mnemonic)

    ensure_built(args.manifest, no_build=args.no_build)

    deploy_info = run_deploy(
        manifest_path=args.manifest,
        rpc_url=rpc_url,
        chain_id=chain_id,
        mnemonic=mnemonic,
        account_index=args.account_index,
        wait=(not args.no_wait),
    )
    write_artifact(deploy_info, rpc_url, chain_id)
    print("[deploy] Done ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
