# -*- coding: utf-8 -*-
"""
Deploy & smoke-test the Quantum RNG example contract on a running devnet/testnet.

What this script does:
  1) Compiles & packages the contract (using contracts.tools.build_package).
  2) Deploys it using the Python SDK (or the contracts.tools.deploy helper).
  3) Calls `request(bits, shots, trap_rate)` to enqueue a quantum job.
  4) Polls `poll(task_id)` until the result is available (bounded by a timeout).
  5) Confirms `last()` matches the result of the latest fulfilled task.
  6) Prints a short, human-friendly summary for CI logs.

Requirements:
  - Node RPC reachable (HTTP for JSON-RPC, WS optional).
  - PQ signing dependencies available (our SDK handles keys/signing).
  - AICF components running (so quantum jobs complete on-chain) *or*
    a devnet that resolves quantum jobs in the next blocks.

Configuration (env or CLI flags):
  RPC_URL            default http://127.0.0.1:8545
  CHAIN_ID           default 1337
  DEPLOYER_MNEMONIC  (required if not using a local unlocked account)
  GAS_PRICE_WEI      optional override (integer)
  TIMEOUT_SECS       default 120
  POLL_INTERVAL_SECS default 2.0

Usage examples:
  python -m contracts.examples.quantum_rng.deploy_and_test
  RPC_URL=http://localhost:8545 CHAIN_ID=1337 DEPLOYER_MNEMONIC="..." \
    python contracts/examples/quantum_rng/deploy_and_test.py --bits 32 --shots 8 --trap-rate 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

# --- Paths -------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3] if len(HERE.parents) >= 3 else Path.cwd()
MANIFEST = HERE / "manifest.json"
SOURCE = HERE / "contract.py"
BUILD_DIR = REPO / "contracts" / "build"

# --- Small utilities ---------------------------------------------------------


def _read_env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _read_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except Exception:
        return default


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class Config:
    rpc_url: str
    chain_id: int
    mnemonic: Optional[str]
    gas_price_wei: Optional[int]
    timeout_secs: int
    poll_interval: float
    bits: int
    shots: int
    trap_rate: int


def parse_args_env() -> Config:
    p = argparse.ArgumentParser(
        description="Deploy & test quantum_rng example contract."
    )
    p.add_argument("--rpc-url", default=os.getenv("RPC_URL", "http://127.0.0.1:8545"))
    p.add_argument("--chain-id", type=int, default=_read_env_int("CHAIN_ID", 1337))
    p.add_argument("--mnemonic", default=os.getenv("DEPLOYER_MNEMONIC"))
    p.add_argument(
        "--gas-price-wei", type=int, default=os.getenv("GAS_PRICE_WEI") or None
    )
    p.add_argument(
        "--timeout-secs", type=int, default=_read_env_int("TIMEOUT_SECS", 120)
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("POLL_INTERVAL_SECS") or 2.0),
    )
    p.add_argument(
        "--bits", type=int, default=32, help="Requested random bits (multiple of 8)."
    )
    p.add_argument(
        "--shots", type=int, default=8, help="Quantum shots (provider-specific)."
    )
    p.add_argument(
        "--trap-rate", type=int, default=10, help="Trap-circuit percent (0..100)."
    )
    args = p.parse_args()

    return Config(
        rpc_url=args.rpc_url,
        chain_id=args.chain_id,
        mnemonic=args.mnemonic,
        gas_price_wei=args.gas_price_wei,
        timeout_secs=args.timeout_secs,
        poll_interval=args.poll_interval,
        bits=args.bits,
        shots=args.shots,
        trap_rate=args.trap_rate,
    )


# --- Build & deploy helpers --------------------------------------------------


def build_package(contract_dir: Path, out_dir: Path) -> Path:
    """
    Build a deployable package using our local toolchain. Returns the package path.
    """
    try:
        from contracts.tools.build_package import \
            build as build_func  # our tool exposes build()
    except Exception:
        # Back-compat: earlier version exported build_package()
        try:
            from contracts.tools.build_package import \
                build_package as build_func  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"contracts.tools.build_package not available: {exc}")

    out_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = build_func(contract_dir=str(contract_dir), out_dir=str(out_dir))
    return Path(pkg_path)


def deploy_package_via_tool(
    pkg_path: Path,
    rpc_url: str,
    chain_id: int,
    mnemonic: Optional[str],
    gas_price_wei: Optional[int],
) -> str:
    """
    Prefer the contracts.tools.deploy helper if available (handles signer & send).
    """
    try:
        from contracts.tools.deploy import deploy_package as deploy_func
    except Exception as exc:
        raise RuntimeError(f"contracts.tools.deploy not available: {exc}")

    addr = deploy_func(
        package_path=str(pkg_path),
        rpc_url=rpc_url,
        chain_id=chain_id,
        mnemonic=mnemonic,
        gas_price_wei=gas_price_wei,
    )
    return str(addr)


def _load_manifest_abi(manifest_path: Path) -> Any:
    m = _load_json(manifest_path)
    if "abi" in m and m["abi"]:
        return m["abi"]
    # Allow manifest to reference an ABI file
    abi_ref = m.get("abi_path") or m.get("abiFile") or None
    if abi_ref:
        ap = (manifest_path.parent / abi_ref).resolve()
        return _load_json(ap)
    raise RuntimeError(
        "Manifest missing ABI; expected 'abi' inline or 'abi_path' reference"
    )


# --- Client helpers ----------------------------------------------------------


class Rpc:
    """Tiny JSON-RPC helper; used only for WS subscribe fallback if SDK WS absent."""

    def __init__(self, url: str):
        self.url = url


# --- Contract client via SDK -------------------------------------------------


class ContractClient:
    """
    Lightweight wrapper around the Python SDK's contract client. We intentionally
    import lazily and keep the surface tiny for robustness across minor API shifts.
    """

    def __init__(self, rpc_url: str, chain_id: int, address: str, abi: Any):
        from omni_sdk.contracts.client import \
            ContractClient as SdkContractClient  # type: ignore
        from omni_sdk.rpc.http import HttpClient  # type: ignore

        self.http = HttpClient(rpc_url)
        self.client = SdkContractClient(
            self.http, address=address, abi=abi, chain_id=chain_id
        )

    def call(self, fn: str, *args) -> Any:
        return self.client.call(fn, *args)


# --- Orchestration -----------------------------------------------------------


def wait_for_poll_ready(
    client: ContractClient,
    task_id: bytes,
    want_len: int,
    timeout_secs: int,
    poll_interval: float,
) -> Tuple[bool, Optional[bytes]]:
    """
    Polls contract.poll(task_id) until it returns (True, out) or timeout.
    Returns (ready, out_or_none).
    """
    t0 = time.time()
    last_status: Tuple[bool, bytes] = (False, b"")
    while True:
        ready, out = client.call("poll", bytes(task_id))
        if not isinstance(ready, bool):
            raise AssertionError(f"poll() returned non-bool readiness: {ready!r}")
        if not isinstance(out, (bytes, bytearray)):
            raise AssertionError(f"poll() returned non-bytes output: {type(out)}")
        last_status = (ready, bytes(out))
        if ready:
            if want_len > 0 and len(out) != want_len:
                raise AssertionError(
                    f"unexpected output length: {len(out)} vs {want_len}"
                )
            return True, bytes(out)
        if time.time() - t0 > timeout_secs:
            return False, None
        time.sleep(poll_interval)


def main() -> int:
    cfg = parse_args_env()

    if not MANIFEST.is_file() or not SOURCE.is_file():
        print(
            f"[ERR] missing manifest/source at {MANIFEST} / {SOURCE}", file=sys.stderr
        )
        return 2

    print(f"[i] Building package from {HERE} → {BUILD_DIR} …")
    pkg_path = build_package(HERE, BUILD_DIR)
    print(f"[✓] Built package: {pkg_path}")

    print(f"[i] Deploying to chain {cfg.chain_id} via {cfg.rpc_url} …")
    try:
        address = deploy_package_via_tool(
            pkg_path, cfg.rpc_url, cfg.chain_id, cfg.mnemonic, cfg.gas_price_wei
        )
    except Exception as exc:
        print(f"[ERR] deploy failed: {exc}", file=sys.stderr)
        return 3
    print(f"[✓] Deployed contract at {address}")

    abi = _load_manifest_abi(MANIFEST)
    c = ContractClient(cfg.rpc_url, cfg.chain_id, address, abi)

    if cfg.bits % 8 != 0 or cfg.bits <= 0:
        print(
            f"[ERR] bits must be a positive multiple of 8 (got {cfg.bits})",
            file=sys.stderr,
        )
        return 4

    print(
        f"[i] request(bits={cfg.bits}, shots={cfg.shots}, trap_rate={cfg.trap_rate}) …"
    )
    try:
        task_id = c.call("request", int(cfg.bits), int(cfg.shots), int(cfg.trap_rate))
    except Exception as exc:
        print(f"[ERR] request() reverted/failed: {exc}", file=sys.stderr)
        return 5
    if not isinstance(task_id, (bytes, bytearray)) or len(task_id) == 0:
        print(f"[ERR] unexpected task_id: {task_id!r}", file=sys.stderr)
        return 6
    print(f"[✓] task_id = 0x{bytes(task_id).hex()}")

    want_len = cfg.bits // 8
    print(
        f"[i] Polling for result (timeout={cfg.timeout_secs}s, every {cfg.poll_interval}s) …"
    )
    ready, out = wait_for_poll_ready(
        c, bytes(task_id), want_len, cfg.timeout_secs, cfg.poll_interval
    )

    if not ready:
        # Not necessarily a failure in early devnets; print status and exit non-zero to flag CI.
        print(
            f"[!] Result not ready within {cfg.timeout_secs}s. The job may settle in later blocks.",
            file=sys.stderr,
        )
        return 7

    assert out is not None and len(out) == want_len
    print(f"[✓] Received mixed bytes ({len(out)} bytes): 0x{out.hex()}")

    # Verify last() mirrors the latest fulfilled output
    last = c.call("last")
    if not isinstance(last, (bytes, bytearray)):
        print(f"[ERR] last() returned non-bytes: {type(last)}", file=sys.stderr)
        return 8
    if bytes(last) != bytes(out):
        print(
            f"[ERR] last() mismatch:\n  last=0x{bytes(last).hex()}\n  out =0x{bytes(out).hex()}",
            file=sys.stderr,
        )
        return 9

    print("[✓] last() matches the fulfilled result")
    print("\n=== SUMMARY ===")
    print(f"address:    {address}")
    print(f"task_id:    0x{bytes(task_id).hex()}")
    print(f"output[{len(out)}]: 0x{out.hex()}")
    print("status:     OK")

    return 0


if __name__ == "__main__":
    sys.exit(main())
