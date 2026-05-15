"""Animica agent runtime — distributed AICF chat client + shared primitives.

Importable submodules:
    agent_runtime.config         hierarchical YAML+env config loader
    agent_runtime.logging        structured logging + stage manifests
    agent_runtime.errors         exception hierarchy
    agent_runtime.types          shared dataclasses/typed dicts

Stable public re-exports below.
"""

from __future__ import annotations

__version__ = "0.1.0"

from agent_runtime.config import (
    ConfigError,
    Config,
    load_config,
    load_run_config,
    resolve_repo_root,
)
from agent_runtime.errors import (
    AgentRuntimeError,
    ProviderUnavailable,
    WalletError,
    AICFError,
    BundleError,
)

__all__ = [
    "__version__",
    "ConfigError",
    "Config",
    "load_config",
    "load_run_config",
    "resolve_repo_root",
    "AgentRuntimeError",
    "ProviderUnavailable",
    "WalletError",
    "AICFError",
    "BundleError",
]
