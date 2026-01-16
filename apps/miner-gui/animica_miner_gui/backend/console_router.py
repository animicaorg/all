"""RPC-backed console router for the Miner GUI."""

from __future__ import annotations

import ast
import json
import logging
import shlex
import sys
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from animica_miner_gui.core.localnode import LocalRpcClient
from animica_miner_gui.core.localnode.rpc import LocalRpcError

logger = logging.getLogger(__name__)


@dataclass
class ConsoleResult:
    ok: bool
    output: str
    method: Optional[str] = None
    error: Optional[str] = None


_RPC_CLIENT: Optional[LocalRpcClient] = None

HELP_TEXT = """Available commands:
  status
  animica node status
  peers
  animica peer list
  animica peer count
  animica peer add <multiaddr>
  animica peer bootstrap [multiaddr]
  animica debug sync-dump
  animica sync force
  animica mempool list
  animica wallet show <address>

Raw RPC:
  rpc <method> [json-params]
  rpc chain.getHead []
  rpc state.getBalance ["0x..."]

Examples:
  animica node status
  animica sync force
  rpc chain.getHead []
"""


def set_rpc_client(rpc_client: Optional[LocalRpcClient]) -> None:
    """Set the LocalRpcClient used by the console router."""
    global _RPC_CLIENT
    _RPC_CLIENT = rpc_client


def run_console_command(text: str) -> ConsoleResult:
    """Run a console command via local RPC."""
    packaged_mode = is_packaged_mode()
    logger.debug(f"Console command received (packaged={packaged_mode})")

    if not text or not text.strip():
        return ConsoleResult(ok=False, output="No command provided.")

    if _RPC_CLIENT is None:
        return ConsoleResult(ok=False, output="RPC client is not available. Start the node first.")

    command = text.strip()

    if _contains_disallowed_args(command):
        return ConsoleResult(
            ok=False,
            output="Refusing to accept --rpc-url/--rpc-token arguments. The console only uses localhost RPC.",
        )

    lowered = command.lower()
    if lowered in {"help", "?"} or lowered.startswith("animica help"):
        return ConsoleResult(ok=True, output=HELP_TEXT)

    if lowered.startswith("rpc "):
        method, params_or_error = _parse_rpc_command(command)
        if method is None:
            return ConsoleResult(ok=False, output=params_or_error or "Invalid rpc command.")
        return _execute_rpc(method, params_or_error)

    if lowered.startswith("animica "):
        return _handle_animica_command(command)

    # Backwards-compatible: accept bare commands as animica subcommands.
    return _handle_animica_command(f"animica {command}")


def _handle_animica_command(command: str) -> ConsoleResult:
    try:
        tokens = shlex.split(command)
    except Exception as exc:
        return ConsoleResult(ok=False, output=f"Failed to parse command: {exc}")

    if not tokens:
        return ConsoleResult(ok=False, output="No command provided.")

    if tokens[0].lower() == "animica":
        tokens = tokens[1:]

    if not tokens:
        return ConsoleResult(ok=True, output=HELP_TEXT)

    verb = tokens[0].lower()
    sub = tokens[1].lower() if len(tokens) > 1 else ""
    remainder = tokens[2:]

    if _RPC_CLIENT:
        _RPC_CLIENT.ensure_methods()

    if verb in {"status"} or (verb == "node" and sub == "status"):
        return _execute_status()

    if verb in {"peers"}:
        return _execute_peers()

    if verb == "sync" and sub == "force":
        return _execute_rpc("sync.force", [])

    if verb == "debug" and sub == "sync-dump":
        return _execute_sync_dump()

    if verb == "mempool" and sub == "list":
        return _execute_rpc("mempool.list", [])

    if verb == "wallet" and sub in {"show", "balance"}:
        if not remainder:
            return ConsoleResult(ok=False, output="wallet show requires an address.")
        return _execute_rpc("state.getBalance", [remainder[0]])

    if verb == "peer":
        if sub in {"list", "ls"}:
            return _execute_peers()
        if sub == "count":
            return _execute_rpc("net.peerCount", [])
        if sub == "bootstrap":
            return _execute_peer_bootstrap(remainder)
        if sub == "add":
            if not remainder:
                return ConsoleResult(ok=False, output="peer add requires a multiaddr.")
            return _execute_peer_add(remainder[0])
        if sub == "remove":
            if not remainder:
                return ConsoleResult(ok=False, output="peer remove requires a peer id.")
            return _execute_rpc("p2p.removePeer", [remainder[0]])
        if sub == "info":
            if not remainder:
                return ConsoleResult(ok=False, output="peer info requires a peer id.")
            return _execute_rpc("p2p.getPeerInfo", [remainder[0]])

    return ConsoleResult(ok=False, output=f"Unknown command: {command}\n\n{HELP_TEXT}")


