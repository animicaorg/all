"""Turn a real metric snapshot into an honest 'state of growth' report: KPI extraction, an
ecosystem-health score, deltas vs a prior snapshot, and rule-based opportunities/risks. Only
real, OK-status metrics contribute a number; unavailable ones are surfaced as gaps, never guessed.
"""

from __future__ import annotations

from typing import Optional


def _map(snap: dict) -> dict:
    return {m["key"]: m for m in snap.get("metrics", [])}


def analyze(snap: dict, prev: Optional[dict] = None) -> dict:
    cur = _map(snap)
    prev_m = _map(prev) if prev else {}

    def val(key):
        m = cur.get(key)
        return m["value"] if m and m["status"] == "OK" else None

    def delta(key):
        a, b = val(key), (prev_m.get(key, {}).get("value") if prev_m.get(key, {}).get("status") == "OK" else None)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a - b
        return None

    ok = [m for m in snap.get("metrics", []) if m["status"] == "OK"]
    total = len(snap.get("metrics", []))
    health = round(100 * len(ok) / total) if total else 0

    facts = []
    for key, label, unit in [
        ("chain.height", "chain height", "blocks"),
        ("newsletter.confirmed", "confirmed newsletter subscribers", ""),
        ("ai.requests_served", "free-AI requests served", ""),
        ("mkt.listings", "marketplace listings", ""),
        ("market.anm_usdt", "ANM/USDT on NonKYC", "USDT"),
        ("pool.hashrate", "pool hashrate", "H/s"),
    ]:
        v = val(key)
        if v is not None:
            d = delta(key)
            facts.append({"key": key, "label": label, "value": v, "unit": unit,
                          "delta": d, "status": "OK", "source": cur[key]["source"]})

    # Rule-based, honest opportunities/risks (no fabrication).
    opps, risks = [], []
    subs = val("newsletter.confirmed")
    if subs is not None and subs < 100:
        opps.append("Newsletter list is small — prioritize double-opt-in signups on animica.dev and the .anm sites.")
    if val("market.anm_usdt") is not None:
        opps.append("ANM is live on NonKYC — the first newsletter should tell subscribers exactly where to buy/trade.")
    if val("mkt.listings") is not None:
        opps.append("Publish SEO content + submit to aggregator directories to grow discovery.")
    for m in snap.get("metrics", []):
        if m["status"] != "OK":
            risks.append(f"Data source unavailable: {m['key']} (report shows it as a gap, not a number).")

    return {
        "ts": snap.get("ts"),
        "health_score": health,
        "facts": facts,
        "opportunities": opps,
        "risks": risks,
        "gaps": snap.get("unavailable", []),
    }
