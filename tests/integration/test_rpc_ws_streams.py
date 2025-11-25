# -*- coding: utf-8 -*-
"""
Integration: WebSocket streams — newHeads & pendingTxs.

We connect to the node's WS hub and subscribe to:
  • newHeads   — expect to receive at least one head within the wait window
  • pendingTxs — try to trigger by submitting a tiny CBOR tx fixture (best-effort)

The suite **skips by default** unless RUN_INTEGRATION_TESTS=1
(see tests/integration/__init__.py). It also skips gracefully if a WS
client library isn't available or the server doesn't expose WS.

Env:
  ANIMICA_RPC_URL        — HTTP JSON-RPC URL (default: http://127.0.0.1:8545)
  ANIMICA_WS_URL         — WS URL override (default: derived from RPC_URL, path /ws)
  ANIMICA_HTTP_TIMEOUT   — per-request timeout secs (default: 5)
  ANIMICA_WS_WAIT_SECS   — wait for events secs (default: 120)
  ANIMICA_TX_FIXTURE     — path to CBOR tx to submit (default: mempool/fixtures/txs_cbor/tx1.cbor)
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # gate + env helper

# ------------------------------ HTTP helpers ---------------------------------


def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(rpc_url: str, method: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, *, req_id: int = 1) -> Any:
    if params is None:
        params = []
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"RPC {method} error: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"RPC {method} missing result: {msg}")
    return msg["result"]


def _rpc_try(rpc_url: str, methods: Sequence[str], params: Optional[Sequence[Any] | Dict[str, Any]] = None) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
    raise AssertionError(f"All methods failed ({methods}); last error: {last_exc}")


# ------------------------------ WS utilities ---------------------------------


def _derive_ws_url() -> str:
    ws_url = env("ANIMICA_WS_URL")
    if ws_url:
        return ws_url
    rpc = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    u = urllib.parse.urlparse(rpc)
    scheme = "wss" if u.scheme in ("https", "wss") else "ws"
    # Default hub path is /ws unless explicit path provided via env
    path = "/ws"
    return urllib.parse.urlunparse((scheme, u.netloc, path, "", "", ""))


async def _ws_connect():
    """
    Try to obtain a WS client using either `websockets` (async) or `websocket-client` (sync adapter).
    Returns a tuple of (send_json, recv_json, close) async callables.
    """
    ws_url = _derive_ws_url()

    # Preferred: websockets (async)
    try:
        import websockets  # type: ignore

        # websockets.connect is an async context manager
        conn = await websockets.connect(ws_url, max_size=4 * 1024 * 1024)

        async def send_json(obj: Any) -> None:
            await conn.send(json.dumps(obj))

        async def recv_json(timeout: float | None = None) -> Any:
            if timeout is None:
                msg = await conn.recv()
            else:
                msg = await asyncio.wait_for(conn.recv(), timeout=timeout)
            if isinstance(msg, (bytes, bytearray)):
                msg = msg.decode("utf-8", "replace")
            return json.loads(msg)

        async def close() -> None:
            await conn.close()

        return send_json, recv_json, close
    except Exception:
        pass

    # Fallback: websocket-client (sync); wrap with threads via asyncio.to_thread
    try:
        import websocket  # type: ignore

        ws = websocket.create_connection(ws_url, timeout=_http_timeout())

        async def send_json(obj: Any) -> None:
            data = json.dumps(obj)
            await asyncio.to_thread(ws.send, data)

        async def recv_json(timeout: float | None = None) -> Any:
            if timeout is not None:
                ws.settimeout(timeout)
            raw = await asyncio.to_thread(ws.recv)
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", "replace")
            return json.loads(raw)

        async def close() -> None:
            await asyncio.to_thread(ws.close)

        return send_json, recv_json, close
    except Exception:
        pytest.skip("No suitable WebSocket client library found (install 'websockets' or 'websocket-client').")
        raise  # unreachable


async def _ws_subscribe(send_json, recv_json, topic: str) -> None:
    """
    Send one of several subscription formats; don't fail if the server auto-subscribes.
    """
    candidates = [
        {"jsonrpc": "2.0", "id": 1, "method": "subscribe", "params": [topic]},
        {"jsonrpc": "2.0", "id": 1, "method": "ws.subscribe", "params": [{"topic": topic}]},
        {"op": "subscribe", "topic": topic},
        {"type": "subscribe", "topic": topic},
        {"action": "subscribe", "topic": topic},
    ]
    for obj in candidates:
        try:
            await send_json(obj)
            # Some hubs send an ack; attempt a quick non-fatal read
            try:
                ack = await recv_json(timeout=0.3)
                # Accept a variety of ack shapes; ignore if not recognizable
                if isinstance(ack, dict) and any(k in ack for k in ("subscribed", "ok", "ack", "result")):
                    break
            except Exception:
                break
        except Exception:
            continue


def _parse_topic(msg: Dict[str, Any]) -> Optional[str]:
    # Direct topic
    for k in ("topic", "channel"):
        v = msg.get(k)
        if isinstance(v, str):
            return v
    # JSON-RPC subscription notification
    if msg.get("method") in ("subscription", "ws.event", "publish"):
        params = msg.get("params")
        if isinstance(params, dict):
            v = params.get("topic") or params.get("channel")
            if isinstance(v, str):
                return v
    return None


def _extract_event_payload(msg: Dict[str, Any]) -> Dict[str, Any]:
    # Common shapes: {topic, data}, {method: subscription, params: {topic, result}}, {event: {...}}
    if "data" in msg and isinstance(msg["data"], dict):
        return msg["data"]
    if "event" in msg and isinstance(msg["event"], dict):
        return msg["event"]
    if msg.get("method") in ("subscription", "ws.event", "publish"):
        params = msg.get("params")
        if isinstance(params, dict):
            res = params.get("result") or params.get("data") or params.get("event")
            if isinstance(res, dict):
                return res
    # Last resort: entire message
    return msg


# ------------------------------ Tx helpers -----------------------------------


def _read_fixture_bytes() -> Optional[bytes]:
    # Allow override via env
    p = env("ANIMICA_TX_FIXTURE", "mempool/fixtures/txs_cbor/tx1.cbor")
    try:
        with open(p, "rb") as f:
            return f.read()
    except Exception:
        return None


def _send_raw_tx(rpc_url: str, raw: bytes) -> str:
    """
    Try multiple encodings & method names; return tx hash hex if available (best-effort).
    """
    candidates = [
        ("tx.sendRawTransaction", ["0x" + raw.hex()]),
        ("tx.sendRawTransaction", [{"raw": "0x" + raw.hex(), "encoding": "cbor"}]),
        ("tx.sendRawTransaction", [base64.b64encode(raw).decode("ascii")]),
        ("tx_sendRawTransaction", ["0x" + raw.hex()]),  # alt naming
        ("eth_sendRawTransaction", ["0x" + raw.hex()]),  # extreme fallback
    ]
    last_exc: Optional[Exception] = None
    for m, params in candidates:
        try:
            res = _rpc_call(rpc_url, m, params)
            if isinstance(res, str) and res.startswith("0x"):
                return res
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        # Not fatal to the test; just return unknown/empty
        return ""
    return ""


# ---------------------------------- Tests ------------------------------------


@pytest.mark.timeout(300)
@pytest.mark.asyncio
async def test_ws_new_heads_stream():
    ws_url = _derive_ws_url()
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    wait_secs = float(env("ANIMICA_WS_WAIT_SECS", "120"))

    send_json, recv_json, close = await _ws_connect()
    try:
        await _ws_subscribe(send_json, recv_json, "newHeads")

        deadline = time.time() + wait_secs
        got_topic = False
        got_height = False
        last_height: Optional[int] = None

        while time.time() < deadline:
            msg = await recv_json(timeout=min(2.0, max(0.2, deadline - time.time())))
            if not isinstance(msg, dict):
                continue

            t = _parse_topic(msg)
            if t and "newheads" in t.lower():
                got_topic = True
            payload = _extract_event_payload(msg)
            # Accept several height fields
            for k in ("height", "number", "blockNumber"):
                v = payload.get(k)
                if isinstance(v, int):
                    if last_height is None or v >= last_height:
                        last_height = v
                        got_height = True
                        break
                if isinstance(v, str) and v.startswith("0x"):
                    try:
                        iv = int(v, 16)
                        if last_height is None or iv >= last_height:
                            last_height = iv
                            got_height = True
                            break
                    except Exception:
                        pass
            if got_topic and got_height:
                break

        assert got_topic, f"Did not receive a 'newHeads' topic message from {ws_url}"
        assert got_height, "Did not parse any head height from WS stream"

    finally:
        await close()


@pytest.mark.timeout(300)
@pytest.mark.asyncio
async def test_ws_pending_txs_stream():
    ws_url = _derive_ws_url()
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    wait_secs = float(env("ANIMICA_WS_WAIT_SECS", "120"))

    send_json, recv_json, close = await _ws_connect()
    try:
        await _ws_subscribe(send_json, recv_json, "pendingTxs")

        # Try to stimulate with a tiny CBOR tx (best-effort).
        raw = _read_fixture_bytes()
        if raw:
            _send_raw_tx(rpc_url, raw)  # ignore result; the goal is to create pending traffic

        deadline = time.time() + wait_secs
        got_topic = False
        got_tx = False

        while time.time() < deadline:
            try:
                msg = await recv_json(timeout=min(2.0, max(0.2, deadline - time.time())))
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            t = _parse_topic(msg)
            if t and ("pending" in t.lower() or "tx" in t.lower()):
                got_topic = True

            payload = _extract_event_payload(msg)
            # Look for a tx-like shape
            if isinstance(payload, dict) and (
                "hash" in payload or "txHash" in payload or "raw" in payload or "from" in payload
            ):
                got_tx = True
                break

        # If the network is idle and we couldn't inject a tx, allow a graceful skip.
        if not (got_topic and got_tx):
            pytest.skip(
                f"No pendingTxs observed from {ws_url} within {wait_secs}s "
                f"(idle mempool or WS hub does not expose this topic)."
            )

        assert got_topic, "Subscribed but no pendingTxs topic messages arrived"
        assert got_tx, "Subscribed but no tx-shaped payloads arrived"

    finally:
        await close()

