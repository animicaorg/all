"""
animica.ena.served_adapter — route to an ENA-served checkpoint as a mesh backend.

``EnaServedModel`` targets the ENA serving path's OpenAI-compatible endpoint (a
pool's currently-promoted checkpoint served by ``animica ena serve`` /
``serving.serve``). It reuses the OpenAI-compatible transport pointed at the ENA
serve URL, so a request routed to ``provider='ena_served'`` runs on the
community-trained model — and each served completion already produces a
Proof-of-Inference receipt.

The pool is addressed either by ``cfg.base_url`` or a model id of the form
``ena://<pool_id>`` combined with ``ANIMICA_ENA_SERVE_URL`` (default the local
coordinator). Being a real local runner, its outputs are seed-honoring, so its
receipts are replay-``verified``.
"""

from __future__ import annotations

import os

from .providers import OpenAICompatibleModel

DEFAULT_SERVE_URL = "http://127.0.0.1:8791/v1"


def _pool_from_model(model: str) -> str:
    if model and model.startswith("ena://"):
        return model[len("ena://"):]
    return model or ""


class EnaServedModel(OpenAICompatibleModel):
    name = "ena_served"
    supports_seed = True  # local runner honors the seed → replay-verifiable

    def generate(self, prompt, *, system=None, history=None,
                 max_tokens=None, temperature=None, seed=None):
        if not (self.cfg.base_url or self.cfg.endpoint):
            self.cfg.base_url = os.environ.get("ANIMICA_ENA_SERVE_URL", DEFAULT_SERVE_URL)
        # Normalize an ena://<pool_id> id down to the served model name.
        self.cfg.model = _pool_from_model(self.cfg.model) or self.cfg.model
        return super().generate(prompt, system=system, history=history,
                                max_tokens=max_tokens, temperature=temperature, seed=seed)
