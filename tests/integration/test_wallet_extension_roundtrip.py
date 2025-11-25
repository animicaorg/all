# -*- coding: utf-8 -*-
"""
Integration (headless): "wallet-extension" roundtrip via a mock extension/provider.

What this test proves (without launching a real MV3 extension/Chrome):
- A dapp-like client can "connect" to a mock wallet provider and obtain an account.
- The provider builds a sign-bytes payload (domain-separated), "signs" it deterministically,
  constructs a raw-transaction blob (hex), and POSTs JSON-RPC tx.sendRawTransaction.
- The response returns a tx hash, and a follow-up receipt lookup succeeds.

We don't validate real CBOR or PQ signatures here. Instead we stand up a tiny local JSON-RPC
stub server that accepts the call, captures/validates the envelope shapes, and returns deterministic
fake results. This gives us a headless, repeatable contract-test for the browser extension glue.

Environment
-----------
• RUN_INTEGRATION_TESTS=1        — enable integration tests (required)
• (optional) EXT_MOCK_CHAIN_ID   — chain id the mock node advertises (default: 1337)
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional, Tuple
import urllib.request
import urllib.parse

import pytest

from tests.integration import env  # gating helper


# ----------------------------- JSON-RPC stub node -----------------------------

def _pick_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _RpcRecorder:
    def __init__(self, chain_id: int = 1337):
        self.last_body: Optional[Dict[str, Any]] = None
        self.chain_id = chain_id
        self.sent_hashes: Dict[str, Dict[str, Any]] = {}  # txHash -> receipt


class _RpcHandler(BaseHTTPRequestHandler):
    server_version = "AnimicaMockRPC/1.0"

    # We'll stuff a recorder onto the HTTPServer instance
    @property
    def rec(self) -> _RpcRecorder:
        return self.server.recorder  # type: ignore[attr-defined]

    def _read_json(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            doc = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if isinstance(doc, dict):
            self.rec.last_body = doc
            return doc
        return None

    def _send_json(self, obj: Dict[str, Any], code: int = 200) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 (http.server method)
        doc = self._read_json()
        if not doc or doc.get("jsonrpc") != "2.0" or "method" not in doc:
            return self._send_json({"jsonrpc": "2.0", "id": doc.get("id") if isinstance(doc, dict) else None,
                                    "error": {"code": -32600, "message": "Invalid Request"}}, 400)

        mid = doc.get("id")
        method = str(doc.get("method"))
        params = doc.get("params") or []

        # Minimal router
        if method == "chain.getChainId":
            return self._send_json({"jsonrpc": "2.0", "id": mid, "result": self.rec.chain_id})

        if method == "tx.sendRawTransaction":
            if not isinstance(params, list) or not params:
                return self._send_json({"jsonrpc": "2.0", "id": mid,
                                        "error": {"code": -32602, "message": "Missing raw tx param"}}, 400)
            raw_hex = params[0]
            if not (isinstance(raw_hex, str) and raw_hex.startswith("0x") and len(raw_hex) > 2):
                return self._send_json({"jsonrpc": "2.0", "id": mid,
                                        "error": {"code": -32602, "message": "raw tx must be hex string"}}, 400)
            # Deterministic tx hash = keccak-like (sha3_256 for test) of raw bytes
            try:
                payload = bytes.fromhex(raw_hex[2:])
            except Exception:
                payload = raw_hex.encode("utf-8")
            h = "0x" + hashlib.sha3_256(payload).hexdigest()
            # Create a synthetic receipt (SUCCESS)
            self.rec.sent_hashes[h] = {
                "transactionHash": h,
                "status": 1,
                "gasUsed": 21000,
                "logs": [],
                "blockNumber": 1,
            }
            return self._send_json({"jsonrpc": "2.0", "id": mid, "result": h})

        if method == "tx.getTransactionReceipt":
            if not isinstance(params, list) or not params:
                return self._send_json({"jsonrpc": "2.0", "id": mid,
                                        "error": {"code": -32602, "message": "Missing tx hash"}}, 400)
            h = str(params[0])
            rec = self.rec.sent_hashes.get(h)
            return self._send_json({"jsonrpc": "2.0", "id": mid, "result": rec})

        # Unknown method
        return self._send_json({"jsonrpc": "2.0", "id": mid,
                                "error": {"code": -32601, "message": f"Method not found: {method}"}}, 404)

    # Silence default noisy logs in tests
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return


def _start_mock_node(chain_id: int) -> Tuple[str, HTTPServer, threading.Thread, _RpcRecorder]:
    port = _pick_free_port()
    server = HTTPServer(("127.0.0.1", port), _RpcHandler)
    recorder = _RpcRecorder(chain_id=chain_id)
    server.recorder = recorder  # type: ignore[attr-defined]

    t = threading.Thread(target=server.serve_forever, name="MockNode", daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    return base, server, t, recorder


# ----------------------------- Extension mock (headless) ----------------------

class MockExtensionProvider:
    """
    A tiny, synchronous mock of the MV3 background+provider pipeline.

    - connect(): returns a deterministic account address (hex), chainId.
    - send_transaction(tx): builds sign-bytes, "signs" them (sha3_256), makes JSON-RPC call.
    """

    def __init__(self, rpc_url: str, seed: bytes, chain_id_hint: Optional[int] = None):
        self.rpc_url = rpc_url.rstrip("/")
        self.seed = seed or b"\x00"
        self.addr = self._derive_address(seed)
        self.chain_id_hint = chain_id_hint

    @staticmethod
    def _derive_address(seed: bytes) -> str:
        # Derive a 20-byte "address" as sha3_256(seed) tail (test-only)
        digest = hashlib.sha3_256(seed).digest()
        return "0x" + digest[-20:].hex()

    def _rpc(self, method: str, params: list) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        req = urllib.request.Request(
            url=self.rpc_url + "/rpc",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        if "error" in doc:
            raise RuntimeError(f"RPC error: {doc['error']}")
        return doc.get("result")

    def connect(self) -> Dict[str, Any]:
        chain_id = self.chain_id_hint or int(self._rpc("chain.getChainId", []))
        return {"accounts": [self.addr], "chainId": chain_id}

    @staticmethod
    def _build_sign_bytes(chain_id: int, tx: Dict[str, Any]) -> bytes:
        """
        Mimic a domain-separated "SignBytes" message: json-canon of a small subset.
        Real extension would CBOR-encode canonical SignBytes; for this headless path,
        we hash a stable, sorted JSON view with a prefixed domain.
        """
        domain = {"domain": "animica/tx-sign", "chainId": chain_id}
        # Deterministic JSON: sorted keys, minimal whitespace
        payload = json.dumps({"domain": domain, "tx": tx}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return payload

    def _sign(self, sign_bytes: bytes) -> bytes:
        """
        Stand-in "signature": sha3_256(seed || sign_bytes). In real life, Dilithium3/SPHINCS+.
        """
        h = hashlib.sha3_256()
        h.update(self.seed)
        h.update(sign_bytes)
        return h.digest()

    def _build_raw_tx_hex(self, sign_bytes: bytes, sig: bytes) -> str:
        """
        Pack a toy "raw" blob: b"RAW" || uvarint(len(sign_bytes)) || sign_bytes || uvarint(len(sig)) || sig
        and hex-encode it. The mock node only checks that it's hex; we keep it structured for sanity.
        """
        def uvar(n: int) -> bytes:
            out = bytearray()
            x = n
            while True:
                b = x & 0x7F
                x >>= 7
                if x:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    break
            return bytes(out)

        blob = b"RAW" + uvar(len(sign_bytes)) + sign_bytes + uvar(len(sig)) + sig
        return "0x" + blob.hex()

    def send_transaction(self, tx: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        meta = self.connect()
        chain_id = int(meta["chainId"])
        sign_bytes = self._build_sign_bytes(chain_id, tx)
        sig = self._sign(sign_bytes)
        raw_hex = self._build_raw_tx_hex(sign_bytes, sig)
        tx_hash = str(self._rpc("tx.sendRawTransaction", [raw_hex]))
        # Optional: wait for receipt
        receipt = self._rpc("tx.getTransactionReceipt", [tx_hash])
        return tx_hash, receipt


# ------------------------------------ test ------------------------------------

@pytest.mark.timeout(120)
def test_wallet_extension_headless_roundtrip_via_mock_provider():
    if env("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run headless wallet-extension mock test.")

    chain_id = int(env("EXT_MOCK_CHAIN_ID", "1337"))
    base, server, thread, recorder = _start_mock_node(chain_id)

    try:
        # "Dapp" creates a provider bound to the mock node URL
        seed = b"deterministic-seed-for-tests"
        provider = MockExtensionProvider(rpc_url=base, seed=seed)

        # Connect (accounts + chainId)
        meta = provider.connect()
        assert isinstance(meta.get("accounts"), list) and len(meta["accounts"]) == 1
        assert meta["accounts"][0].startswith("0x") and len(meta["accounts"][0]) == 42  # 20-byte hex
        assert int(meta["chainId"]) == chain_id

        # Build a tiny transfer-like tx shape (not consensus-critical here)
        tx = {
            "from": meta["accounts"][0],
            "to": "0x" + "12" * 20,
            "nonce": 0,
            "value": "0x1",
            "gasPrice": "0x3b9aca00",  # 1 gwei
            "gasLimit": 21000,
            "tip": 0,
            "data": "0x",
        }

        tx_hash, receipt = provider.send_transaction(tx)
        # Hash looks hex-like and non-empty
        assert isinstance(tx_hash, str) and tx_hash.startswith("0x") and len(tx_hash) == 66

        # Receipt came back and indicates success from our mock
        assert isinstance(receipt, dict)
        assert receipt.get("transactionHash") == tx_hash
        assert int(receipt.get("status", 0)) == 1

        # Inspect the last JSON-RPC body captured by the mock node
        body = recorder.last_body or {}
        assert body.get("method") in ("tx.sendRawTransaction", "tx.getTransactionReceipt")
        if body.get("method") == "tx.sendRawTransaction":
            params = body.get("params") or []
            assert isinstance(params, list) and params and isinstance(params[0], str) and params[0].startswith("0x")
            # a tiny sanity check: our toy raw format starts with 0x524157 (ASCII "RAW")
            raw_hex = params[0]
            raw_bytes = bytes.fromhex(raw_hex[2:])
            assert raw_bytes[:3] == b"RAW", "Raw-transaction envelope did not start with sentinel b'RAW'"

    finally:
        server.shutdown()
        server.server_close()

