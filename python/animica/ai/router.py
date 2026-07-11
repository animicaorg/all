"""
animica.ai.router — the intelligent provider-mesh router (7.1.1 P3).

Wraps (never replaces) ``gateway.resolve_chat_adapter``. With no routing policy
active, ``route()`` returns exactly what ``resolve_chat_adapter`` returns today —
the base case is byte-for-byte unchanged. When a policy is active (config
``[routing]``, a per-request ``animica.policy/candidates``, or the
``ANIMICA_AI_ROUTING`` env flag), it builds an ordered candidate chain and returns
a :class:`FallbackModelAdapter` that tries each provider in turn, skipping any
whose circuit breaker is open and recording latency/health telemetry — the
offline ``deterministic`` backend is always the final safety net.

Speculative/concurrent racing is intentionally NOT done (blocking urllib inside
the async loop is unsafe); this is sequential fallback + circuit breaking.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from animica.ai import policy as _policy
from animica.ai import telemetry

# Synthesized default model per provider when the config has no entry for it.
_DEFAULT_MODEL = {
    "anthropic": "claude-opus-4-8",
    "claude": "claude-opus-4-8",
    "openai": "gpt-4o-mini",
    "chutes": "",
    "bittensor": "",
    "ollama": "",
    "ena_served": "",
    "deterministic": "deterministic",
}


# Relative default $/1k when neither the provider config nor telemetry knows a
# real price — enough to order 'cheapest' sensibly (offline nets are free).
_DEFAULT_COST = {"deterministic": 0.0, "ollama": 0.0, "ena_served": 0.0,
                 "chutes": 0.2, "bittensor": 0.2, "openai": 0.5,
                 "anthropic": 3.0, "claude": 3.0}


def _cost_of(provider_key: str, cfg, tel: dict) -> float:
    if cfg is not None:
        for _k, mp in (getattr(cfg, "model_providers", None) or {}).items():
            if mp.provider == provider_key and getattr(mp, "cost_per_1k", None) is not None:
                return float(mp.cost_per_1k)
    t = tel.get(provider_key) or {}
    if t.get("cost_per_1k") is not None:
        return float(t["cost_per_1k"])
    return _DEFAULT_COST.get(provider_key, 1.0)


def _latency_of(provider_key: str, tel: dict) -> float:
    t = tel.get(provider_key) or {}
    lat = t.get("ewma_latency_ms")
    return float(lat) if lat else float("inf")  # untried → sorted after measured


def _apply_policy_order(order: list, pol, cfg, tel: dict) -> list:
    """Order candidates by the policy mode + enforce the cost cap. The offline
    ``deterministic`` net is always retained and always last."""
    rest = [k for k in order if k != "deterministic"]
    if pol.max_cost_per_1k is not None:
        rest = [k for k in rest if _cost_of(k, cfg, tel) <= pol.max_cost_per_1k]
    if pol.mode == "cheapest":
        rest.sort(key=lambda k: _cost_of(k, cfg, tel))
    elif pol.mode == "fastest":
        rest.sort(key=lambda k: _latency_of(k, tel))
    # 'quality' / 'quantum_fair' keep the policy's authored order.
    return rest + [_policy.DETERMINISTIC_FALLBACK]


@dataclass
class RouteResult:
    adapter: Any
    provider_key: str
    resolved_model: str
    candidates_tried: list[str] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)


def _operator_routing_on(cfg) -> bool:
    """Routing is a privileged, potentially-paid operation (it can send traffic to
    a provider backed by the OPERATOR's API key). It is enabled ONLY by the
    operator — via config ``[routing]`` or ``ANIMICA_AI_ROUTING`` — never by an
    untrusted request field."""
    if cfg is not None and getattr(cfg, "routing", None):
        return True
    return os.environ.get("ANIMICA_AI_ROUTING", "").strip().lower() in {"1", "true", "on", "yes"}


def _routing_active(request: dict, policy: Optional[_policy.RoutePolicy], cfg) -> bool:
    if policy is not None:
        return True
    # A per-request animica.policy/candidates only takes effect once the operator
    # has opted into routing — otherwise a client could steer traffic to the
    # operator's paid Claude key and discard its own requested (free) model.
    return _operator_routing_on(cfg)


def _candidate_cfg(provider_key: str, cfg, requested_model: Optional[str]):
    """A ModelProviderConfig for ``provider_key`` (configured entry or synthesized)."""
    from animica.ena.models import ModelProviderConfig
    if cfg is not None:
        for _key, mp in (getattr(cfg, "model_providers", None) or {}).items():
            if mp.provider == provider_key:
                import copy
                mp = copy.deepcopy(mp)
                return mp
    model = _DEFAULT_MODEL.get(provider_key, requested_model or "")
    if provider_key in ("ollama", "openai", "chutes", "bittensor", "ena_served") and requested_model:
        model = requested_model
    env_keys = {"anthropic": ["ANTHROPIC_API_KEY"], "claude": ["ANTHROPIC_API_KEY"],
                "openai": ["OPENAI_API_KEY"], "chutes": ["CHUTES_API_TOKEN"],
                "bittensor": ["CHUTES_API_TOKEN"]}.get(provider_key, [])
    return ModelProviderConfig(name=provider_key, provider=provider_key, model=model or "",
                               api_key_env_vars=env_keys)


class FallbackModelAdapter:
    """Tries an ordered list of (provider_key, adapter, model) candidates.

    Skips breaker-open providers, records telemetry, and advances to the next on
    ProviderError. Exposes ``last_provider`` / ``last_model`` / ``last_seed_applied``
    after ``generate`` so the caller's receipt reflects who actually served.
    """

    def __init__(self, candidates: list[tuple[str, Any, str]]):
        self.candidates = candidates
        first = candidates[0] if candidates else (None, None, None)
        self.last_provider, _, self.last_model = first
        self.last_seed_applied = False

    @property
    def supports_seed(self) -> bool:
        return bool(self.candidates and getattr(self.candidates[0][1], "supports_seed", False))

    def generate(self, prompt, *, system=None, history=None, max_tokens=None,
                 temperature=None, seed=None) -> str:
        from animica.ena.errors import ProviderError
        errors: list[str] = []
        for pk, adapter, model in self.candidates:
            if telemetry.breaker_open(pk):
                errors.append(f"{pk}:breaker_open")
                continue
            use_seed = seed if getattr(adapter, "supports_seed", False) else None
            t0 = time.perf_counter()
            try:
                out = adapter.generate(prompt, system=system, history=history,
                                       max_tokens=max_tokens, temperature=temperature,
                                       seed=use_seed)
                telemetry.record(pk, latency_ms=(time.perf_counter() - t0) * 1000, ok=True)
                self.last_provider, self.last_model = pk, model
                self.last_seed_applied = use_seed is not None
                return out
            except ProviderError as e:
                telemetry.record(pk, latency_ms=(time.perf_counter() - t0) * 1000, ok=False)
                errors.append(f"{pk}:{e}")
            except Exception as e:  # noqa: BLE001 — treat as a provider failure, keep going
                telemetry.record(pk, latency_ms=(time.perf_counter() - t0) * 1000, ok=False)
                errors.append(f"{pk}:{type(e).__name__}")
        raise ProviderError("all mesh candidates failed: " + "; ".join(errors))


def route(request: dict, *, cfg=None, policy=None, default_provider=None) -> RouteResult:
    """Resolve a chat route. Passthrough to resolve_chat_adapter unless routing is active."""
    from animica.ai.gateway import resolve_chat_adapter
    if cfg is None:
        try:
            from animica.ena import config as ena_config
            cfg = ena_config.load_config()
        except Exception:  # noqa: BLE001
            cfg = None

    pol = _policy.resolve_policy(policy, cfg) if policy is not None else None
    if not _routing_active(request, pol, cfg):
        adapter, key, model = resolve_chat_adapter(request.get("model"), default_provider)
        return RouteResult(adapter, key, model, [key], {"policy": "none", "reason": "passthrough"})

    # Active routing: resolve the policy + candidate order.
    a = request.get("animica") if isinstance(request.get("animica"), dict) else {}
    if pol is None:
        pol = _policy.resolve_policy(a.get("policy") or (a.get("candidates") and
              {"name": "adhoc", "candidates": a["candidates"]}) or None, cfg)
    requested_model = request.get("model")
    order = _apply_policy_order(pol.ordered(), pol, cfg, telemetry.snapshot())

    from animica.ena.providers import build_model_adapter
    candidates: list[tuple[str, Any, str]] = []
    for provider_key in order:
        try:
            mp = _candidate_cfg(provider_key, cfg, requested_model)
            candidates.append((provider_key, build_model_adapter(mp), mp.model))
        except Exception:  # noqa: BLE001 — skip a candidate we can't build
            continue
    if not candidates:
        adapter, key, model = resolve_chat_adapter(requested_model, default_provider)
        return RouteResult(adapter, key, model, [key], {"policy": pol.name, "reason": "no_candidates"})

    fb = FallbackModelAdapter(candidates)
    return RouteResult(fb, candidates[0][0], candidates[0][2],
                       [c[0] for c in candidates],
                       {"policy": pol.name, "mode": pol.mode, "order": [c[0] for c in candidates]})


def router_status(cfg=None) -> dict[str, Any]:
    """Data for GET /v1/router/status: policies + per-provider health."""
    return {"object": "animica.router",
            "policies": {k: {"mode": v.mode, "order": v.ordered()}
                         for k, v in _policy.default_policies().items()},
            "health": telemetry.snapshot()}