def _parse_rpc_command(command: str) -> Tuple[Optional[str], Any]:
    payload = command.strip()[4:].strip()
    if not payload:
        return None, "rpc command requires a method name."

    parts = payload.split(maxsplit=1)
    method = parts[0]
    params_text = parts[1].strip() if len(parts) > 1 else ""

    if not params_text:
        return method, []

    try:
        params = json.loads(params_text)
    except json.JSONDecodeError:
        return None, "Invalid JSON params. Use [] or {} syntax."

    if not isinstance(params, (list, dict)):
        return None, "RPC params must be a JSON array or object."

    return method, params


def _execute_rpc(method: str, params: Any) -> ConsoleResult:
    if _RPC_CLIENT is None:
        return ConsoleResult(ok=False, output="RPC client is not available.")

    try:
        result = _RPC_CLIENT.call(method, params)
        formatted = _format_result(method, params, result)
        return ConsoleResult(ok=True, output=formatted, method=method)
    except Exception as exc:
        error_text = _format_error(method, exc)
        logger.debug(f"Console command failed: {exc}")
        return ConsoleResult(ok=False, output=error_text, method=method, error=str(exc))


def _execute_status() -> ConsoleResult:
    if _RPC_CLIENT is None:
        return ConsoleResult(ok=False, output="RPC client is not available.")

    head_method, head = _call_first_supported(
        ["chain.getHead", "chain.getTip", "chain.getHeight"],
        [],
    )
    if head_method == "chain.getHeight":
        head = {"height": head} if head is not None else {}
    if not head_method:
        head = {
            "unavailable": True,
            "reason": "no known chain head RPC methods enabled",
        }

    sync_method, sync = _call_first_supported(
        ["sync.getStatus", "sync.dump", "sync.status"],
        [],
    )
    peers_method, peers = _call_first_supported(
        ["net.peers", "net.getPeers", "peer.list", "p2p.peers"],
        [],
    )

    status = {
        "rpc_url": _RPC_CLIENT.rpc_url,
        "head": _normalize_head(head) if head_method else head,
        "sync": _normalize_sync(sync) if sync_method else {
            "unavailable": True,
            "reason": "no known sync RPC methods enabled",
        },
        "peers": _normalize_peers(peers) if peers_method else {
            "unavailable": True,
            "reason": "no known peer RPC methods enabled",
        },
    }

    status["connectivity"] = _build_connectivity(status.get("peers", {}))

    return ConsoleResult(ok=True, output=_pretty_json(status), method="node.status")


def _execute_peers() -> ConsoleResult:
    if _RPC_CLIENT is None:
        return ConsoleResult(ok=False, output="RPC client is not available.")

    method, peers = _call_first_supported(
        ["net.peers", "net.getPeers", "peer.list", "p2p.peers"],
        [],
    )
    if not method:
        return ConsoleResult(
            ok=False,
            output="Peer RPC methods are not enabled in this node build.",
        )

    normalized = _normalize_peers(peers)
    table = _format_peers_table(normalized)
    output = "\n".join([table, "", "JSON:", _pretty_json(normalized)])
    return ConsoleResult(ok=True, output=output, method=method)


