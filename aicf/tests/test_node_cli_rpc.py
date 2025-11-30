import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from aicf.node import make_block


def _run_cli(cmd: list[str]) -> str:
    proc = subprocess.run([sys.executable, "-m", "aicf.cli.node_pipeline", *cmd], check=True, capture_output=True, text=True)
    return proc.stdout.strip()


class _RpcHandler(BaseHTTPRequestHandler):
    state = {"height": 0, "chainId": 0xA11CA, "auto": False}

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - silence server logs in tests
        return

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 - http.server signature
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        req = json.loads(body.decode() or "{}")
        method = req.get("method")
        params = req.get("params") or []

        if self.path.rstrip("/") != "/rpc":
            self.send_error(404)
            return

        if method == "chain.getHead":
            result = {
                "height": self.state["height"],
                "chainId": self.state["chainId"],
                "autoMine": self.state["auto"],
            }
        elif method == "chain.getChainId":
            result = self.state["chainId"]
        elif method == "miner.mine":
            count = int(params[0]) if params else 1
            mined = max(1, count)
            self.state["height"] += mined
            result = {"mined": mined, "height": self.state["height"]}
        elif method == "miner.getWork":
            next_height = self.state["height"] + 1
            result = {
                "jobId": "job",
                "height": next_height,
                "header": {"number": next_height, "chainId": self.state["chainId"]},
            }
        elif method == "miner.submit_sha256_block":
            self.state["height"] += 1
            result = {"accepted": True, "height": self.state["height"]}
        elif method in {"miner.start", "miner_start", "miner.setAutoMine"}:
            self.state["auto"] = True
            result = True
        elif method in {"miner.stop", "miner_stop"}:
            self.state["auto"] = False
            result = False
        elif method == "chain.getBlockByNumber":
            tag = params[0] if params else "latest"
            if isinstance(tag, str):
                if tag in ("latest", "pending", "safe", "finalized"):
                    height = self.state["height"]
                elif tag == "earliest":
                    height = 0
                elif tag.startswith("0x"):
                    height = int(tag, 16)
                else:
                    height = int(tag)
            else:
                height = int(tag)
            result = make_block(max(0, height))
        else:
            payload = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": "Method not found"}}
            self._send(payload)
            return

        payload = {"jsonrpc": "2.0", "id": req.get("id", 1), "result": result}
        self._send(payload)


def _start_rpc_server(tmp_path: Path, handler_cls: type[BaseHTTPRequestHandler] = _RpcHandler) -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


