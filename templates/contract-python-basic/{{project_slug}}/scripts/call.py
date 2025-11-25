#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Call a contract function (read or write) against an Animica node.

Features
- Reads config from CLI flags or .env (RPC_URL, CHAIN_ID, DEPLOYER_MNEMONIC)
- Auto-finds the last deployed address from ./build/deploy_result.json if --address not given
- Accepts JSON args (positional list or keyword object)
- For write calls, signs with DEPLOYER_MNEMONIC (or --mnemonic) and can wait for receipt
- Uses the Python SDK CLI under the hood to stay aligned with RPC/ABI rules

Examples
  # Read-only call (e.g., "get"):
  python scripts/call.py --fn get

  # Read-only with explicit address and args:
  python scripts/call.py --address anim1... --fn balanceOf --args-json '["anim1xyz..."]'

  # State-changing write (e.g., "inc" or "transfer"), wait for receipt:
  python scripts/call.py --fn inc --write --wait

  # Provide explicit config:
  python scripts/call.py \
    --manifest contracts/manifest.json \
    --address anim1... \
    --fn transfer \
    --args-json '["anim1abc...", "1000"]' \
    --write --rpc http://127.0.0.1:8545 --chain 1337 --mnemonic "word1 ... word12"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

ROOT = Path(__file__).resolve().parents[2]  # {{project_slug}}/
DEFAULT_MANIFEST = ROOT / "contracts" / "manifest.json"
BUILD_DIR = ROOT / "build"
DEPLOY_ARTIFACT = BUILD_DIR / "deploy_result.json"
CALL_ARTIFACT = BUILD_DIR / "call_last.json"


# ------------------------------ .env loader ---------------------------------


def load_env_file(dotenv_path: Path) -> Dict[str, str]:
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
    require_mnemonic: bool,
) -> Tuple[str, int, str]:
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
        raise SystemExit(f"[call] Invalid CHAIN_ID: {chain_str!r}")

    mnemonic = (
        mnemonic_flag
        or os.environ.get("DEPLOYER_MNEMONIC")
        or file_env.get("DEPLOYER_MNEMONIC")
        or ""
    )
    if require_mnemonic and not mnemonic.strip():
        raise SystemExit(
            "[call] Missing DEPLOYER_MNEMONIC. Provide via --mnemonic or set in .env for write calls."
        )

    return rpc, chain_id, mnemonic


# ------------------------------ helpers -------------------------------------


def _sdk_cli_available() -> bool:
    try:
        test_cmd = [sys.executable, "-m", "omni_sdk.cli.call", "--help"]
        subprocess.run(test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
        return True
    except Exception:
        return False


def _run_or_die(cmd, env: Optional[Dict[str, str]]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        if proc.stderr.strip():
            sys.stderr.write(proc.stderr)
        raise SystemExit(f"[call] Command failed (rc={proc.returncode}): {' '.join(cmd)}")
    return proc


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    candidates = re.findall(r"(\{.*\})", text, flags=re.DOTALL)
    for raw in reversed(candidates):
        try:
            return json.loads(raw)
        except Exception:
            continue
    return None


def load_last_deployed_address() -> Optional[str]:
    if not DEPLOY_ARTIFACT.is_file():
        return None
    try:
        data = json.loads(DEPLOY_ARTIFACT.read_text(encoding="utf-8"))
        # Common shapes: {"result":{"address":"anim1..."}}
        # or {"address":"anim1..."}
        if isinstance(data, dict):
            if "result" in data and isinstance(data["result"], dict) and "address" in data["result"]:
                return data["result"]["address"]
            if "address" in data:
                return data["address"]
    except Exception:
        pass
    return None


def parse_args_json(s: Optional[str], file: Optional[Path]) -> Optional[str]:
    """
    Normalize user-provided JSON into a compact string that the SDK CLI accepts.
    Returns a JSON string or None.
    """
    if file:
        if not file.is_file():
            raise SystemExit(f"[call] --args-file not found: {file}")
        s = file.read_text(encoding="utf-8")
    if s is None:
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise SystemExit(f"[call] --args-json is not valid JSON: {e}")
    # Allow either list (positional) or object (keyword). Re-serialize compactly.
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


# ------------------------------ main flow -----------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Call a contract function using the Animica Python SDK.")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Path to contract manifest.json")
    ap.add_argument("--address", type=str, default=None, help="Target contract address (bech32m anim1...)")
    ap.add_argument("--fn", type=str, required=True, help="Function name in ABI")
    ap.add_argument("--args-json", type=str, default=None, help="JSON of args (array for positional, object for keyword)")
    ap.add_argument("--args-file", type=Path, default=None, help="Read args JSON from file")
    ap.add_argument("--write", action="store_true", help="Perform a state-changing call (sends a transaction)")
    ap.add_argument("--value", type=str, default=None, help="Optional value to send (as decimal string)")
    ap.add_argument("--wait", action="store_true", help="Wait for receipt on write calls")
    ap.add_argument("--rpc", type=str, default=None, help="RPC URL (overrides .env RPC_URL)")
    ap.add_argument("--chain", type=int, default=None, help="Chain ID (overrides .env CHAIN_ID)")
    ap.add_argument("--mnemonic", type=str, default=None, help="Deployer mnemonic (needed for --write if not in .env)")
    ap.add_argument("--account-index", type=int, default=0, help="Account index derived from mnemonic (default: 0)")
    args = ap.parse_args()

    if not args.manifest.is_file():
        raise SystemExit(f"[call] manifest not found: {args.manifest}")

    target_addr = args.address or load_last_deployed_address()
    if not target_addr:
        raise SystemExit("[call] No --address provided and no build/deploy_result.json found to infer address.")

    args_json = parse_args_json(args.args_json, args.args_file)

    rpc_url, chain_id, mnemonic = resolve_config(
        args.rpc, args.chain, args.mnemonic, require_mnemonic=args.write
    )

    if not _sdk_cli_available():
        raise SystemExit(
            "[call] omni_sdk CLI not found. Ensure the Python SDK is installed/available (module omni_sdk.cli.call)."
        )

    env = os.environ.copy()
    env["RPC_URL"] = rpc_url
    env["CHAIN_ID"] = str(chain_id)
    if args.write:
        env["DEPLOYER_MNEMONIC"] = mnemonic

    cmd = [
        sys.executable,
        "-m",
        "omni_sdk.cli.call",
        "--manifest",
        str(args.manifest),
        "--address",
        target_addr,
        "--fn",
        args.fn,
        "--account-index",
        str(args.account-index if False else args.account_index),  # keep linter happy in static templates
    ]

    if args_json is not None:
        cmd += ["--args-json", args_json]
    if args.write:
        cmd.append("--write")
        if args.wait:
            cmd.append("--wait")
        if args.value is not None:
            cmd += ["--value", str(args.value)]

    print(f"[call] Calling {args.fn} on {target_addr} (RPC={rpc_url}, chainId={chain_id})")
    proc = _run_or_die(cmd, env=env)

    if proc.stdout.strip():
        print(proc.stdout.strip())

    # Persist last result (best-effort JSON extraction)
    result = _extract_json(proc.stdout) or {"message": "Call completed. See CLI output above."}
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    CALL_ARTIFACT.write_text(
        json.dumps(
            {
                "rpc_url": rpc_url,
                "chain_id": chain_id,
                "address": target_addr,
                "fn": args.fn,
                "write": bool(args.write),
                "result": result,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[call] Wrote call artifact: {CALL_ARTIFACT}")
    print("[call] Done ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
