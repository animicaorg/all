#!/usr/bin/env python3
"""
Animica SDK — Devnet helper

Bring up a minimal local devnet (RPC server + optional CPU miner) or attach to an
already-running node. Prints the effective RPC/WS endpoints and detected chainId.

Usage:
  python sdk/test-harness/devnet_env.py up [--dir .animica-devnet] [--host 127.0.0.1] [--port 8545] [--chain 1337] [--mine/--no-mine] [--threads auto]
  python sdk/test-harness/devnet_env.py down [--dir .animica-devnet]
  python sdk/test-harness/devnet_env.py status [--dir .animica-devnet]
  python sdk/test-harness/devnet_env.py attach --rpc http://127.0.0.1:8545 [--ws ws://127.0.0.1:8545/ws]

Notes:
- Uses `uvicorn rpc.server:app` to launch the node RPC (FastAPI) which also serves WS at /ws.
- Attempts to start the built-in CPU miner via `python -m mining.cli.miner` unless --no-mine is given.
- Writes PIDs and connection info under the chosen working dir (default: ./.animica-devnet).
- Avoids non-stdlib deps; HTTP JSON-RPC uses urllib.

This helper is best-effort. If your repository exposes different run commands, set RPC_URL/WS_URL
and use `attach` mode instead.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
import typing as t
from pathlib import Path
from urllib import error as urlerror
from urllib import request

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8545
DEFAULT_CHAIN_ID = 1337
DEFAULT_DIR = ".animica-devnet"

RPC_PID = "rpc.pid"
MINER_PID = "miner.pid"
INFO_JSON = "info.json"


@dataclasses.dataclass
class DevnetInfo:
    rpc_url: str
    ws_url: str
    chain_id: t.Optional[int] = None
    pids: dict[str, int] = dataclasses.field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True)

    @staticmethod
    def from_file(path: Path) -> "DevnetInfo":
        data = json.loads(path.read_text())
        return DevnetInfo(
            rpc_url=data["rpc_url"],
            ws_url=data["ws_url"],
            chain_id=data.get("chain_id"),
            pids=dict(data.get("pids") or {}),
        )


def _json_rpc_call(
    rpc_url: str, method: str, params: t.Any = None, timeout: float = 3.0
) -> t.Any:
    """Minimal JSON-RPC 2.0 call using stdlib only."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        rpc_url.rstrip("/") + "/rpc",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        if "error" in payload:
            raise RuntimeError(f"RPC error: {payload['error']}")
        return payload.get("result")


def _wait_for_ready(rpc_url: str, deadline_sec: float = 20.0) -> int:
    """Wait until chain.getChainId responds, return chainId."""
    t0 = time.time()
    last_exc: t.Optional[Exception] = None
    while time.time() - t0 < deadline_sec:
        try:
            cid = _json_rpc_call(rpc_url, "chain.getChainId")
            if isinstance(cid, int):
                return cid
        except Exception as e:  # noqa: BLE001
            last_exc = e
        time.sleep(0.25)
    raise TimeoutError(
        f"RPC did not become ready within {deadline_sec:.1f}s; last={last_exc!r}"
    )


def _spawn(
    cmd: list[str], env: dict[str, str], cwd: Path, stdout_file: Path
) -> subprocess.Popen:
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    # Open in append mode; keep logs across sessions.
    out = open(stdout_file, "ab", buffering=0)
    # On POSIX, start a new session so we can kill the whole tree later.
    preexec = os.setsid if hasattr(os, "setsid") else None
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=out,
        stderr=subprocess.STDOUT,
        preexec_fn=preexec,
        text=False,
    )


def _terminate(pid: int) -> None:
    with contextlib.suppress(Exception):
        if sys.platform != "win32":
            # Kill process group if we started a new session
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)


def _uvicorn_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("uvicorn") is not None
    except Exception:
        return False


def _write_pidfile(path: Path, pid: int) -> None:
    path.write_text(str(pid))


