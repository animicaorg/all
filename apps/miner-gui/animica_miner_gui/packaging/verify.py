"""Packaged build verification helpers."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional

from animica_miner_gui.backend.rpc_client import RPCClient
from animica_miner_gui.ide.toolchain.builder import build_contract
from animica_miner_gui.ide.toolchain.simulator import simulate_call, simulate_tx


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify packaged GUI build")
    parser.add_argument("--resources", type=Path, default=None, help="Override resources dir")
    args = parser.parse_args(list(argv) if argv is not None else None)

    resources_dir = _resolve_resources_dir(args.resources)
    ok = True

    if not _check_imports():
        ok = False
    if not _check_build_and_simulate():
        ok = False
    if not _check_node_rpc(resources_dir):
        ok = False

    return 0 if ok else 1


def _resolve_resources_dir(override: Optional[Path]) -> Path:
    if override:
        return override
    env_override = os.environ.get("ANIMICA_RESOURCES_DIR")
    if env_override:
        return Path(env_override)
    from animica_miner_gui.backend.app_paths import get_resources_dir

    return get_resources_dir()


def _check_imports() -> bool:
    modules = [
        "animica_miner_gui.ide",
        "animica_miner_gui.ide.toolchain.builder",
        "animica_miner_gui.ide.toolchain.simulator",
        "vm_py",
        "vm_py.compiler",
        "omni_sdk",
        "omni_sdk.contracts.deployer",
        "jsonschema",
    ]
    ok = True
    for name in modules:
        try:
            __import__(name)
        except Exception as exc:
            print(f"[verify] Missing module {name}: {exc}")
            ok = False
    return ok


def _check_build_and_simulate() -> bool:
    ok = True
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        (workspace / "contract.py").write_text(_contract_template(), encoding="utf-8")
        (workspace / "manifest.json").write_text(_manifest_template(), encoding="utf-8")

        build_result = build_contract(workspace)
        if not build_result.success:
            print(f"[verify] Build failed: {build_result.message}")
            ok = False
        manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
        manifest["source"] = str(workspace / "contract.py")
        manifest["abi"] = manifest.get("abi")

        sim_call = simulate_call(manifest, "get", {})
        if not sim_call.ok:
            print(f"[verify] Simulation call failed: {sim_call.message}")
            ok = False
        sim_tx = simulate_tx(manifest, "increment", {}, {})
        if not sim_tx.ok:
            print(f"[verify] Simulation tx failed: {sim_tx.message}")
            ok = False
    return ok


def _check_node_rpc(resources_dir: Path) -> bool:
    node_exe = resources_dir / "node" / "animica-node" / "animica-node"
    if not node_exe.exists():
        print(f"[verify] Node executable missing: {node_exe}")
        return False
    if not os.access(node_exe, os.X_OK):
        print(f"[verify] Node executable is not executable: {node_exe}")
        return False

    try:
        subprocess.run([str(node_exe), "--preflight-imports"], check=True)
    except Exception as exc:
        print(f"[verify] Node preflight imports failed: {exc}")
        return False

    data_dir = Path(tempfile.mkdtemp(prefix="animica-node-data-"))
    logs_dir = Path(tempfile.mkdtemp(prefix="animica-node-logs-"))
    token = os.urandom(16).hex()
    port = _pick_port(8545)
    rpc_url = f"http://127.0.0.1:{port}/rpc"

    env = os.environ.copy()
    env["ANIMICA_RPC_PORT"] = str(port)
    env["ANIMICA_RPC_ADMIN_TOKEN"] = token
    env["ANIMICA_DATA_DIR"] = str(data_dir)
    env["ANIMICA_LOGS_DIR"] = str(logs_dir)

    proc = subprocess.Popen([str(node_exe)], cwd=str(data_dir), env=env)
    client = RPCClient(rpc_url, token=token)
    ok = _wait_for_rpc(client)
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    if not ok:
        print("[verify] Node RPC did not respond to rpc.methods")
    return ok


def _wait_for_rpc(client: RPCClient, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            methods = client.get_rpc_methods()
            if methods is not None:
                return True
        except Exception:
            time.sleep(1)
    return False


def _pick_port(preferred: int) -> int:
    if not _port_in_use(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _contract_template() -> str:
    return (
        "from __future__ import annotations\n"
        "from stdlib import abi, storage\n\n"
        "KEY_COUNTER = b\"counter\"\n\n"
        "def _get_counter() -> int:\n"
        "    data = storage.get(KEY_COUNTER)\n"
        "    if data is None:\n"
        "        return 0\n"
        "    return abi.decode_int(data)\n\n"
        "def get() -> int:\n"
        "    return _get_counter()\n\n"
        "def increment() -> int:\n"
        "    value = _get_counter() + 1\n"
        "    storage.set(KEY_COUNTER, abi.encode_int(value))\n"
        "    return value\n"
    )


def _manifest_template() -> str:
    manifest = {
        "name": "VerifyCounter",
        "version": "0.1.0",
        "language": "python",
        "source": "contract.py",
        "abi": {
            "abiVersion": 1,
            "name": "VerifyCounter",
            "functions": [
                {
                    "name": "get",
                    "inputs": [],
                    "outputs": [{"name": "value", "type": "int"}],
                    "stateMutability": "view",
                },
                {
                    "name": "increment",
                    "inputs": [],
                    "outputs": [{"name": "value", "type": "int"}],
                    "stateMutability": "nonpayable",
                },
            ],
        },
    }
    return json.dumps(manifest, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
