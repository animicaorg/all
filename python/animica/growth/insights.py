"""AI insight generation. Sends the REAL analyzed metrics to the free treasury AI gateway as
grounded context and asks for insights/opportunities — with an explicit instruction to use only
the provided numbers and to say 'unavailable' otherwise. Falls back to the rule-based report if the
gateway is unreachable. Never invents metrics.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional

from .config import GrowthConfig

_SYSTEM = (
    "You are Animica's growth analyst. You will be given REAL ecosystem metrics as JSON. "
    "Use ONLY those numbers — never invent or estimate figures; if something isn't in the data, "
    "say it's unavailable. Be concrete, honest, and non-hyperbolic. Do NOT write marketing copy or "
    "price predictions here — only analysis: what's working, what to improve, and 3-5 concrete, "
    "legal growth actions (SEO/content, opt-in newsletter, directory listings, community). "
    "Return strict JSON: {\"summary\":str,\"insights\":[str],\"actions\":[str]}."
)


def generate(cfg: GrowthConfig, report: dict, *, timeout: float = 45.0) -> dict:
    payload = {
        "model": cfg.gateway_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": "Metrics + rule-based report (JSON):\n" + json.dumps(report)[:6000]},
        ],
        "temperature": 0.3,
        "stream": False,
    }
    try:
        req = urllib.request.Request(
            f"{cfg.gateway_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            d = json.loads(r.read().decode())
        text = d["choices"][0]["message"]["content"]
        parsed = _extract_json(text)
        if parsed:
            parsed["_source"] = "ai-gateway"
            return parsed
    except Exception:
        pass
    # Honest fallback — reuse the rule-based analysis, clearly labeled.
    return {
        "summary": f"Ecosystem health {report.get('health_score')}%. "
                   f"{len(report.get('gaps', []))} data source(s) unavailable.",
        "insights": report.get("opportunities", []) + [f"Gap: {g}" for g in report.get("gaps", [])],
        "actions": report.get("opportunities", []),
        "_source": "rule-based-fallback",
    }


def _extract_json(text: str) -> Optional[dict]:
    try:
        s = text.find("{"); e = text.rfind("}")
        if s >= 0 and e > s:
            return json.loads(text[s:e + 1])
    except Exception:
        return None
    return None
