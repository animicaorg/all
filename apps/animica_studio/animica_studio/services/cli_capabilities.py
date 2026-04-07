"""Singleton access to CLI registry + operation builders."""

from __future__ import annotations

from animica_studio.services.cli_ops import CliOps
from animica_studio.services.cli_registry import CliRegistry
from animica_studio.storage.config import Config

_registry: CliRegistry | None = None
_ops: CliOps | None = None


def get_cli_registry(config: Config) -> CliRegistry:
    global _registry
    if _registry is None:
        _registry = CliRegistry(config)
    return _registry


def refresh_cli_registry(config: Config) -> CliRegistry:
    global _registry, _ops
    _registry = CliRegistry(config)
    _registry.refresh()
    _ops = CliOps(_registry)
    return _registry


def get_cli_ops(config: Config) -> CliOps:
    global _ops
    if _ops is None:
        _ops = CliOps(get_cli_registry(config))
    return _ops
