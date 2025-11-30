"""
Useful work demo pipeline:
  1) Launch the lightweight devnet JSON-RPC shim (aicf.node).
  2) Mine a block and fetch it over RPC.
  3) Call the Python VM contract with block data using omni-vm-run.

Run from repo root:
    python -m vm_py.examples.useful_work_demo
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

from urllib import error, request

DEFAULT_MANIFEST = Path(__file__).resolve().parent / "useful_work" / "manifest.json"


def rpc_call(url: str, method: str, params: list[Any] | None = None) -> Dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    data = json.dumps(payload).encode()
    req = request.Request(url, data=data, headers={"content-type": "application/json"})
    try:
        with request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
    except error.URLError as exc:  # pragma: no cover - network shim failure
        raise RuntimeError(exc) from exc

    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def wait_for_rpc(url: str, attempts: int = 30) -> None:
    for _ in range(attempts):
        try:
            rpc_call(url, "web3_clientVersion")
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("RPC shim did not start in time")


def launch_devnet(rpc_port: int, datadir: Path) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "aicf.node",
        "--network",
        "devnet",
        "--rpc-addr",
        "127.0.0.1",
        "--rpc-port",
        str(rpc_port),
        "--datadir",
        str(datadir),
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def build_args_from_block(block: Dict[str, Any]) -> str:
    height = int(block["number"], 16)
    timestamp = int(block["timestamp"], 16)
    difficulty_hex = block.get("difficulty", "0x0")
    difficulty = int(difficulty_hex, 16) if isinstance(difficulty_hex, str) else int(difficulty_hex)
    miner = block.get("miner", "0x" + "0" * 40)
    args = [block.get("hash"), height, timestamp, difficulty, miner]
    return json.dumps(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a useful-work demo against the devnet shim")
    parser.add_argument("--rpc-port", type=int, default=18545)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)

    rpc_url = f"http://127.0.0.1:{args.rpc_port}/"
    with tempfile.TemporaryDirectory(prefix="animica-devnet-") as tmp:
        node = launch_devnet(args.rpc_port, Path(tmp))
        try:
            wait_for_rpc(rpc_url)
            mined = rpc_call(rpc_url, "animica_generate", [1])
            print(f"[demo] mined block: {mined}")

            block = rpc_call(rpc_url, "eth_getBlockByNumber", ["latest", False])
            print(f"[demo] fetched block hash={block.get('hash')} height={block.get('number')}\n")

            arg_json = build_args_from_block(block)
            run_cmd = [
                sys.executable,
                "-m",
                "vm_py.cli.run",
                "--manifest",
                os.fspath(args.manifest),
                "--call",
                "score_block",
                "--args",
                arg_json,
            ]
            print(f"[demo] invoking omni-vm-run: {' '.join(run_cmd)}\n")
            proc = subprocess.run(run_cmd, check=True, capture_output=True, text=True)
            print(proc.stdout)
            return 0
        finally:
            node.terminate()
            try:
                node.wait(timeout=2)
            except subprocess.TimeoutExpired:
                node.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
