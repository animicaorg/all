# -*- coding: utf-8 -*-
"""
Deploy the ai_agent example contract using the repo's deployment tooling and perform a few
sanity checks against the node RPC.

This script is intentionally self-contained and resilient:
- It first tries to import and call the internal deploy tool (contracts/tools/deploy.py).
- If that import path isn't available, it falls back to running the deploy tool as a subprocess.
- It parses either JSON or human output to extract the deployed address.
- It then pings the RPC (chain.getHead) and tries a couple of light post-deploy checks:
  * state.getBalance for the new contract address (should be 0 on fresh deploys).
  * chain.getBlockByNumber("latest") presence sanity.

Environment variables (overridable via CLI flags):
  RPC_URL     (default: http://127.0.0.1:8545)
  CHAIN_ID    (default: 1337)
  MNEMONIC    (12/24-word test mnemonic; if absent the deploy tool may look for keystore)
  ALG         (default: dilithium3) — post-quantum signer algorithm (if the deploy tool supports it)

Examples:
  python -m contracts.examples.ai_agent.deploy_and_test
  python -m contracts.examples.ai_agent.deploy_and_test --rpc http://localhost:8545 --chain-id 1337
  RPC_URL=http://localhost:8545 CHAIN_ID=1337 python contracts/examples/ai_agent/deploy_and_test.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---- paths -------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # contracts/examples/ai_agent → contracts → repo root
TOOLS_DIR = REPO_ROOT / "contracts" / "tools"
DEPLOY_TOOL = TOOLS_DIR / "deploy.py"
MANIFEST_JSON = HERE / "manifest.json"
CONTRACT_PY = HERE / "contract.py"


# ---- small JSON-RPC helper (no external deps) --------------------------------


def jsonrpc_call(
    rpc_url: str, method: str, params: Any, *, timeout: float = 10.0
) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    data = json.dumps(payload).encode("utf-8")
    req = Request(rpc_url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            obj = json.loads(body)
            if "error" in obj and obj["error"]:
                raise RuntimeError(f"RPC error for {method}: {obj['error']}")
            return obj.get("result")
    except HTTPError as e:
        raise RuntimeError(f"HTTP error calling {method}: {e.code} {e.reason}") from e
    except URLError as e:
        raise RuntimeError(f"Network error calling {method}: {e.reason}") from e


# ---- deploy tool integration --------------------------------------------------


def _call_deploy_tool_import(
    rpc_url: str,
    chain_id: int,
    mnemonic: Optional[str],
    alg: Optional[str],
    manifest_path: Path,
    source_path: Path,
) -> Tuple[str, Dict[str, Any]]:
    """
    Try to import contracts.tools.deploy and invoke a likely function signature.
    Returns (address, extra_info_dict).
    """
    sys.path.insert(0, str(REPO_ROOT))  # ensure repo is importable
    try:
        from contracts.tools import deploy as deploy_mod  # type: ignore
    except Exception as exc:
        raise ImportError(f"Could not import internal deploy tool: {exc}")

    # Probe function candidates in a robust order
    candidates = [
        ("deploy_from_files", dict),
        ("deploy_package", dict),
        ("deploy", dict),
        ("main", dict),  # some tools expose a main(...) returning dict
    ]

    last_err: Optional[Exception] = None
    for fname, _ in candidates:
        fn = getattr(deploy_mod, fname, None)
        if not callable(fn):
            continue
        try:
            # Try common kwarg shapes; tolerate tools that ignore unknown kwargs.
            res = fn(  # type: ignore[misc]
                manifest_path=str(manifest_path),
                source_path=str(source_path),
                rpc_url=rpc_url,
                chain_id=chain_id,
                mnemonic=mnemonic,
                alg=alg,
                json_output=True,  # many tools honor this to return dicts / structured output
            )
            if isinstance(res, dict):
                addr = (
                    res.get("address")
                    or res.get("contract_address")
                    or res.get("deployed_address")
                )
                if not addr:
                    # Some tools return (address, meta)
                    if "result" in res and isinstance(res["result"], dict):
                        addr = res["result"].get("address")
                if not addr and "stdout" in res and isinstance(res["stdout"], str):
                    addr = _extract_address_text(res["stdout"])
                if not addr:
                    raise RuntimeError(f"Deploy tool result missing address: {res}")
                return str(addr), res
            # If the tool returns a tuple (address, meta)
            if isinstance(res, tuple) and res and isinstance(res[0], str):
                return res[0], (
                    res[1] if len(res) > 1 and isinstance(res[1], dict) else {}
                )
            # If returns a plain address string
            if isinstance(res, str):
                return res, {}
        except Exception as exc:
            last_err = exc
            continue

    if last_err:
        raise RuntimeError(f"All deploy-tool call patterns failed: {last_err}")
    raise RuntimeError("No usable function found in contracts.tools.deploy")


def _extract_address_text(text: str) -> Optional[str]:
    """
    Parse an address from arbitrary CLI output.
    Supports both 0x… hex and bech32m anim1… addresses.
    """
    # Bech32m (anim1…)
    m = re.search(r"\b(anim1[0-9a-z]{20,})\b", text)
    if m:
        return m.group(1)
    # Hex 20-byte
    m = re.search(r"\b0x[a-fA-F0-9]{40}\b", text)
    if m:
        return m.group(0)
    # Keyed forms
    m = re.search(
        r"(?:address|deployed(?:_at)?|contract):\s*(anim1[0-9a-z]{20,}|0x[a-fA-F0-9]{40})",
        text,
        re.I,
    )
    if m:
        return m.group(1)
    return None


def _call_deploy_tool_subprocess(
    rpc_url: str,
    chain_id: int,
    mnemonic: Optional[str],
    alg: Optional[str],
    manifest_path: Path,
    source_path: Path,
) -> Tuple[str, Dict[str, Any]]:
    """
    Run the deploy tool as a subprocess and parse its output.
    """
    if not DEPLOY_TOOL.is_file():
        raise FileNotFoundError(f"Deploy tool not found at {DEPLOY_TOOL}")

    cmd = [
        sys.executable,
        str(DEPLOY_TOOL),
        "--manifest",
        str(manifest_path),
        "--source",
        str(source_path),
        "--rpc",
        rpc_url,
        "--chain-id",
        str(chain_id),
        "--json",  # if tool supports this, we'll get structured output
    ]
    if mnemonic:
        cmd += ["--mnemonic", mnemonic]
    if alg:
        cmd += ["--alg", alg]

    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    out = proc.stdout.strip()
    err = proc.stderr.strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Deploy tool failed (exit {proc.returncode}).\nSTDERR:\n{err}\nSTDOUT:\n{out}"
        )

    # Try JSON first
    try:
        obj = json.loads(out)
        addr = (
            obj.get("address")
            or obj.get("contract_address")
            or obj.get("deployed_address")
        )
        if not addr:
            # Some tools nest result
            if "result" in obj and isinstance(obj["result"], dict):
                addr = obj["result"].get("address")
        if addr:
            return str(addr), obj
    except Exception:
        pass

    # Fall back to regex parsing
    addr = _extract_address_text(out) or _extract_address_text(err)
    if not addr:
        raise RuntimeError(
            f"Could not parse deployed address from output.\nSTDOUT:\n{out}\n---\nSTDERR:\n{err}"
        )
    return addr, {"stdout": out, "stderr": err}


# ---- post-deploy checks ------------------------------------------------------


def _post_deploy_checks(rpc_url: str, chain_id: int, address: str) -> Dict[str, Any]:
    """
    Perform a couple of light sanity checks against the node.
    Returns a dict of facts to print as JSON.
    """
    head = jsonrpc_call(rpc_url, "chain.getHead", [])
    # Optional chainId sanity if method exists
    try:
        rid = jsonrpc_call(rpc_url, "chain.getChainId", [])
    except Exception:
        rid = None

    # Balance may be zero for contracts; still good to exercise the path
    balance = None
    try:
        balance = jsonrpc_call(rpc_url, "state.getBalance", [address])
    except Exception:
        pass

    latest = None
    try:
        latest = jsonrpc_call(
            rpc_url, "chain.getBlockByNumber", ["latest", False, False]
        )
    except Exception:
        pass

    return {
        "head": head,
        "rpc_chain_id": rid,
        "expected_chain_id": chain_id,
        "contract_address": address,
        "balance": balance,
        "latest_block": (
            latest["header"]["number"]
            if isinstance(latest, dict)
            and "header" in latest
            and "number" in latest["header"]
            else None
        ),
    }


# ---- main --------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy ai_agent example and run quick checks."
    )
    parser.add_argument(
        "--rpc",
        dest="rpc_url",
        default=os.getenv("RPC_URL", "http://127.0.0.1:8545"),
        help="Node RPC URL (default: %(default)s or RPC_URL env)",
    )
    parser.add_argument(
        "--chain-id",
        dest="chain_id",
        type=int,
        default=int(os.getenv("CHAIN_ID", "1337")),
        help="Chain ID (default: %(default)s or CHAIN_ID env)",
    )
    parser.add_argument(
        "--mnemonic",
        dest="mnemonic",
        default=os.getenv("MNEMONIC"),
        help="Deployer mnemonic (env MNEMONIC). If omitted, tool may use a keystore or fail.",
    )
    parser.add_argument(
        "--alg",
        dest="alg",
        default=os.getenv("ALG", "dilithium3"),
        help="PQ signer algorithm (default: dilithium3)",
    )
    parser.add_argument(
        "--manifest",
        dest="manifest",
        default=str(MANIFEST_JSON),
        help="Path to manifest.json (default: examples/ai_agent/manifest.json)",
    )
    parser.add_argument(
        "--source",
        dest="source",
        default=str(CONTRACT_PY),
        help="Path to contract.py (default: examples/ai_agent/contract.py)",
    )
    parser.add_argument(
        "--no-import",
        action="store_true",
        help="Skip import-path deploy and force subprocess execution of the deploy tool",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Emit machine-readable JSON summary to stdout",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    source_path = Path(args.source).resolve()
    if not manifest_path.is_file():
        print(f"ERROR: manifest file not found: {manifest_path}", file=sys.stderr)
        sys.exit(2)
    if not source_path.is_file():
        print(f"ERROR: contract source not found: {source_path}", file=sys.stderr)
        sys.exit(2)

    # Deploy
    address: Optional[str] = None
    meta: Dict[str, Any] = {}
    try:
        if args.no_import:
            raise ImportError("forced subprocess path")
        address, meta = _call_deploy_tool_import(
            rpc_url=args.rpc_url,
            chain_id=args.chain_id,
            mnemonic=args.mnemonic,
            alg=args.alg,
            manifest_path=manifest_path,
            source_path=source_path,
        )
    except Exception:
        # Fallback to subprocess
        address, meta = _call_deploy_tool_subprocess(
            rpc_url=args.rpc_url,
            chain_id=args.chain_id,
            mnemonic=args.mnemonic,
            alg=args.alg,
            manifest_path=manifest_path,
            source_path=source_path,
        )

    # Basic sanity delay (allow the tx to land)
    time.sleep(0.5)

    checks = {}
    try:
        checks = _post_deploy_checks(args.rpc_url, args.chain_id, address)
    except Exception as exc:
        checks = {
            "error": f"post-deploy checks failed: {exc}",
            "contract_address": address,
        }

    result = {
        "ok": True,
        "address": address,
        "rpc_url": args.rpc_url,
        "chain_id": args.chain_id,
        "tool_meta": meta,
        "checks": checks,
    }

    if args.print_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("\n✅ Deployed ai_agent contract")
        print(f"  Address: {address}")
        print(f"  RPC URL: {args.rpc_url}")
        if checks:
            head_h = (
                checks.get("head", {}).get("height")
                if isinstance(checks.get("head"), dict)
                else checks.get("head")
            )
            print(f"  Head: {head_h}")
            if checks.get("balance") is not None:
                print(f"  Balance: {checks['balance']}")
        # Friendly hints
        print("\nTry calling a simple view via RPC after a block or two:")
        print(
            f"  curl -s {args.rpc_url} -H 'content-type: application/json' "
            f'-d \'{{"jsonrpc":"2.0","id":1,'
            f'"method":"state.getBalance","params":["{address}"]}}\' | jq'
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