class _GenerateOnlyHandler(BaseHTTPRequestHandler):
    state = {"height": 0, "chainId": 0xA11CA, "auto": False}

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - silence server logs in tests
        return

    def _send(self, payload: dict, code: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 - http.server signature
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        req = json.loads(body.decode() or "{}")

        if self.path.rstrip("/") != "/rpc":
            self.send_error(404)
            return

        method = req.get("method")
        params = req.get("params") or []
        if method == "chain.getHead":
            result = {
                "height": self.state["height"],
                "chainId": self.state["chainId"],
                "autoMine": self.state["auto"],
            }
        elif method == "animica_generate":
            count = int(params[0]) if params else 1
            self.state["height"] += max(1, count)
            result = {"height": self.state["height"], "mined": max(1, count)}
        elif method == "animica_status":
            result = {"height": self.state["height"], "chainId": self.state["chainId"], "autoMine": self.state["auto"]}
        else:
            payload = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": "Method not found"}}
            self._send(payload)
            return

        payload = {"jsonrpc": "2.0", "id": req.get("id", 1), "result": result}
        self._send(payload)


class _AnvilMineHandler(BaseHTTPRequestHandler):
    state = {"height": 0, "chainId": 0xA11CA, "auto": False}

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - silence server logs in tests
        return

    def _send(self, payload: dict, code: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 - http.server signature
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        req = json.loads(body.decode() or "{}")

        if self.path.rstrip("/") != "/rpc":
            self.send_error(404)
            return

        method = req.get("method")
        params = req.get("params") or []
        if method == "chain.getHead":
            result = {
                "height": self.state["height"],
                "chainId": self.state["chainId"],
                "autoMine": self.state["auto"],
            }
        elif method == "anvil_mine":
            count = int(params[0]) if params else 1
            self.state["height"] += max(1, count)
            result = True
        elif method == "eth_blockNumber":
            result = hex(self.state["height"])
        elif method == "eth_chainId":
            result = hex(self.state["chainId"])
        else:
            payload = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": "Method not found"}}
            self._send(payload)
            return

        payload = {"jsonrpc": "2.0", "id": req.get("id", 1), "result": result}
        self._send(payload)


class _MinerOnlyHandler(BaseHTTPRequestHandler):
    state = {"height": 0, "chainId": 0xA11CA, "auto": False}

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - silence server logs in tests
        return

    def _send(self, payload: dict, code: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 - http.server signature
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        req = json.loads(body.decode() or "{}")
        method = req.get("method")
        params = req.get("params") or []

        if self.path.rstrip("/") != "/rpc":
            self.send_error(404)
            return

        if method == "chain.getHead":
            result = {"height": self.state["height"], "chainId": self.state["chainId"], "autoMine": self.state["auto"]}
        elif method == "miner.mine":
            count = int(params[0]) if params else 1
            mined = max(1, count)
            self.state["height"] += mined
            result = {"mined": mined, "height": self.state["height"]}
        elif method == "miner.start":
            self.state["auto"] = True
            result = True
        elif method == "miner.stop":
            self.state["auto"] = False
            result = False
        else:
            payload = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": "Method not found"}}
            self._send(payload)
            return

        payload = {"jsonrpc": "2.0", "id": req.get("id", 1), "result": result}
        self._send(payload)


def test_status_and_mine_against_rpc(tmp_path: Path) -> None:
    server, url = _start_rpc_server(tmp_path)
    try:
        status = json.loads(_run_cli(["status", "--rpc-url", url, "--json"]))
        assert status["height"] == 0
        assert int(status["chainId"]) == _RpcHandler.state["chainId"]

        new_height = int(_run_cli(["mine", "--rpc-url", url, "--count", "1"]))
        assert new_height == 1

        status_after = json.loads(_run_cli(["status", "--rpc-url", url, "--json"]))
        assert status_after["height"] == 1

        block_one = json.loads(_run_cli(["block", "1", "--rpc-url", url, "--json"]))
        assert block_one["number"] == hex(1)
    finally:
        server.shutdown()
        server.server_close()


def test_mine_fallbacks_when_miner_endpoints_missing(tmp_path: Path) -> None:
    server, url = _start_rpc_server(tmp_path, _GenerateOnlyHandler)
    try:
        new_height = int(_run_cli(["mine", "--rpc-url", url, "--count", "2"]))
        assert new_height == 2

        status_after = json.loads(_run_cli(["status", "--rpc-url", url, "--json"]))
        assert status_after["height"] == 2
    finally:
        server.shutdown()
        server.server_close()


def test_mine_fallbacks_to_anvil_mine(tmp_path: Path) -> None:
    server, url = _start_rpc_server(tmp_path, _AnvilMineHandler)
    try:
        new_height = int(_run_cli(["mine", "--rpc-url", url, "--count", "3"]))
        assert new_height == 3

        status_after = json.loads(_run_cli(["status", "--rpc-url", url, "--json"]))
        assert status_after["height"] == 3
    finally:
        server.shutdown()
        server.server_close()


def test_mine_uses_miner_endpoint_when_available(tmp_path: Path) -> None:
    server, url = _start_rpc_server(tmp_path, _MinerOnlyHandler)
    try:
        new_height = int(_run_cli(["mine", "--rpc-url", url, "--count", "2"]))
        assert new_height == 2

        status_after = json.loads(_run_cli(["status", "--rpc-url", url, "--json"]))
        assert status_after["height"] == 2

        toggled = _run_cli(["auto", "true", "--rpc-url", url])
        assert toggled.strip() == "on"
    finally:
        server.shutdown()
        server.server_close()
