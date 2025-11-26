from __future__ import annotations
import argparse, json, threading, time, hashlib, signal, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

state = {"height": 0, "chain_id": "0xa11ca", "auto_mine": False}
lock = threading.Lock()
state_file: Path | None = None

def load_state(datadir: str) -> None:
    global state_file
    state_file = Path(datadir) / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    if state_file.exists():
        try:
            saved = json.loads(state_file.read_text())
            with lock:
                state["height"] = int(saved.get("height", 0))
                state["chain_id"] = saved.get("chain_id", state["chain_id"])
        except Exception:
            pass

def save_state() -> None:
    if state_file is None: return
    with lock:
        state_file.write_text(json.dumps({"height": state["height"], "chain_id": state["chain_id"]}))

def make_block(n: int) -> dict:
    parent = "0x" + "0"*64 if n <= 0 else "0x" + hashlib.sha256(f"{n-1}".encode()).hexdigest()
    hsh    = "0x" + hashlib.sha256(f"{n}".encode()).hexdigest()
    return {"number": hex(n),"hash": hsh,"parentHash": parent,"timestamp": hex(int(time.time())),
            "difficulty": "0x1","nonce": "0x0","miner": "0x" + "0"*40,"transactions": []}

class Handler(BaseHTTPRequestHandler):
    def _send(self, obj: dict, code: int = 200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        try:
            req = json.loads(body or b"{}")
        except Exception:
            self._send({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}}, 400); return

        if not isinstance(req, dict):
            self._send({"jsonrpc":"2.0","id":None,"error":{"code":-32600,"message":"Invalid Request"}}, 400); return

        mid    = req.get("id", 1)
        method = req.get("method")
        params = req.get("params", []) or []
        ok  = lambda result: self._send({"jsonrpc":"2.0","id":mid,"result":result})
        err = lambda code,msg: self._send({"jsonrpc":"2.0","id":mid,"error":{"code":code,"message":msg}}, 200)

        with lock:
            if method in ("eth_blockNumber","animica_blockNumber"): ok(hex(state["height"])); return
            if method in ("net_version","eth_chainId","animica_chainId"): ok(state["chain_id"]); return
            if method == "evm_mine":
                n = 1
                if params:
                    v = params[0]
                    try:
                        if isinstance(v,int): n = v
                        elif isinstance(v,str) and v.startswith("0x"): n = int(v,16)
                        else: n = int(v)
                    except Exception: n = 1
                n = max(1,n)
                state["height"] += n; save_state(); ok(hex(state["height"])); return
            if method == "miner_start": state["auto_mine"] = True; ok(True); return
            if method == "miner_stop":  state["auto_mine"] = False; ok(True); return
            if method == "eth_getBlockByNumber":
                tag = params[0] if params else "latest"
                if isinstance(tag,str):
                    if tag in ("latest","finalized","safe","pending"): n = state["height"]
                    elif tag == "earliest": n = 0
                    elif tag.startswith("0x"): n = int(tag,16)
                    else: n = int(tag)
                else: n = state["height"]
                ok(make_block(max(0,n))); return
            if method == "web3_clientVersion": ok("animica-dev/0.0.0 (stub)"); return

        err(-32601,"Method not found")

def auto_miner():
    while True:
        time.sleep(1)
        with lock:
            if state["auto_mine"]:
                state["height"] += 1; save_state()

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--network", default="testnet")
    p.add_argument("--chain-id", dest="chain_id", default="0xa11ca")
    p.add_argument("--datadir", default=str(Path.home()/".animica"/"testnet"))
    p.add_argument("--rpc-addr", default="0.0.0.0")
    p.add_argument("--rpc-port", type=int, default=8545)
    p.add_argument("--ws-port", type=int, default=8546)
    p.add_argument("--p2p-port", type=int, default=30333)
    p.add_argument("--http", action="store_true")
    p.add_argument("--ws", action="store_true")
    p.add_argument("--allow-cors", action="store_true")
    p.add_argument("--auto-mine", action="store_true")
    p.add_argument("--nat", default=None, help="public IP (accepted & ignored by shim)")
    return p.parse_args()

def main():
    args = parse_args()
    with lock: state["chain_id"] = args.chain_id
    load_state(args.datadir)
    t = threading.Thread(target=auto_miner, daemon=True); t.start()
    httpd = HTTPServer((args.rpc_addr, args.rpc_port), Handler)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: os._exit(0))
    print(f"[shim] RPC http://{args.rpc_addr}:{args.rpc_port} chainId={state['chain_id']} height={state['height']}")
    httpd.serve_forever()

if __name__ == "__main__":
    main()
