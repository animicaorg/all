"""
animica.ena.adapters_chutes — Bittensor/Chutes backend for the provider mesh.

Chutes exposes an OpenAI-compatible ``/chat/completions`` surface (Animica's
Track-1 Chutes resell), so ``ChutesModel`` reuses the OpenAI-compatible transport
with the Chutes base URL + bearer auth from ``CHUTES_API_TOKEN``. Routing a model
to ``provider='chutes'`` (or ``'bittensor'``) reaches decentralized GPU compute
priced in the marketplace.
"""

from __future__ import annotations

from .providers import OpenAICompatibleModel

DEFAULT_CHUTES_BASE = "https://llm.chutes.ai/v1"


class ChutesModel(OpenAICompatibleModel):
    name = "chutes"
    supports_seed = True  # OpenAI-compatible seed passthrough (best-effort)

    def generate(self, prompt, *, system=None, history=None,
                 max_tokens=None, temperature=None, seed=None):
        if not (self.cfg.base_url or self.cfg.endpoint):
            self.cfg.base_url = DEFAULT_CHUTES_BASE
        if not self.cfg.api_key_env_vars:
            self.cfg.api_key_env_vars = ["CHUTES_API_TOKEN", "CHUTES_API_KEY"]
        return super().generate(prompt, system=system, history=history,
                                max_tokens=max_tokens, temperature=temperature, seed=seed)
