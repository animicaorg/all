"""
animica.ai.policy — routing policies for the provider mesh (7.1.1 P3).

A :class:`RoutePolicy` describes how the router picks among candidate providers:
an ordered fallback chain plus a selection ``mode`` (cheapest / fastest / quality
/ quantum-fair). Policies come from the ENA config ``[routing]`` section or a
per-request ``body['animica']['policy']`` override; the built-ins below are the
zero-config defaults. The offline ``deterministic`` provider is always the last
resort so the engine never hard-fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DETERMINISTIC_FALLBACK = "deterministic"


@dataclass
class RoutePolicy:
    name: str = "default"
    mode: str = "quality"                       # quality | cheapest | fastest | quantum_fair
    candidates: list[str] = field(default_factory=list)   # ordered provider keys
    max_cost_per_1k: Optional[float] = None
    fallback_chain: list[str] = field(default_factory=list)
    quantum_fair: bool = False

    def ordered(self) -> list[str]:
        """The full candidate order, deterministic-fallback guaranteed last."""
        seq = list(self.candidates or []) + list(self.fallback_chain or [])
        out: list[str] = []
        for k in seq:
            if k and k not in out:
                out.append(k)
        if DETERMINISTIC_FALLBACK not in out:
            out.append(DETERMINISTIC_FALLBACK)
        return out


def default_policies() -> dict[str, RoutePolicy]:
    return {
        "quality": RoutePolicy(name="quality", mode="quality",
                               candidates=["anthropic", "openai", "ollama"],
                               fallback_chain=["ollama", DETERMINISTIC_FALLBACK]),
        "cheapest": RoutePolicy(name="cheapest", mode="cheapest",
                                candidates=["ollama", "chutes", "anthropic"],
                                fallback_chain=[DETERMINISTIC_FALLBACK]),
        "fastest": RoutePolicy(name="fastest", mode="fastest",
                               candidates=["ollama", "anthropic", "chutes"],
                               fallback_chain=[DETERMINISTIC_FALLBACK]),
        "quantum_fair": RoutePolicy(name="quantum_fair", mode="quantum_fair", quantum_fair=True,
                                    candidates=["anthropic", "chutes", "ollama"],
                                    fallback_chain=[DETERMINISTIC_FALLBACK]),
    }


def policy_from_config(cfg) -> Optional[RoutePolicy]:
    """Read a ``[routing]`` policy off an ENAConfig, if present."""
    routing = getattr(cfg, "routing", None)
    if not routing:
        return None
    if isinstance(routing, RoutePolicy):
        return routing
    if isinstance(routing, dict):
        known = {f for f in RoutePolicy.__dataclass_fields__}
        return RoutePolicy(**{k: v for k, v in routing.items() if k in known})
    return None


def resolve_policy(spec, cfg=None) -> RoutePolicy:
    """Turn a policy spec (name str | dict | RoutePolicy | None) into a RoutePolicy."""
    if isinstance(spec, RoutePolicy):
        return spec
    if isinstance(spec, dict):
        known = {f for f in RoutePolicy.__dataclass_fields__}
        return RoutePolicy(**{k: v for k, v in spec.items() if k in known})
    if isinstance(spec, str):
        pol = default_policies().get(spec)
        if pol:
            return pol
    from_cfg = policy_from_config(cfg) if cfg is not None else None
    return from_cfg or default_policies()["quality"]