def _read_pidfile(path: Path) -> t.Optional[int]:
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def do_up(args: argparse.Namespace) -> int:
    work = Path(args.dir).resolve()
    work.mkdir(parents=True, exist_ok=True)

    host = args.host
    port = args.port
    ws_url = f"ws://{host}:{port}/ws"
    rpc_url = f"http://{host}:{port}"
    chain_id = args.chain

    env = os.environ.copy()
    # Provide multiple env keys so rpc/config.py can pick any it recognizes.
    env.setdefault("ANIMICA_DB_URL", f"sqlite:///{work / 'devnet.db'}")
    env.setdefault("DB_URL", env["ANIMICA_DB_URL"])
    env.setdefault("ANIMICA_CHAIN_ID", str(chain_id))
    env.setdefault("CHAIN_ID", str(chain_id))
    env.setdefault("ANIMICA_LOG_LEVEL", "INFO")

    # Prefer uvicorn; fall back to module runner if unavailable.
    if _uvicorn_available():
        rpc_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "rpc.server:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "info",
        ]
    else:
        # As a fallback, try running the module directly.
        rpc_cmd = [sys.executable, "-m", "rpc.server"]

    print(f"[devnet] launching RPC: {' '.join(rpc_cmd)}")
    rpc_proc = _spawn(rpc_cmd, env=env, cwd=work, stdout_file=work / "rpc.log")
    _write_pidfile(work / RPC_PID, rpc_proc.pid)

    # Wait for JSON-RPC to come up
    try:
        detected_chain = _wait_for_ready(rpc_url, deadline_sec=args.rpc_timeout)
    except Exception as e:  # noqa: BLE001
        print(f"[devnet] ERROR: RPC did not start properly: {e}", file=sys.stderr)
        print("[devnet] Check logs:", work / "rpc.log", file=sys.stderr)
        return 2

    if detected_chain != chain_id:
        print(
            f"[devnet] WARN: detected chainId={detected_chain} != requested {chain_id}. "
            "Proceeding anyway.",
            file=sys.stderr,
        )

    # Optionally start miner
    miner_pid: t.Optional[int] = None
    if args.mine:
        miner_cmd = [
            sys.executable,
            "-m",
            "mining.cli.miner",
            "--threads",
            str(args.threads),
            "--device",
            "cpu",
        ]
        # Miner might expect env RPC URL; provide a few common names.
        miner_env = env.copy()
        miner_env.setdefault("RPC_URL", rpc_url)
        miner_env.setdefault("ANIMICA_RPC_URL", rpc_url)
        miner_env.setdefault("CHAIN_ID", str(detected_chain))

        print(f"[devnet] launching miner: {' '.join(miner_cmd)}")
        try:
            miner_proc = _spawn(
                miner_cmd, env=miner_env, cwd=work, stdout_file=work / "miner.log"
            )
            miner_pid = miner_proc.pid
            _write_pidfile(work / MINER_PID, miner_pid)
        except Exception as e:  # noqa: BLE001
            print(
                f"[devnet] WARN: miner failed to start: {e}. Continuing without mining.",
                file=sys.stderr,
            )

    info = DevnetInfo(
        rpc_url=rpc_url,
        ws_url=ws_url,
        chain_id=detected_chain,
        pids={"rpc": rpc_proc.pid, **({"miner": miner_pid} if miner_pid else {})},
    )
    (work / INFO_JSON).write_text(info.to_json())

    print("[devnet] ✅ up")
    print(
        json.dumps(
            {"rpc_url": rpc_url, "ws_url": ws_url, "chain_id": detected_chain}, indent=2
        )
    )
    print(
        f"[devnet] logs: {work/'rpc.log'} {'; ' + str(work/'miner.log') if miner_pid else ''}"
    )
    return 0


def do_down(args: argparse.Namespace) -> int:
    work = Path(args.dir).resolve()
    rpc_pid = _read_pidfile(work / RPC_PID)
    miner_pid = _read_pidfile(work / MINER_PID)

    did_something = False
    if miner_pid:
        print(f"[devnet] stopping miner pid={miner_pid}")
        _terminate(miner_pid)
        did_something = True
        with contextlib.suppress(Exception):
            (work / MINER_PID).unlink()

    if rpc_pid:
        print(f"[devnet] stopping rpc pid={rpc_pid}")
        _terminate(rpc_pid)
        did_something = True
        with contextlib.suppress(Exception):
            (work / RPC_PID).unlink()

    if not did_something:
        print("[devnet] nothing to stop (no pidfiles found)")
        return 0

    # Give processes a moment to exit
    time.sleep(0.5)
    print("[devnet] ✅ down")
    return 0


def do_status(args: argparse.Namespace) -> int:
    work = Path(args.dir).resolve()
    info_path = work / INFO_JSON
    if not info_path.exists():
        print("[devnet] no info.json found; is the devnet up?", file=sys.stderr)
        return 1

    info = DevnetInfo.from_file(info_path)
    # Ping RPC
    ok = False
    try:
        cid = _json_rpc_call(info.rpc_url, "chain.getChainId")
        ok = True
        info.chain_id = cid
    except Exception as e:  # noqa: BLE001
        print(f"[devnet] RPC not responding: {e}", file=sys.stderr)

    print(info.to_json())
    return 0 if ok else 2


def do_attach(args: argparse.Namespace) -> int:
    rpc_url = args.rpc
    ws_url = args.ws or (
        rpc_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
        + "/ws"
    )
    # Probe endpoint
    try:
        cid = _json_rpc_call(rpc_url, "chain.getChainId")
    except urlerror.URLError as e:
        print(f"[attach] unable to reach RPC at {rpc_url}: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[attach] RPC error: {e}", file=sys.stderr)
        return 2

    print(json.dumps({"rpc_url": rpc_url, "ws_url": ws_url, "chain_id": cid}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="devnet_env", description="Animica devnet helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="Start a local devnet (RPC + optional miner)")
    up.add_argument(
        "--dir",
        default=DEFAULT_DIR,
        help="Working directory for db/logs/pids (default: .animica-devnet)",
    )
    up.add_argument(
        "--host", default=DEFAULT_HOST, help="Bind host (default: 127.0.0.1)"
    )
    up.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="HTTP/WS port (default: 8545)"
    )
    up.add_argument(
        "--chain",
        type=int,
        default=DEFAULT_CHAIN_ID,
        help="Desired chain id hint (default: 1337)",
    )
    up.add_argument(
        "--mine",
        dest="mine",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Start CPU miner",
    )
    up.add_argument("--threads", default="auto", help="Miner threads (default: auto)")
    up.add_argument(
        "--rpc-timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for RPC readiness (default: 20.0)",
    )
    up.set_defaults(func=do_up)

    down = sub.add_parser("down", help="Stop local devnet processes")
    down.add_argument(
        "--dir",
        default=DEFAULT_DIR,
        help="Working directory (default: .animica-devnet)",
    )
    down.set_defaults(func=do_down)

    status = sub.add_parser("status", help="Print connection info and ping RPC")
    status.add_argument(
        "--dir",
        default=DEFAULT_DIR,
        help="Working directory (default: .animica-devnet)",
    )
    status.set_defaults(func=do_status)

    attach = sub.add_parser("attach", help="Check and print info for an existing node")
    attach.add_argument(
        "--rpc", required=True, help="HTTP RPC base URL (e.g., http://127.0.0.1:8545)"
    )
    attach.add_argument("--ws", help="WS URL (default: derive from --rpc as /ws)")
    attach.set_defaults(func=do_attach)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