def _execute_sync_dump() -> ConsoleResult:
    if _RPC_CLIENT is None:
        return ConsoleResult(ok=False, output="RPC client is not available.")

    method, result = _call_first_supported(["sync.dump"], [])
    if not method:
        method, result = _call_first_supported(["sync.getStatus", "sync.status"], [True])
    if not method:
        return ConsoleResult(
            ok=False,
            output="Sync debug RPC methods are not enabled in this node build.",
        )

    output = "\n".join(
        [
            f"RPC method: {method}",
            "Result:",
            _pretty_json(result),
        ]
    )
    return ConsoleResult(ok=True, output=output, method=method)


def _execute_peer_bootstrap(args: list[str]) -> ConsoleResult:
    if _RPC_CLIENT is None:
        return ConsoleResult(ok=False, output="RPC client is not available.")

    method, result = _call_first_supported(
        ["net.bootstrap", "peer.bootstrap", "net.addPeer", "peer.add"],
        [args[0]] if args else [],
    )
    if not method:
        return ConsoleResult(
            ok=False,
            output="Peer management RPC methods are not enabled in this node build.",
        )
    return ConsoleResult(ok=True, output=_format_result(method, args or [], result), method=method)


def _execute_peer_add(address: str) -> ConsoleResult:
    return _execute_peer_bootstrap([address])


def _format_result(method: str, params: Any, result: Any) -> str:
    lines = [f"RPC method: {method}"]
    if params not in (None, [], {}):
        lines.append(f"Params: {json.dumps(params, indent=2, sort_keys=True)}")
    lines.append("Result:")
    lines.append(_pretty_json(result))
    return "\n".join(lines)


def _format_error(method: str, exc: Exception) -> str:
    error_type = type(exc).__name__
    raw_message = str(exc)
    code, message = _extract_rpc_error_details(exc, raw_message)

    lines = [f"RPC method: {method}", f"Error type: {error_type}"]
    if code is not None:
        lines.append(f"RPC error code: {code}")
    if message:
        lines.append(f"RPC error message: {message}")
    lines.append(f"Details: {raw_message}")
    return "\n".join(lines)


def _extract_rpc_error_details(exc: Exception, raw_message: str) -> Tuple[Optional[int], Optional[str]]:
    if not isinstance(exc, LocalRpcError):
        return None, None

    if not raw_message.startswith("RPC error:"):
        return None, None

    payload = raw_message.replace("RPC error:", "", 1).strip()
    data = _parse_jsonish(payload)
    if isinstance(data, dict):
        return data.get("code"), data.get("message") or data.get("error")
    return None, payload or None


