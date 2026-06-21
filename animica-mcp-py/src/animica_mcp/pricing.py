"""Per-model pricing (USD per 1M tokens) for price quotes."""
from __future__ import annotations
import json
import math
import os

DEFAULT = {
    "anm-fast-8b": {"in": 0.10, "out": 0.30},
    "anm-code-7b": {"in": 0.15, "out": 0.45},
    "anm-pro-70b": {"in": 0.60, "out": 1.80},
    "anm-flagship-moe": {"in": 0.40, "out": 1.20},
    "anm-embed": {"in": 0.02, "out": 0.0},
}


def pricing() -> dict:
    raw = os.environ.get("ANIMICA_PRICING_JSON")
    if raw:
        try:
            return {**DEFAULT, **json.loads(raw)}
        except Exception:
            pass
    return DEFAULT


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def quote(model: str, input_tokens: int, output_tokens: int) -> dict:
    p = pricing().get(model, DEFAULT["anm-fast-8b"])
    cost = input_tokens / 1e6 * p["in"] + output_tokens / 1e6 * p["out"]
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "price_per_1m_input_usd": p["in"],
        "price_per_1m_output_usd": p["out"],
        "estimated_cost_usd": round(cost, 6),
    }
