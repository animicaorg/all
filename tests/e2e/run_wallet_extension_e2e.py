# -*- coding: utf-8 -*-
"""
E2E: headless Playwright run against a minimal "extension demo" page.

What this does
--------------
- Starts a tiny local JSON-RPC mock node that accepts:
    • chain.getChainId            → int
    • tx.sendRawTransaction(hex)  → txHash (sha3_256 of payload)
    • tx.getTransactionReceipt(h) → receipt dict (status=1)
- Launches Playwright (Chromium) headless.
- Injects a minimal `window.animica` provider (the extension stand-in) that:
    • animica_requestAccounts → returns one deterministic address
    • animica_sendTransaction → builds toy sign-bytes & "signature", posts rawTx to RPC,
                                then fetches a receipt and returns both results.
- Loads a simple HTML "demo" page (data: URL) and, via page.evaluate, performs:
    • request accounts
    • send a transaction
  and asserts the roundtrip succeeds.

Why this approach?
------------------
Running a real MV3 extension inside headless CI is fragile (needs persistent contexts,
non-headless mode, and packing the built extension). This test still exercises the
same dapp-facing shape (window.animica.request) and a real browser JS environment,
which is what often regresses first, while remaining hermetic and dependable.

How to enable
-------------
Set RUN_E2E_TESTS=1 and ensure Playwright + browsers are installed:
    pip install playwright
    playwright install chromium

Optional env:
• EXT_MOCK_CHAIN_ID   — chain id announced by mock RPC (default: 1337)
• E2E_TIMEOUT         — default step timeout seconds (default: 120)
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional, Tuple

import pytest

from tests.e2e import default_timeout, env, skip_unless_e2e

# ----------------------------- JSON-RPC mock node -----------------------------


def _pick_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Recorder:
    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id
        self.sent: Dict[str, Dict[str, Any]] = {}  # txHash -> receipt
        self.last_method: Optional[str] = None


class _RpcHandler(BaseHTTPRequestHandler):
    server_version = "AnimicaMockRPC/1.0"

    @property
    def rec(self) -> _Recorder:
        return self.server.recorder  # type: ignore[attr-defined]

    def _send(self, obj: Dict[str, Any], code: int = 200) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b""
            doc = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                },
                400,
            )

        if not isinstance(doc, dict) or doc.get("jsonrpc") != "2.0":
            return self._send(
                {
                    "jsonrpc": "2.0",
                    "id": doc.get("id") if isinstance(doc, dict) else None,
                    "error": {"code": -32600, "message": "Invalid Request"},
                },
                400,
            )

        mid = doc.get("id")
        method = str(doc.get("method"))
        params = doc.get("params") or []
        self.rec.last_method = method

        if method == "chain.getChainId":
            return self._send(
                {"jsonrpc": "2.0", "id": mid, "result": self.rec.chain_id}
            )

        if method == "tx.sendRawTransaction":
            if not (
                isinstance(params, list)
                and params
                and isinstance(params[0], str)
                and params[0].startswith("0x")
            ):
                return self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {"code": -32602, "message": "raw tx hex required"},
                    },
                    400,
                )
            raw_hex = params[0]
            try:
                payload = bytes.fromhex(raw_hex[2:])
            except Exception:
                payload = raw_hex.encode("utf-8")
            h = "0x" + hashlib.sha3_256(payload).hexdigest()
            self.rec.sent[h] = {
                "transactionHash": h,
                "status": 1,
                "gasUsed": 21000,
                "logs": [],
                "blockNumber": 1,
            }
            return self._send({"jsonrpc": "2.0", "id": mid, "result": h})

        if method == "tx.getTransactionReceipt":
            if not (isinstance(params, list) and params and isinstance(params[0], str)):
                return self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {"code": -32602, "message": "tx hash required"},
                    },
                    400,
                )
            rec = self.rec.sent.get(params[0])
            return self._send({"jsonrpc": "2.0", "id": mid, "result": rec})

        return self._send(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            },
            404,
        )

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter test logs
        return


def _start_mock_node(
    chain_id: int,
) -> Tuple[str, HTTPServer, threading.Thread, _Recorder]:
    port = _pick_free_port()
    server = HTTPServer(("127.0.0.1", port), _RpcHandler)
    server.recorder = _Recorder(chain_id=chain_id)  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, name="MockRPC", daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}", server, t, server.recorder  # type: ignore[attr-defined]


# ---------------------------------- the test ----------------------------------


@pytest.mark.timeout(300)
def test_run_wallet_extension_e2e_with_playwright_headless():
    skip_unless_e2e()

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        pytest.skip(
            f"Playwright is not available ({exc}). "
            "Install with: pip install playwright && playwright install chromium"
        )

    chain_id = int(env("EXT_MOCK_CHAIN_ID", "1337") or "1337")
    rpc_base, server, thread, rec = _start_mock_node(chain_id)

    # JS snippet that installs a minimal window.animica provider.
    # It talks to our mock RPC via fetch; implements requestAccounts & sendTransaction.
    provider_js = f"""
    (() => {{
      const rpc = "{rpc_base}/rpc";
      async function rpcCall(method, params) {{
        const body = {{ jsonrpc: "2.0", id: 1, method, params }};
        const res = await fetch(rpc, {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(body) }});
        const doc = await res.json();
        if (doc.error) throw new Error(JSON.stringify(doc.error));
        return doc.result;
      }}
      function sha3_256_hex(buf) {{
        // No WebCrypto sha3 in the browser; we don't need it here. The node hashes rawTx.
        // Keep as placeholder if future demo wants a local hash.
        return null;
      }}
      function uvar(n) {{
        const out = [];
        let x = n >>> 0;
        while (true) {{
          const b = x & 0x7f;
          x >>>= 7;
          if (x) out.push(b | 0x80); else {{ out.push(b); break; }}
        }}
        return Uint8Array.from(out);
      }}
      function enc(str) {{ return new TextEncoder().encode(str); }}
      function hex(bytes) {{
        return "0x" + Array.from(bytes).map(b => b.toString(16).padStart(2, "0")).join("");
      }}
      function concat(a, b) {{
        const out = new Uint8Array(a.length + b.length);
        out.set(a, 0); out.set(b, a.length);
        return out;
      }}

      const seed = enc("deterministic-seed-for-tests");
      // derive pseudo address = last 20 bytes of SHA3-256(seed); we just fake with a stable pattern:
      const addr = "0x" + ("aa".repeat(19)) + "bb";  // deterministic placeholder, 20 bytes

      const provider = {{
        request: async (req) => {{
          const method = req && req.method || "";
          const params = req && req.params || [];
          if (method === "animica_requestAccounts") {{
            return [addr];
          }}
          if (method === "animica_chainId") {{
            return await rpcCall("chain.getChainId", []);
          }}
          if (method === "animica_sendTransaction") {{
            const tx = params[0] || {{}};
            const chainId = await rpcCall("chain.getChainId", []);
            // Build tiny "signBytes" (sorted JSON) and toy "signature" (just lengths here)
            const payload = JSON.stringify({{domain: {{domain:"animica/tx-sign", chainId}}, tx}}, Object.keys({{}}).sort());
            const signBytes = enc(payload);
            const sig = enc("sig:" + signBytes.length);
            const raw = concat(enc("RAW"), concat(uvar(signBytes.length), concat(signBytes, concat(uvar(sig.length), sig))));
            const rawHex = hex(raw);
            const txHash = await rpcCall("tx.sendRawTransaction", [rawHex]);
            const receipt = await rpcCall("tx.getTransactionReceipt", [txHash]);
            return {{ txHash, receipt }};
          }}
          throw new Error("Unsupported method: " + method);
        }},
        on: () => {{}},
        removeListener: () => {{}},
        isAnimica: true
      }};
      Object.defineProperty(window, "animica", {{ value: provider, enumerable: true }});
    }})();
    """

    # Minimal HTML demo that a dapp might host.
    demo_html = """data:text/html,
    <html><head><meta charset='utf-8'><title>Animica Demo</title></head>
    <body>
      <button id="connect">Connect</button>
      <button id="send">Send Tx</button>
      <pre id="out"></pre>
      <script>
        const $ = (id) => document.getElementById(id);
        $("connect").onclick = async () => {
          try {
            const accts = await window.animica.request({method:"animica_requestAccounts"});
            $("out").textContent = "accounts: " + JSON.stringify(accts);
          } catch (e) { $("out").textContent = "connect error: " + e; }
        };
        $("send").onclick = async () => {
          try {
            const tx = { from: "0x" + "11".repeat(20), to: "0x" + "22".repeat(20), value: "0x1", gasLimit: 21000, gasPrice: "0x3b9aca00", nonce: 0, tip: 0, data: "0x" };
            const r = await window.animica.request({method:"animica_sendTransaction", params:[tx]});
            $("out").textContent = "txHash: " + r.txHash + "\\nstatus: " + (r.receipt && r.receipt.status);
          } catch (e) { $("out").textContent = "send error: " + e; }
        };
      </script>
    </body></html>"""

    from playwright.sync_api import sync_playwright  # type: ignore

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.add_init_script(provider_js)
            page.goto(demo_html)

            # Click connect and assert output
            page.click("#connect")
            page.wait_for_timeout(200)  # brief UI settle
            out1 = page.text_content("#out") or ""
            assert "accounts:" in out1 and "0x" in out1 and len(out1) > 10

            # Click send and assert outputs
            page.click("#send")
            # poll a little for receipt to appear in text
            page.wait_for_timeout(400)
            out2 = page.text_content("#out") or ""
            assert "txHash:" in out2 and "status: 1" in out2

            # Also exercise direct programmatic calls (dapp-less)
            res = page.evaluate(
                """async () => {
              const tx = { from: "0x" + "33".repeat(20), to: "0x" + "44".repeat(20), value: "0x2", gasLimit: 21000, gasPrice: "0x3b9aca00", nonce: 0, tip: 0, data: "0x" };
              return await window.animica.request({method:"animica_sendTransaction", params:[tx]});
            }"""
            )
            assert (
                isinstance(res, dict)
                and isinstance(res.get("txHash"), str)
                and res["txHash"].startswith("0x")
            )
            assert res.get("receipt", {}).get("status") == 1

            ctx.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
