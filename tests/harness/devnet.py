"""
Devnet process harness
======================

Spins up a multi-process local devnet for tests:
- One or more node processes (JSON-RPC + optional WS)
- Optional separate miner process
- studio-services (FastAPI) pointing at the node
- Optional explorer (preview server) pointing at the same RPC/services

The harness is **command-agnostic**: you provide the actual launch
commands via environment variables so it works with your local node
binary/scripts without baking assumptions into tests.

Environment (defaults shown where applicable)
---------------------------------------------
# Node(s)
NODE_CMD="""
  # Example template (adjust to your node):
  omni-node --dev --http --http-port {rpc_port} --ws --ws-port {ws_port} \
            --datadir {datadir} --mine --allow-insecure-unlock
"""
NODE_COUNT="1"
NODE_RPC_PORT_BASE="8545"
NODE_WS_PORT_BASE="8546"
NODE_DATADIR_BASE=".cache/devnet/node{index}"

# Miner (optional standalone)
MINER_CMD=""  # e.g. "omni-miner --rpc {rpc_url}"

# studio-services (enabled by default)
START_SERVICES="1"
SERVICES_CMD="""
  python -m studio_services.main --host 127.0.0.1 --port {services_port}
"""
SERVICES_PORT="8787"
SERVICES_STORAGE_DIR=".cache/devnet/services_storage"

# Explorer (optional)
START_EXPLORER="0"
EXPLORER_CMD="""
  npm run -w explorer-web preview -- --port {explorer_port} --host 127.0.0.1
