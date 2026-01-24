"""RPC-backed console router for the GUI."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from animica_miner_gui.backend.rpc_client import RPCClient

logger = logging.getLogger(__name__)


class ConsoleRouter:
    """Parse console commands and route to RPC."""

    def __init__(self, rpc_client: RPCClient):
        self.rpc_client = rpc_client

    def handle(self, command: str) -> Dict[str, Any]:
        command = command.strip()
        if not command:
            return {"ok": False, "message": "Empty command"}

        if command.startswith("rpc "):
            return self._handle_rpc(command)

        if command == "animica node status":
            return self._handle_node_status()

        if command.startswith("animica peer bootstrap"):
            return self._handle_peer_bootstrap(command)

        return {
            "ok": False,
            "message": "Unknown command. Try: animica node status, animica peer bootstrap, rpc <method> <json>",
        }

    def _handle_rpc(self, command: str) -> Dict[str, Any]:
        parts = command.split(maxsplit=2)
        if len(parts) < 2:
            return {"ok": False, "message": "Usage: rpc <method> [json]"}
        method = parts[1]
        params = []
        if len(parts) == 3:
            try:
                params = json.loads(parts[2])
            except json.JSONDecodeError as exc:
                return {"ok": False, "message": f"Invalid JSON params: {exc}"}
        try:
            result = self.rpc_client._call(method, params)
            return {"ok": True, "result": result}
        except Exception as exc:
            methods = self._safe_methods()
            return {"ok": False, "message": str(exc), "methods": methods}

    def _handle_node_status(self) -> Dict[str, Any]:
        try:
            head = self.rpc_client.get_chain_head()
            sync = self.rpc_client.get_sync_status()
            peers = self.rpc_client.get_peer_summary()
            return {"ok": True, "head": head, "sync": sync, "peers": peers}
        except Exception as exc:
            methods = self._safe_methods()
            return {"ok": False, "message": str(exc), "methods": methods}

    def _handle_peer_bootstrap(self, command: str) -> Dict[str, Any]:
        parts = command.split()
        if len(parts) < 3:
            return {"ok": False, "message": "Usage: animica peer bootstrap <addr>"}
        addr = parts[-1]
        for method in ["peer.bootstrap", "peer.bootstrapAdd", "p2p.bootstrap", "p2p.addPeer"]:
            try:
                result = self.rpc_client._call(method, [addr])
                return {"ok": True, "result": result, "method": method}
            except Exception:
                continue
        methods = self._safe_methods()
        return {"ok": False, "message": "Bootstrap method not available", "methods": methods}

    def _safe_methods(self) -> Optional[list]:
        try:
            return self.rpc_client.get_rpc_methods()
        except Exception as exc:
            logger.debug("Failed to list methods: %s", exc)
            return None
