from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

# ---- best-effort version discovery -------------------------------------------------------------

def _try_import_version(mod: str, attr: str = "__version__") -> str:
    try:
        m = __import__(mod, fromlist=[attr])
        v = getattr(m, attr, None)
        if isinstance(v, str):
            return v
    except Exception:
        pass
    return "0.0.0"

_VERSIONS = {
    "p2p": _try_import_version("p2p.version"),
    "core": _try_import_version("core.version"),
    "consensus": _try_import_version("consensus.version"),
    "rpc": _try_import_version("rpc.version"),
}

def _default_agent() -> str:
    return f"animica-core/{_VERSIONS['core']} p2p/{_VERSIONS['p2p']}"

# ---- caps --------------------------------------------------------------------------------------

_DEFAULT_CAPS = [
    "blocks/get",
    "blocks/announce",
    "txs/gossip",
    "headers/get",
    "poies/v1",
]

def _normalize_caps(caps: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for c in (caps or _DEFAULT_CAPS):
        c = (c or "").strip().lower()
        if not c:
            continue
        if c not in out:
            out.append(c)
    return out

# ---- wire (optional) ---------------------------------------------------------------------------

# We try to use the canonical wire encoding if available; otherwise fall back to a tiny JSON line.
try:
    from p2p.wire.message_ids import MSG_IDENTIFY, MSG_IDENTIFY_RESP  # type: ignore
except Exception:  # pragma: no cover
    MSG_IDENTIFY = 0x01
    MSG_IDENTIFY_RESP = 0x02

try:
    from p2p.wire.encoding import encode, decode  # type: ignore
except Exception:  # pragma: no cover
    encode = decode = None  # type: ignore

# ---- data model --------------------------------------------------------------------------------

@dataclass
class IdentifyRequest:
    versions: Dict[str, str]
    caps: List[str]
    agent: str
    height: int
    timestamp: float
    network_id: Optional[str] = None  # e.g., "animica:1"

@dataclass
class IdentifyResponse:
    peer_id: str
    versions: Dict[str, str]
    caps: List[str]
    agent: str
    height: int
    timestamp: float
    network_id: Optional[str] = None
    # Optional diagnostics
    addr: Optional[str] = None
    rtt_ms: Optional[float] = None

# ---- exceptions --------------------------------------------------------------------------------

class IdentifyError(Exception):
    pass


class IdentifyService:
    """
    Minimal IDENTIFY background service.

    The full node service wires this into the connection manager to keep a
    cache of peer metadata fresh. For devnet we only need a stub that:
      - Exposes a `run()` loop so callers can schedule it as a task.
      - Provides helpers to describe the local node and to actively identify
        a peer connection when needed.
    """

    def __init__(
        self,
        connmgr: Any,
        peer_id: bytes | str,
        version: str = "0.0.0",
        *,
        head_reader: Any = None,
        alg_policy_root: Optional[str] = None,
        caps: Optional[List[str]] = None,
        agent: Optional[str] = None,
    ) -> None:
        self.connmgr = connmgr
        self.peer_id = peer_id.hex() if isinstance(peer_id, (bytes, bytearray)) else str(peer_id)
        self.version = version
        self.head_reader = head_reader
        self.alg_policy_root = alg_policy_root
        self.caps = _normalize_caps(caps)
        self.agent = agent or _default_agent()
        self._running = False

    async def run(self) -> None:
        """Background noop loop (kept for interface compatibility)."""

        self._running = True
        while self._running:
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        self._running = False

    def describe_local(self, *, addr: Optional[str] = None, rtt_ms: Optional[float] = None) -> Dict[str, Any]:
        """
        Return an IdentifyResponse-like dict for this node. This is used by
        servers when answering IDENTIFY requests.
        """

        height = 0
        if self.head_reader:
            with contextlib.suppress(Exception):
                height = int(getattr(self.head_reader, "height", 0))

        network_id = self.alg_policy_root
        return asdict(
            IdentifyResponse(
                peer_id=self.peer_id,
                versions={"p2p": _VERSIONS.get("p2p", "0.0.0")},
                caps=self.caps,
                agent=self.agent,
                height=height,
                timestamp=time.time(),
                network_id=network_id,
                addr=addr,
                rtt_ms=rtt_ms,
            )
        )

    async def identify_peer(self, conn: Any, timeout: float = 5.0) -> Dict[str, Any]:
        """Thin wrapper around ``perform_identify`` for convenience."""

        return await perform_identify(
            conn,
            timeout=timeout,
            local_caps=self.caps,
            network_id=self.alg_policy_root,
            agent=self.agent,
        )

# ---- public API --------------------------------------------------------------------------------

async def perform_identify(
    conn: Any,
    timeout: float = 5.0,
    *,
    local_caps: Optional[List[str]] = None,
    local_height: int = 0,
    network_id: Optional[str] = None,
    agent: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Perform an IDENTIFY exchange with a remote peer connection.

    The function is deliberately defensive and supports multiple transports:

      1) If the connection exposes an 'identify' coroutine method, call it directly.
      2) Else, if it exposes a generic 'request(op, data, timeout=..)' method, use op='IDENTIFY'.
      3) Else, if the p2p.wire.* stack is available and the connection exposes 'send_frame'/'recv_frame',
         use MSG_IDENTIFY / MSG_IDENTIFY_RESP frames with canonical encoding.
      4) Else, if the connection looks like an asyncio Stream (has 'write' and 'readuntil'),
         fall back to a single JSON line exchange.

    On success, returns a normalized dict with:
        {peer_id, caps, agent, height, versions, network_id}
    """
    req = IdentifyRequest(
        versions=dict(_VERSIONS),
        caps=_normalize_caps(local_caps),
        agent=agent or _default_agent(),
        height=max(0, int(local_height)),
        timestamp=time.time(),
        network_id=network_id,
    )

    # 1) Direct identify(conn, req)
    if hasattr(conn, "identify") and callable(getattr(conn, "identify")):
        try:
            return await asyncio.wait_for(_via_direct(conn, req), timeout=timeout)
        except Exception as e:
            raise IdentifyError(f"identify() failed: {e}") from e

    # 2) Generic request("IDENTIFY", req)
    if hasattr(conn, "request") and callable(getattr(conn, "request")):
        try:
            return await asyncio.wait_for(_via_request(conn, req), timeout=timeout)
        except Exception as e:
            raise IdentifyError(f"request('IDENTIFY') failed: {e}") from e

    # 3) Canonical wire frames (if available)
    if encode and hasattr(conn, "send_frame") and hasattr(conn, "recv_frame"):
        try:
            return await asyncio.wait_for(_via_wire_frames(conn, req), timeout=timeout)
        except Exception as e:
            # do not abort yet; attempt JSON fallback
            pass

    # 4) Minimal JSON line fallback
    if hasattr(conn, "write") and hasattr(conn, "readuntil"):
        try:
            return await asyncio.wait_for(_via_json_line(conn, req), timeout=timeout)
        except Exception as e:
            raise IdentifyError(f"line-identify failed: {e}") from e

    # 5) Last resort: synthesize from connection metadata
    peer_id = getattr(conn, "peer_id", None) or getattr(conn, "remote_addr", "unknown")
    if not peer_id:
        raise IdentifyError("Cannot determine peer identity; unsupported connection type")
    return {
        "peer_id": str(peer_id),
        "versions": dict(_VERSIONS),
        "caps": _normalize_caps(local_caps),
        "agent": agent or _default_agent(),
        "height": max(0, int(local_height)),
        "network_id": network_id,
    }

# ---- strategy implementations ------------------------------------------------------------------

async def _via_direct(conn: Any, req: IdentifyRequest) -> Dict[str, Any]:
    resp = await conn.identify(asdict(req))
    return _validate_response(resp, conn)

async def _via_request(conn: Any, req: IdentifyRequest) -> Dict[str, Any]:
    resp = await conn.request("IDENTIFY", asdict(req))
    return _validate_response(resp, conn)

async def _via_wire_frames(conn: Any, req: IdentifyRequest) -> Dict[str, Any]:
    # Expect send_frame(msg_id: int, payload: bytes) / recv_frame() -> (msg_id:int, payload:bytes)
    payload = encode(asdict(req))  # type: ignore
    await conn.send_frame(MSG_IDENTIFY, payload)
    msg_id, body = await conn.recv_frame()
    if msg_id != MSG_IDENTIFY_RESP:
        raise IdentifyError(f"unexpected msg_id {msg_id:#x} (wanted {MSG_IDENTIFY_RESP:#x})")
    resp = decode(body)  # type: ignore
    return _validate_response(resp, conn)

async def _via_json_line(conn: Any, req: IdentifyRequest) -> Dict[str, Any]:
    """
    Ultra-minimal fallback: newline-delimited JSON exchange.

    Format:
       {"op":"IDENTIFY","data":{...}}\n
       {"op":"IDENTIFY/RESP","data":{...}}\n
    """
    import json

    line = json.dumps({"op": "IDENTIFY", "data": asdict(req)}, separators=(",", ":")).encode("utf-8") + b"\n"
    conn.write(line)            # type: ignore[attr-defined]
    await conn.drain()          # type: ignore[attr-defined]
    raw = await conn.readuntil(b"\n")  # type: ignore[attr-defined]
    obj = json.loads(raw.decode("utf-8", "strict"))
    if not isinstance(obj, dict) or obj.get("op") not in ("IDENTIFY/RESP", "IDENTIFY_RESP"):
        raise IdentifyError("invalid identify line response")
    return _validate_response(obj.get("data", {}), conn)

# ---- validation / normalization ----------------------------------------------------------------

def _validate_response(resp: Any, conn: Any) -> Dict[str, Any]:
    if not isinstance(resp, dict):
        raise IdentifyError("malformed identify response")

    peer_id = str(resp.get("peer_id") or getattr(conn, "peer_id", "") or getattr(conn, "remote_addr", ""))
    if not peer_id:
        raise IdentifyError("identify response lacks peer_id")

    # versions
    versions = resp.get("versions") or {}
    if not isinstance(versions, dict):
        versions = {}

    # height
    try:
        height = int(resp.get("height", 0))
        if height < 0:
            height = 0
    except Exception:
        height = 0

    caps = resp.get("caps")
    if not isinstance(caps, list):
        caps = []
    caps = _normalize_caps(caps)

    agent = resp.get("agent")
    if not isinstance(agent, str) or not agent:
        agent = "unknown"

    network_id = resp.get("network_id")
    if network_id is not None:
        network_id = str(network_id)

    # Optional diagnostics
    rtt_ms = resp.get("rtt_ms")
    try:
        rtt_ms = float(rtt_ms) if rtt_ms is not None else None
    except Exception:
        rtt_ms = None

    addr = resp.get("addr")
    if addr is not None:
        addr = str(addr)

    out = {
        "peer_id": peer_id,
        "versions": versions,
        "caps": caps,
        "agent": agent,
        "height": height,
        "network_id": network_id,
    }
    if rtt_ms is not None:
        out["rtt_ms"] = rtt_ms
    if addr is not None:
        out["addr"] = addr
    return out

# ---- server-side helper (optional) --------------------------------------------------------------

async def handle_identify_request(
    req: Dict[str, Any],
    *,
    peer_id: str,
    current_height: int,
    network_id: Optional[str],
    caps: Optional[List[str]] = None,
    agent: Optional[str] = None,
    addr: Optional[str] = None,
    rtt_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Helper for servers implementing IDENTIFY. Given a parsed IdentifyRequest,
    produce an IdentifyResponse dict (ready to encode or return via RPC).
    """
    # (Optionally validate req; we trust minimally here)
    return asdict(
        IdentifyResponse(
            peer_id=str(peer_id),
            versions=dict(_VERSIONS),
            caps=_normalize_caps(caps),
            agent=agent or _default_agent(),
            height=max(0, int(current_height)),
            timestamp=time.time(),
            network_id=network_id,
            addr=addr,
            rtt_ms=rtt_ms,
        )
    )