def _parse_jsonish(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(value)
        except Exception:
            return value


def _pretty_json(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, sort_keys=True)
    if value is None:
        return "(no result)"
    return str(value)


def _call_first_supported(methods: list[str], params: Any) -> Tuple[Optional[str], Any]:
    if _RPC_CLIENT is None:
        return None, None

    supported = _RPC_CLIENT.ensure_methods()
    cache_source = getattr(_RPC_CLIENT, "_methods_cache_source", None)

    for method in methods:
        if method not in supported and cache_source != "probe":
            continue
        try:
            result = _RPC_CLIENT.call(method, params)
            return method, result
        except Exception as exc:
            code, _ = _extract_rpc_error_details(exc, str(exc))
            if method not in supported and cache_source == "probe" and code == -32601:
                continue
            logger.debug("RPC method %s failed: %s", method, exc)
            continue
    return None, None


def _normalize_head(head: Any) -> dict[str, Any]:
    if not isinstance(head, dict):
        return {}
    height = head.get("height")
    if height is None:
        height = head.get("number")
    return {
        "height": height,
        "hash": head.get("hash") or head.get("blockHash") or head.get("block_hash"),
    }


def _normalize_sync(sync: Any) -> dict[str, Any]:
    if not isinstance(sync, dict):
        return {}
    return {
        "phase": sync.get("phase") or sync.get("stage") or sync.get("state"),
        "best_peer_head": (
            sync.get("best_peer_head")
            or sync.get("bestPeerHead")
            or sync.get("best_peer_height")
        ),
        "in_flight": sync.get("in_flight") or sync.get("inFlight") or sync.get("inflight"),
        "last_error": sync.get("last_error") or sync.get("lastError") or sync.get("error"),
    }


def _normalize_peers(peers: Any) -> dict[str, Any]:
    peer_list: list[dict[str, Any]] = []
    if isinstance(peers, list):
        peer_list = [p for p in peers if isinstance(p, dict)]
    elif isinstance(peers, dict):
        if isinstance(peers.get("peers"), list):
            peer_list = [p for p in peers["peers"] if isinstance(p, dict)]
        elif isinstance(peers.get("result"), list):
            peer_list = [p for p in peers["result"] if isinstance(p, dict)]

    inbound = 0
    outbound = 0
    for peer in peer_list:
        direction = peer.get("direction") or peer.get("dir")
        if isinstance(direction, str):
            if direction.lower().startswith("in"):
                inbound += 1
            elif direction.lower().startswith("out"):
                outbound += 1
        else:
            if peer.get("inbound") or peer.get("isInbound") or peer.get("inbound_only"):
                inbound += 1
            if peer.get("outbound") or peer.get("isOutbound") or peer.get("outbound_only"):
                outbound += 1

    sample = peer_list[:5]
    return {
        "total": len(peer_list),
        "inbound": inbound,
        "outbound": outbound,
        "sample": sample,
    }


def _format_peers_table(peers: dict[str, Any]) -> str:
    sample = peers.get("sample") or []
    lines = [
        f"Peers: total={peers.get('total', 0)} inbound={peers.get('inbound', 0)} outbound={peers.get('outbound', 0)}",
        "id | address | direction",
        "-" * 60,
    ]
    for peer in sample:
        if not isinstance(peer, dict):
            continue
        peer_id = peer.get("id") or peer.get("peer_id") or peer.get("peerId") or "?"
        address = peer.get("address") or peer.get("addr") or peer.get("multiaddr") or "?"
        direction = peer.get("direction") or ("inbound" if peer.get("inbound") else "outbound" if peer.get("outbound") else "?")
        lines.append(f"{peer_id} | {address} | {direction}")
    return "\n".join(lines)


def _build_connectivity(peers: dict[str, Any]) -> dict[str, Any]:
    connectivity: dict[str, Any] = {}

    listen_method, listen = _call_first_supported(
        [
            "net.listening",
            "net.listen",
            "net.listenAddr",
            "net.getListenAddr",
            "net.getListenAddresses",
            "p2p.listening",
            "p2p.listen",
        ],
        [],
    )
    if listen_method:
        connectivity["listening"] = {"method": listen_method, "value": listen}

    bootstrap_method, bootstrap = _call_first_supported(
        ["net.getBootstrapSeeds", "net.bootstrap", "peer.bootstrap", "p2p.bootstrap"],
        [],
    )
    if bootstrap_method:
        connectivity["bootstrap"] = {"method": bootstrap_method, "value": bootstrap}

    last_activity = _extract_last_peer_activity(peers.get("sample", []))
    if last_activity:
        connectivity["last_peer_activity"] = last_activity

    return connectivity


def _extract_last_peer_activity(peers: list[Any]) -> Optional[Any]:
    last_activity = None
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        activity = (
            peer.get("last_activity")
            or peer.get("lastActivity")
            or peer.get("last_seen")
            or peer.get("lastSeen")
        )
        if activity is None:
            continue
        if last_activity is None or str(activity) > str(last_activity):
            last_activity = activity
    return last_activity


def _contains_disallowed_args(command: str) -> bool:
    lowered = command.lower()
    return any(
        token in lowered
        for token in ("--rpc-url", "--rpc-auth-token", "--rpc-token", "rpc-url=")
    )


def is_packaged_mode() -> bool:
    """Return True if running in packaged (frozen) mode."""
    return bool(getattr(sys, "frozen", False))
