from __future__ import annotations

"""
Animica Stratum Proxy CLI

Bridges the local node's mining getwork API (HTTP/WS JSON-RPC) to external Stratum miners.

It prefers using the first available integration exposed by mining.stratum_server:
  1) run_proxy(rpc_url=..., ws_url=..., listen_host=..., listen_port=..., **cfg)
  2) serve_with_backend(listen_host, listen_port, backend, **cfg)
     where backend implements:
        async get_current_work() -> dict
        async submit_share(share: dict) -> dict
  3) StratumServer(backend, **cfg).run_async(listen_host, listen_port)

If none of the above are available, it will start a minimal, built-in Stratum v1-compatible
server that supports subscribe/authorize/submit and broadcasts "notify" frames whenever new work
arrives (via polling the node). This fallback understands the Animica HashShare job envelope.

Examples:
  python -m mining.cli.stratum_proxy start --rpc-url http://127.0.0.1:8547 \
      --ws-url ws://127.0.0.1:8547/ws --listen 0.0.0.0:3333 --poll-interval 1.5

Environment overrides:
  ANIMICA_RPC_URL, ANIMICA_WS_URL, ANIMICA_STRATUM_LISTEN, ANIMICA_CHAIN_ID
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

# -------- Logging -------------------------------------------------------------


def _setup_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


log = logging.getLogger("stratum.proxy")

# -------- Utilities -----------------------------------------------------------


def _env_default(name: str, fallback: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None and v != "" else fallback


def _parse_host_port(value: str) -> Tuple[str, int]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("expected HOST:PORT")
    host, port_str = value.rsplit(":", 1)
    try:
        return host, int(port_str)
    except ValueError as e:
        raise argparse.ArgumentTypeError("invalid port") from e


# -------- JSON-RPC HTTP/WS client helpers ------------------------------------


class JsonRpcClient:
    """Tiny JSON-RPC over HTTP client with retries."""

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._id = 0

    async def call(
        self, method: str, params: Any | None = None, retries: int = 3
    ) -> Any:
        import urllib.request
        from urllib.error import HTTPError, URLError

        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or [],
        }
        self._id += 1
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.url}/rpc",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        backoff = 0.25
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    obj = json.loads(body)
                    if "error" in obj and obj["error"] is not None:
                        raise RuntimeError(f"RPC error: {obj['error']}")
                    return obj.get("result")
            except (URLError, HTTPError, TimeoutError) as e:
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(backoff)
                backoff *= 2.0

        raise RuntimeError("unreachable: retries loop exited unexpectedly")


# -------- Work backend (talks to node RPC/WS) --------------------------------


@dataclass
class RpcWorkBackend:
    rpc_url: str
    ws_url: Optional[str] = None
    poll_interval: float = 1.5
    chain_id: int = 1
    _rpc: JsonRpcClient = field(init=False)
    _current_work: Optional[Dict[str, Any]] = field(default=None, init=False)
    _current_job_id: Optional[str] = field(default=None, init=False)
    _updated_at: float = field(default=0.0, init=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def __post_init__(self) -> None:
        self._rpc = JsonRpcClient(self.rpc_url)

    async def start(self) -> None:
        # Prefer WS push if available
        if self.ws_url:
            asyncio.create_task(self._ws_watch_loop(), name="work-ws-loop")
        # Always run a polling loop as a safety net
        asyncio.create_task(self._poll_loop(), name="work-poll-loop")

    async def stop(self) -> None:
        self._stop.set()

    async def _ws_watch_loop(self) -> None:
        try:
            import websockets  # type: ignore
        except Exception:
            log.warning("websockets not available; falling back to polling only")
            return

        ws_uri = self.ws_url.rstrip("/")
        # Expect the main WS hub at /ws; subscribe to 'newWork' broadcast if available.
        uri = ws_uri if ws_uri.endswith("/ws") else f"{ws_uri}/ws"

        while not self._stop.is_set():
            try:
                log.info("connecting WS %s", uri)
                async with websockets.connect(uri, max_size=8 * 1024 * 1024) as ws:
                    # Subscribe protocol: send {"op":"subscribe","topic":"newWork"}
                    sub = {"op": "subscribe", "topic": "newWork"}
                    await ws.send(json.dumps(sub))
                    async for msg in ws:
                        try:
                            obj = json.loads(msg)
                        except Exception:
                            continue
                        # Accept either hub-style messages or direct work payloads
                        if isinstance(obj, dict) and obj.get("topic") == "newWork":
                            payload = obj.get("payload")
                        else:
                            payload = obj
                        if isinstance(payload, dict) and payload.get("jobId"):
                            self._current_work = payload
                            self._current_job_id = payload.get("jobId")
                            self._updated_at = time.time()
                            log.debug(
                                "WS new work job=%s height=%s Θ=%.3f",
                                payload.get("jobId"),
                                payload.get("height"),
                                payload.get("theta", 0),
                            )
            except Exception as e:
                log.warning("WS watch error: %s; retrying in 2s", e)
                await asyncio.sleep(2.0)

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                work = await self._rpc.call(
                    "miner.getWork", [{"chainId": self.chain_id}]
                )
                if (
                    isinstance(work, dict)
                    and work.get("jobId")
                    and work != self._current_work
                ):
                    self._current_work = work
                    self._current_job_id = work.get("jobId")
                    self._updated_at = time.time()
                    log.debug(
                        "poll new work job=%s height=%s",
                        work.get("jobId"),
                        work.get("height"),
                    )
            except Exception as e:
                log.debug("poll getWork error: %s", e)
            await asyncio.sleep(self.poll_interval)

    # API expected by stratum_server integrations
    async def get_current_work(self) -> Optional[Dict[str, Any]]:
        return self._current_work

    async def submit_share(self, share: Dict[str, Any]) -> Dict[str, Any]:
        """
        Forward a share (HashShare/AI/Quantum) to the node via JSON-RPC.
        The share object is expected to be already in the node's schema if the upstream
        stratum_server converted it; if we are in fallback mode we map fields below.
        """
        try:
            res = await self._rpc.call("miner.submitShare", [share])
            return {
                "accepted": bool(res.get("accepted", False)),
                "reason": res.get("reason"),
            }
        except Exception as e:
            return {"accepted": False, "reason": str(e)}


# -------- Fallback mini Stratum server (if mining.stratum_server is missing) --


class MiniStratumServer:
    """
    Minimal JSON-RPC-over-TCP server implementing a subset of Stratum for Animica:
      - mining.subscribe
      - mining.authorize
      - mining.submit
    It pushes 'mining.notify' upon new work.

    This is a compatibility fallback; the full featured server in mining/stratum_server
    should be preferred.
    """

    def __init__(self, backend: RpcWorkBackend, host: str, port: int) -> None:
        self.backend = backend
        self.host = host
        self.port = port
        self.clients: set[asyncio.StreamWriter] = set()
        self._notify_task: Optional[asyncio.Task] = None
        self._last_job_id: Optional[str] = None

    async def start(self) -> None:
        server = await asyncio.start_server(self._handle_client, self.host, self.port)
        addr = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
        log.info("MiniStratum listening on %s", addr)
        self._notify_task = asyncio.create_task(self._notify_loop(), name="notify-loop")
        async with server:
            await server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        self.clients.add(writer)
        log.info("Stratum client connected %s", peer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    req = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                resp = await self._handle_req(req)
                if resp is not None:
                    writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                    await writer.drain()
        except Exception as e:
            log.debug("client error %s: %s", peer, e)
        finally:
            self.clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            log.info("Stratum client disconnected %s", peer)

    async def _handle_req(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mid = req.get("id")
        method = req.get("method")
        params = req.get("params", [])
        if method == "mining.subscribe":
            # Return subscription id and extranonce placeholder
            return {"id": mid, "result": ["animica-sub", "00"], "error": None}
        if method == "mining.authorize":
            return {"id": mid, "result": True, "error": None}
        if method == "mining.submit":
            # Params shape is miner-dependent; we accept a dict as first param,
            # or a legacy tuple and map to a dict.
            share = params[0] if params else {}
            if (
                not isinstance(share, dict)
                and isinstance(params, list)
                and len(params) >= 3
            ):
                # Legacy: [worker, job_id, nonce, ...]
                share = {"jobId": params[1], "nonce": params[2]}
            result = await self.backend.submit_share(share)
            return {
                "id": mid,
                "result": bool(result.get("accepted", False)),
                "error": None,
            }
        # Unknown method
        return {
            "id": mid,
            "result": None,
            "error": {"code": -32601, "message": "Method not found"},
        }

    async def _notify_loop(self) -> None:
        while True:
            work = await self.backend.get_current_work()
            if work and work.get("jobId") != self._last_job_id:
                self._last_job_id = work.get("jobId")
                notify = {
                    "id": None,
                    "method": "mining.notify",
                    "params": [work],  # single Animica job object
                }
                payload = (json.dumps(notify) + "\n").encode("utf-8")
                stale = []
                for w in self.clients:
                    try:
                        w.write(payload)
                    except Exception:
                        stale.append(w)
                for w in stale:
                    self.clients.discard(w)
                if self.clients:
                    await asyncio.gather(
                        *(c.drain() for c in self.clients), return_exceptions=True
                    )
            await asyncio.sleep(0.25)


# -------- Proxy runner --------------------------------------------------------


async def run_proxy(
    rpc_url: str,
    ws_url: Optional[str],
    listen_host: str,
    listen_port: int,
    chain_id: int,
    poll_interval: float,
    log_level: str,
) -> None:
    _setup_logging(log_level)
    backend = RpcWorkBackend(
        rpc_url=rpc_url, ws_url=ws_url, poll_interval=poll_interval, chain_id=chain_id
    )
    await backend.start()

    # Try to use the full-featured server if available
    try:
        import mining.stratum_server as ss  # type: ignore
    except Exception:
        ss = None

    # 1) run_proxy(...) shape
    if ss and hasattr(ss, "run_proxy"):
        log.info("Launching stratum_server.run_proxy(...)")
        coro = ss.run_proxy(  # type: ignore[attr-defined]
            rpc_url=rpc_url,
            ws_url=ws_url,
            listen_host=listen_host,
            listen_port=listen_port,
            chain_id=chain_id,
            poll_interval=poll_interval,
        )
        if asyncio.iscoroutine(coro):
            await coro  # type: ignore[misc]
            return

    # 2) serve_with_backend(listen_host, listen_port, backend)
    if ss and hasattr(ss, "serve_with_backend"):
        log.info("Launching stratum_server.serve_with_backend(...)")
        coro = ss.serve_with_backend(listen_host, listen_port, backend)  # type: ignore[attr-defined]
        await (coro if asyncio.iscoroutine(coro) else asyncio.to_thread(coro))  # type: ignore[misc]
        return

    # 3) StratumServer(backend) shape
    if ss and hasattr(ss, "StratumServer"):
        log.info("Launching stratum_server.StratumServer(...).run_async")
        Server = ss.StratumServer  # type: ignore[attr-defined]
        server = Server(backend)
        if hasattr(server, "run_async") and asyncio.iscoroutinefunction(server.run_async):  # type: ignore[attr-defined]
            await server.run_async(listen_host, listen_port)  # type: ignore[misc]
            return
        if hasattr(server, "run"):
            await asyncio.to_thread(server.run, listen_host, listen_port)  # type: ignore[attr-defined]
            return

    # Fallback mini server
    log.warning("Falling back to built-in minimal Stratum server (limited feature set)")
    mini = MiniStratumServer(backend, listen_host, listen_port)
    await mini.start()


# -------- CLI -----------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omni stratum-proxy", description="Animica Stratum proxy"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    start = sub.add_parser("start", help="start the Stratum proxy")
    start.add_argument(
        "--rpc-url",
        type=str,
        default=_env_default("ANIMICA_RPC_URL", "http://127.0.0.1:8547"),
        help="node JSON-RPC base (http://host:port)",
    )
    start.add_argument(
        "--ws-url",
        type=str,
        default=_env_default("ANIMICA_WS_URL", "ws://127.0.0.1:8547/ws"),
        help="node WebSocket hub (ws://host:port/ws)",
    )
    start.add_argument(
        "--listen",
        type=_parse_host_port,
        default=_parse_host_port(
            _env_default("ANIMICA_STRATUM_LISTEN", "0.0.0.0:3333") or "0.0.0.0:3333"
        ),
        help="Stratum listen HOST:PORT (default 0.0.0.0:3333)",
    )
    start.add_argument(
        "--chain-id",
        type=int,
        default=int(_env_default("ANIMICA_CHAIN_ID", "1") or "1"),
        help="chain id (default 1)",
    )
    start.add_argument(
        "--poll-interval",
        type=float,
        default=1.5,
        help="HTTP poll interval seconds for getWork (WS used when available)",
    )
    start.add_argument(
        "--log-level",
        type=str,
        default=_env_default("ANIMICA_LOG_LEVEL", "info"),
        help="logging level (debug, info, warning, error)",
    )
    return p


async def _amain(argv: list[str]) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.cmd != "start":
        print("unknown command", file=sys.stderr)
        return 2

    stop_event = asyncio.Event()

    def _sig(_sig: int, _frm: Any | None) -> None:
        log.info("signal received, shutting down…")
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _sig)  # type: ignore[arg-type]
        except Exception:
            pass

    runner = asyncio.create_task(
        run_proxy(
            rpc_url=args.rpc_url,
            ws_url=args.ws_url,
            listen_host=args.listen[0],
            listen_port=args.listen[1],
            chain_id=args.chain_id,
            poll_interval=args.poll_interval,
            log_level=args.log_level,
        ),
        name="stratum-proxy",
    )

    await stop_event.wait()
    # Cancel the server task; underlying servers should handle graceful shutdown.
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass
    return 0


def main() -> None:
    try:
        rc = asyncio.run(_amain(sys.argv[1:]))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
