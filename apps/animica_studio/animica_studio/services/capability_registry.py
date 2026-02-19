"""CapabilityRegistry — discover RPC methods and CLI commands at startup.

The registry is populated by calling :meth:`CapabilityRegistry.refresh` once
at app startup (off the UI thread).  The UI then uses the registry to
enable/disable buttons.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any

from animica_studio.services.rpc_client import RpcClient
from animica_studio.storage.config import Config

log = logging.getLogger(__name__)


class CapabilityRegistry:
    """Caches discovered RPC methods and available CLI sub-commands."""

    def __init__(self) -> None:
        self._rpc_methods: set[str] = set()
        self._cli_commands: set[str] = set()
        self._raw_discover: dict[str, Any] = {}
        self._ready: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, config: Config) -> None:
        """Populate the registry by probing RPC and CLI.

        Must be called off the UI thread.
        """
        self._discover_rpc(config)
        self._discover_cli(config)
        self._ready = True
        log.info(
            "CapabilityRegistry: %d RPC methods, %d CLI commands",
            len(self._rpc_methods),
            len(self._cli_commands),
        )

    @property
    def ready(self) -> bool:
        return self._ready

    def has_rpc(self, method: str) -> bool:
        """Return ``True`` if *method* was found in the discover result."""
        if not self._rpc_methods:
            return True  # unknown — assume available
        return method in self._rpc_methods

    def has_cli(self, command: str) -> bool:
        """Return ``True`` if *command* was found in CLI help output."""
        if not self._cli_commands:
            return True
        return command in self._cli_commands

    def rpc_methods(self) -> set[str]:
        return set(self._rpc_methods)

    def cli_commands(self) -> set[str]:
        return set(self._cli_commands)

    # ------------------------------------------------------------------
    # Internal discovery
    # ------------------------------------------------------------------

    def _discover_rpc(self, config: Config) -> None:
        profile = config.get_active_profile()
        url = profile.node.rpc_local_url.rstrip("/")
        if not url.endswith("/rpc"):
            url = url + "/rpc"
        try:
            client = RpcClient(url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
            disc = client.discover()
            methods_raw = disc.get("methods", [])
            if isinstance(methods_raw, list):
                self._rpc_methods = {
                    (m.get("name", "") if isinstance(m, dict) else str(m))
                    for m in methods_raw
                }
            self._raw_discover = disc
            client.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("CapabilityRegistry: RPC discover failed: %s", exc)

    def _discover_cli(self, config: Config) -> None:
        bin_ = config.get_active_profile().cli.animica_bin
        try:
            result = subprocess.run(
                [bin_, "--help"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            output = result.stdout + result.stderr
            self._cli_commands = _parse_cli_commands(output)
        except Exception as exc:  # noqa: BLE001
            log.debug("CapabilityRegistry: CLI discover failed: %s", exc)


def _parse_cli_commands(help_text: str) -> set[str]:
    """Extract top-level command names from ``--help`` output."""
    commands: set[str] = set()
    in_commands = False
    for line in help_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_commands:
                in_commands = False
            continue
        lower = stripped.lower()
        if "commands" in lower or "subcommands" in lower:
            in_commands = True
            continue
        if in_commands:
            # First word on the line is typically the command name
            word = stripped.split()[0].rstrip(":")
            if word and word[0].isalpha():
                commands.add(word)
    return commands
