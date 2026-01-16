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
  animica node status
  animica sync force
  animica mempool list
  animica wallet show <address>
  animica peer list
  animica peer count
  animica peer add <multiaddr>
  animica peer remove <peer_id>
  animica peer info <peer_id>
  animica peer bootstrap

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

    if verb == "node" and sub == "status":
        return _execute_rpc("node.status", [])

    if verb == "sync" and sub == "force":
        return _execute_rpc("sync.force", [])

    if verb == "mempool" and sub == "list":
        return _execute_rpc("mempool.list", [])

    if verb == "wallet" and sub in {"show", "balance"}:
        if not remainder:
            return ConsoleResult(ok=False, output="wallet show requires an address.")
        return _execute_rpc("state.getBalance", [remainder[0]])

    if verb == "peer":
        if sub in {"list", "ls"}:
            return _execute_rpc("net.peers", [])
        if sub == "count":
            return _execute_rpc("net.peerCount", [])
        if sub == "bootstrap":
            return _execute_rpc("net.getBootstrapSeeds", [])
        if sub == "add":
            if not remainder:
                return ConsoleResult(ok=False, output="peer add requires a multiaddr.")
            return _execute_rpc("p2p.addPeer", [remainder[0]])
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


def _contains_disallowed_args(command: str) -> bool:
    lowered = command.lower()
    return any(
        token in lowered
        for token in ("--rpc-url", "--rpc-auth-token", "--rpc-token", "rpc-url=")
    )


def is_packaged_mode() -> bool:
    """Return True if running in packaged (frozen) mode."""
    return bool(getattr(sys, "frozen", False))
