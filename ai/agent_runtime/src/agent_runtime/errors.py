"""Exception hierarchy for the agent runtime.

All AI-stack errors derive from AgentRuntimeError so callers can do a single
catch at the boundary and surface structured messages to the user.
"""

from __future__ import annotations

from typing import Any, Optional


class AgentRuntimeError(Exception):
    """Root of the agent_runtime exception tree."""

    def __init__(self, message: str, *, hint: Optional[str] = None,
                 detail: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail: dict[str, Any] = dict(detail or {})

    def render(self) -> str:
        out = [self.message]
        if self.hint:
            out.append(f"hint: {self.hint}")
        if self.detail:
            kv = ", ".join(f"{k}={v!r}" for k, v in self.detail.items())
            out.append(f"detail: {kv}")
        return "\n".join(out)


class ConfigError(AgentRuntimeError):
    """A config file is malformed, references a missing path, fails strict
    validation, or contains an unresolvable env interpolation."""


class ProviderUnavailable(AgentRuntimeError):
    """A provider declined to serve a turn (missing wallet, no bundle, network
    unreachable, etc.). Callers cascade to the next provider in the order
    from integration.yaml unless ANIMICA_CHAT_REQUIRE_DISTRIBUTED is set."""

    def __init__(self, provider: str, reason: str, **kwargs: Any) -> None:
        super().__init__(f"provider {provider!r} unavailable: {reason}",
                         **kwargs)
        self.provider = provider
        self.reason = reason


class WalletError(AgentRuntimeError):
    """Wallet load / sign / balance failure."""


class AICFError(AgentRuntimeError):
    """AICF protocol-level error (submit, poll, settle)."""

    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        super().__init__(f"AICF {code}: {message}", **kwargs)
        self.code = code


class BundleError(AgentRuntimeError):
    """Local flagship bundle is missing, malformed, or unavailable for real
    inference (see eval.yaml::gates.available_for_real_inference)."""