"""
EXPLORER_PORT="4173"

# Common
CHAIN_ID="1337"
HTTP_TIMEOUT="30"
WS_TIMEOUT="30"
LOG_DIR=".cache/devnet/logs"

Usage in tests
--------------
from tests.harness.devnet import Devnet

def test_end_to_end():
    with Devnet().start() as dn:
        # dn.rpc_url points to node[0] JSON-RPC
        # dn.services_url points to studio-services (if started)
        # ... run your integration steps ...


Notes
-----
- Health checks: the harness first tries service /healthz endpoints;
  if not present, it falls back to JSON-RPC "omni_chainId", "eth_chainId",
  or "web3_clientVersion".
- All stdout/stderr is tee'd to rotating-ish log files under LOG_DIR.
- Clean shutdown: SIGTERM (grace), then SIGKILL on timeout.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    # Reuse helpers if available
    from tests.harness import (
        REPO_ROOT,
        TESTS_ROOT,
        get_logger,
        DEFAULT_CHAIN_ID as _DEFAULT_CHAIN_ID,
        DEFAULT_HTTP_TIMEOUT as _DEFAULT_HTTP_TIMEOUT,
        DEFAULT_WS_TIMEOUT as _DEFAULT_WS_TIMEOUT,
    )
except Exception:  # pragma: no cover - fallback if imported standalone
    REPO_ROOT = Path(__file__).resolve().parents[2]
    TESTS_ROOT = REPO_ROOT / "tests"

    def get_logger(name: str = "tests.harness.devnet"):
        import logging

        logger = logging.getLogger(name)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            logger.addHandler(handler)
        logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
        return logger

    _DEFAULT_CHAIN_ID = 1337
    _DEFAULT_HTTP_TIMEOUT = 30.0
    _DEFAULT_WS_TIMEOUT = 30.0


LOG = get_logger("tests.harness.devnet")


# ------------------------------ utils ---------------------------------


def _env_str(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


def _env_int(key: str, default: Optional[int] = None) -> Optional[int]:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val, 0)
    except Exception:
        return default


def _env_float(key: str, default: Optional[float] = None) -> Optional[float]:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except Exception:
        return default


def _is_truthy(val: Optional[str]) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _pick_free_port(preferred: Optional[int] = None) -> int:
    if preferred:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_request(
    method: str,
    url: str,
    timeout: float,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
) -> Tuple[int, bytes]:
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url=url, method=method, data=body or None)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, "read") else b""
    except Exception:
        return 0, b""


def _jsonrpc_ping(rpc_url: str, timeout: float) -> bool:
    payloads = [
        {"jsonrpc": "2.0", "id": 1, "method": "omni_chainId", "params": []},
        {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
        {"jsonrpc": "2.0", "id": 1, "method": "web3_clientVersion", "params": []},
    ]
    for p in payloads:
        code, body = _http_request(
            "POST",
            rpc_url,
            timeout=timeout,
            headers={"content-type": "application/json"},
            body=json.dumps(p).encode("utf-8"),
        )
        if code == 200:
            try:
                j = json.loads(body.decode("utf-8"))
                if "result" in j or "error" in j:
                    return True
            except Exception:
                pass
    return False


def _wait_for_http_up(urls: Sequence[str], timeout: float, kind: str) -> None:
    deadline = time.time() + timeout
    last_err: Optional[str] = None
    while time.time() < deadline:
        for u in urls:
            code, _ = _http_request("GET", u, timeout=timeout * 0.25)
            if code == 200:
                LOG.info("Healthy %s at %s", kind, u)
                return
            last_err = f"{u} -> {code}"
        time.sleep(0.25)
    raise RuntimeError(f"Timeout waiting for {kind} health: {last_err or 'no response'}")


def _wait_for_rpc_up(rpc_url: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _jsonrpc_ping(rpc_url, timeout=timeout * 0.25):
            LOG.info("RPC up at %s", rpc_url)
            return
        time.sleep(0.25)
    raise RuntimeError(f"Timeout waiting for RPC at {rpc_url}")


def _format_cmd(cmd_template: str, **params: object) -> List[str]:
    # Allow both "{name}" and "$name" style (basic expansion)
    templ = cmd_template.strip()
    for k, v in params.items():
        templ = templ.replace(f"${k}", str(v))
    templ = templ.format_map({k: v for k, v in params.items()})
    return shlex.split(templ)


def _open_log(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Line-buffered text to keep interleaving readable
    return open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")


# ------------------------------ model ---------------------------------


@dataclass
class ProcHandle:
    name: str
    popen: subprocess.Popen
    log_file: object
    env: Dict[str, str]
    cmd: List[str]

    def terminate(self, grace: float = 5.0) -> None:
        if self.popen.poll() is not None:
            return
        try:
            self.popen.terminate()
        except Exception:
            pass
        deadline = time.time() + grace
        while time.time() < deadline:
            if self.popen.poll() is not None:
                break
            time.sleep(0.05)
        if self.popen.poll() is None:
            try:
                self.popen.kill()
            except Exception:
                pass

    def close(self) -> None:
        try:
            self.log_file.flush()
            self.log_file.close()
        except Exception:
            pass


@dataclass
class DevnetConfig:
    node_cmd: Optional[str] = field(default_factory=lambda: _env_str("NODE_CMD"))
    node_count: int = field(default_factory=lambda: _env_int("NODE_COUNT", 1) or 1)
    node_rpc_port_base: int = field(default_factory=lambda: _env_int("NODE_RPC_PORT_BASE", 8545) or 8545)
    node_ws_port_base: int = field(default_factory=lambda: _env_int("NODE_WS_PORT_BASE", 8546) or 8546)
    node_datadir_template: str = field(
        default_factory=lambda: _env_str("NODE_DATADIR_BASE", ".cache/devnet/node{index}") or ".cache/devnet/node{index}"
    )

    miner_cmd: Optional[str] = field(default_factory=lambda: _env_str("MINER_CMD"))

    start_services: bool = field(default_factory=lambda: _is_truthy(_env_str("START_SERVICES", "1")))
    services_cmd: str = field(
        default_factory=lambda: _env_str("SERVICES_CMD", "python -m studio_services.main --host 127.0.0.1 --port {services_port}")
        or "python -m studio_services.main --host 127.0.0.1 --port {services_port}"
    )
    services_port: int = field(default_factory=lambda: _env_int("SERVICES_PORT", 8787) or 8787)
    services_storage_dir: str = field(
        default_factory=lambda: _env_str("SERVICES_STORAGE_DIR", ".cache/devnet/services_storage")
        or ".cache/devnet/services_storage"
    )

    start_explorer: bool = field(default_factory=lambda: _is_truthy(_env_str("START_EXPLORER", "0")))
    explorer_cmd: Optional[str] = field(default_factory=lambda: _env_str("EXPLORER_CMD"))
    explorer_port: int = field(default_factory=lambda: _env_int("EXPLORER_PORT", 4173) or 4173)

    chain_id: int = field(default_factory=lambda: _env_int("CHAIN_ID", _DEFAULT_CHAIN_ID) or _DEFAULT_CHAIN_ID)
    http_timeout: float = field(default_factory=lambda: _env_float("HTTP_TIMEOUT", _DEFAULT_HTTP_TIMEOUT) or _DEFAULT_HTTP_TIMEOUT)
    ws_timeout: float = field(default_factory=lambda: _env_float("WS_TIMEOUT", _DEFAULT_WS_TIMEOUT) or _DEFAULT_WS_TIMEOUT)

    log_dir: Path = field(default_factory=lambda: Path(_env_str("LOG_DIR", ".cache/devnet/logs") or ".cache/devnet/logs"))
    cwd: Path = field(default_factory=lambda: REPO_ROOT)

    def datadir_for(self, index: int) -> Path:
        return self.cwd / self.node_datadir_template.format(index=index)


class Devnet:
    """
    Context-managed process supervisor that starts/stops the devnet stack.

    Attributes after start():
      - rpc_url: str (node[0] JSON-RPC URL)
      - ws_url: Optional[str]
      - services_url: Optional[str]
      - explorer_url: Optional[str]
    """

    def __init__(self, cfg: Optional[DevnetConfig] = None):
        self.cfg = cfg or DevnetConfig()
        self.procs: List[ProcHandle] = []
        self.rpc_url: Optional[str] = None
        self.ws_url: Optional[str] = None
        self.services_url: Optional[str] = None
        self.explorer_url: Optional[str] = None
        self._started = False

    # ------------- lifecycle -------------

    def start(self) -> "Devnet":
        if self._started:
            return self
        LOG.info("Starting devnet...")
        self.cfg.log_dir.mkdir(parents=True, exist_ok=True)

        # 1) Start node(s)
        rpc_ports: List[int] = []
        ws_ports: List[int] = []
        for i in range(self.cfg.node_count):
            rpc_port = _pick_free_port(self.cfg.node_rpc_port_base + i if self.cfg.node_rpc_port_base else None)
            ws_port = _pick_free_port(self.cfg.node_ws_port_base + i if self.cfg.node_ws_port_base else None)
            datadir = self.cfg.datadir_for(i)
            datadir.mkdir(parents=True, exist_ok=True)

            if not self.cfg.node_cmd:
                LOG.warning("NODE_CMD not set; skipping node %d startup (assuming external RPC)", i)
            else:
                node_env = os.environ.copy()
                node_env.setdefault("CHAIN_ID", str(self.cfg.chain_id))
                cmd = _format_cmd(
                    self.cfg.node_cmd,
                    index=i,
                    rpc_port=rpc_port,
                    ws_port=ws_port,
                    datadir=str(datadir),
                )
                self._spawn(
                    name=f"node-{i}",
                    cmd=cmd,
                    env=node_env,
                    log_file=self.cfg.log_dir / f"node-{i}.log",
                    cwd=self.cfg.cwd,
                )

            rpc_ports.append(rpc_port)
            ws_ports.append(ws_port)

        # Bind URLs
        self.rpc_url = f"http://127.0.0.1:{rpc_ports[0]}"
        self.ws_url = f"ws://127.0.0.1:{ws_ports[0]}"

        # 2) Optional miner
        if self.cfg.miner_cmd:
            miner_env = os.environ.copy()
            miner_env.setdefault("CHAIN_ID", str(self.cfg.chain_id))
            miner_env.setdefault("RPC_URL", self.rpc_url)
            self._spawn(
                name="miner",
                cmd=_format_cmd(self.cfg.miner_cmd, rpc_url=self.rpc_url),
                env=miner_env,
                log_file=self.cfg.log_dir / "miner.log",
                cwd=self.cfg.cwd,
            )

        # 3) studio-services
        if self.cfg.start_services:
            services_port = _pick_free_port(self.cfg.services_port)
            self.services_url = f"http://127.0.0.1:{services_port}"
            services_env = os.environ.copy()
            services_env.setdefault("RPC_URL", self.rpc_url)
            services_env.setdefault("CHAIN_ID", str(self.cfg.chain_id))
            storage_dir = (self.cfg.cwd / self.cfg.services_storage_dir).resolve()
            storage_dir.mkdir(parents=True, exist_ok=True)
            services_env.setdefault("STORAGE_DIR", str(storage_dir))
            cmd = _format_cmd(self.cfg.services_cmd, services_port=services_port)
            self._spawn(
                name="studio-services",
                cmd=cmd,
                env=services_env,
                log_file=self.cfg.log_dir / "studio-services.log",
                cwd=self.cfg.cwd,
            )

        # 4) Explorer (optional)
        if self.cfg.start_explorer and self.cfg.explorer_cmd:
            explorer_port = _pick_free_port(self.cfg.explorer_port)
            self.explorer_url = f"http://127.0.0.1:{explorer_port}"
            explorer_env = os.environ.copy()
            explorer_env.setdefault("VITE_RPC_URL", self.rpc_url)
            if self.services_url:
                explorer_env.setdefault("VITE_SERVICES_URL", self.services_url)
            explorer_env.setdefault("VITE_CHAIN_ID", str(self.cfg.chain_id))
            cmd = _format_cmd(self.cfg.explorer_cmd, explorer_port=explorer_port)
            self._spawn(
                name="explorer",
                cmd=cmd,
                env=explorer_env,
                log_file=self.cfg.log_dir / "explorer.log",
                cwd=self.cfg.cwd,
            )

        # 5) Health checks (node first)
        self._post_start_health()
        self._started = True
        LOG.info("Devnet is up: rpc=%s services=%s explorer=%s", self.rpc_url, self.services_url, self.explorer_url)
        return self

    def stop(self) -> None:
        # Stop in reverse order
        LOG.info("Stopping devnet...")
        for p in reversed(self.procs):
            LOG.info("Terminating %s (pid=%s)", p.name, getattr(p.popen, "pid", "?"))
            p.terminate(grace=7.0)
        for p in reversed(self.procs):
            p.close()
        self.procs.clear()
        self._started = False
        LOG.info("Devnet stopped.")

    # ------------- internals -------------

    def _spawn(
        self,
        name: str,
        cmd: List[str],
        env: Dict[str, str],
        log_file: Path,
        cwd: Path,
    ) -> ProcHandle:
        LOG.info("Launching %s: %s", name, " ".join(shlex.quote(x) for x in cmd))
        lf = _open_log(log_file)
        popen = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=lf,
            stderr=lf,
            start_new_session=True,  # so we can kill the whole group if needed
        )
        handle = ProcHandle(name=name, popen=popen, log_file=lf, env=env, cmd=cmd)
        self.procs.append(handle)
        return handle

    def _post_start_health(self) -> None:
        # Node RPC health
        assert self.rpc_url, "rpc_url not set"
        # Try /healthz first if node serves one
        health_urls = [f"{self.rpc_url}/healthz", f"{self.rpc_url}/readyz", f"{self.rpc_url}/version"]
        try:
            _wait_for_http_up(health_urls, timeout=self.cfg.http_timeout, kind="node")
        except Exception:
            _wait_for_rpc_up(self.rpc_url, timeout=self.cfg.http_timeout)

        # studio-services health
        if self.services_url:
            _wait_for_http_up(
                [f"{self.services_url}/healthz", f"{self.services_url}/readyz", f"{self.services_url}/version"],
                timeout=self.cfg.http_timeout,
                kind="studio-services",
            )
        # Explorer health (if enabled)
        if self.explorer_url:
            _wait_for_http_up(
                [self.explorer_url],
                timeout=self.cfg.http_timeout,
                kind="explorer",
            )

    # ------------- context manager -------------

    def __enter__(self) -> "Devnet":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


# ------------------------------ CLI -----------------------------------


def _print_banner(dn: Devnet) -> None:
    print("Devnet running")
    print(f"  RPC:       {dn.rpc_url}")
    if dn.ws_url:
        print(f"  WS:        {dn.ws_url}")
    if dn.services_url:
        print(f"  Services:  {dn.services_url}")
    if dn.explorer_url:
        print(f"  Explorer:  {dn.explorer_url}")
    print("Press Ctrl+C to stop.")


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    # A tiny argparse to allow --no-services / --explorer
    import argparse

    ap = argparse.ArgumentParser(description="Start local devnet (tests harness).")
    ap.add_argument("--no-services", action="store_true", help="Do not start studio-services")
    ap.add_argument("--explorer", action="store_true", help="Start explorer (if EXPLORER_CMD provided)")
    ap.add_argument("--nodes", type=int, default=None, help="Override node count")
    ap.add_argument("--rpc-port", type=int, default=None, help="Preferred RPC base port for node[0]")
    ap.add_argument("--services-port", type=int, default=None, help="Preferred port for studio-services")
    args = ap.parse_args(argv)

    cfg = DevnetConfig()
    if args.no_services:
        cfg.start_services = False
    if args.explorer:
        cfg.start_explorer = True
    if args.nodes is not None:
        cfg.node_count = max(1, int(args.nodes))
    if args.rpc_port is not None:
        cfg.node_rpc_port_base = int(args.rpc_port)
    if args.services_port is not None:
        cfg.services_port = int(args.services_port)

    # If no NODE_CMD, we assume user already runs a node at RPC_URL
    if not cfg.node_cmd:
        existing = os.environ.get("RPC_URL")
        if not existing:
            LOG.warning(
                "NODE_CMD not set and RPC_URL missing. "
                "Either export NODE_CMD to spawn a node, or set RPC_URL to attach."
            )

    # If attaching, don't perform node health / port checks (we still try RPC ping).
    with Devnet(cfg).start() as dn:
        _print_banner(dn)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
