"""Render a human-readable growth report (markdown) from a cycle result. Numbers are shown with
their source; gaps are shown as gaps."""

from __future__ import annotations

from typing import Optional


def to_markdown(rpt: dict) -> str:
    lines = ["# Animica Growth Report", ""]
    lines.append(f"**Ecosystem health:** {rpt.get('health_score')}%")
    lines.append("")
    lines.append("## Live metrics")
    facts = rpt.get("facts", [])
    if facts:
        for f in facts:
            d = f.get("delta")
            darr = f" ({'+' if (isinstance(d,(int,float)) and d>=0) else ''}{d})" if isinstance(d, (int, float)) else ""
            unit = f" {f['unit']}" if f.get("unit") else ""
            lines.append(f"- **{f['label']}:** {f['value']}{unit}{darr}  \n  <sub>source: {f['source']}</sub>")
    else:
        lines.append("- _no live metrics available this cycle_")
    gaps = rpt.get("gaps", [])
    if gaps:
        lines.append("")
        lines.append(f"> ⚠️ Data unavailable (shown as gaps, not fabricated): {', '.join(gaps)}")

    ins = rpt.get("insights", {})
    if ins:
        lines += ["", "## Insights", f"_{ins.get('summary','')}_", ""]
        for a in ins.get("actions", [])[:6]:
            lines.append(f"- {a}")

    nl = rpt.get("newsletter", {})
    if nl:
        lines += ["", "## Newsletter",
                  f"- {'LIVE' if not nl.get('dry_run', True) else 'DRY-RUN (outbox preview)'} · "
                  f"sent {nl.get('sent',0)} of {nl.get('recipients','?')} confirmed subscribers"]

    d = rpt.get("drafts", {})
    if d:
        lines += ["", "## Drafts produced",
                  f"- campaign: `{d.get('campaign')}`",
                  f"- social (queued for approval): {len(d.get('social', []))}"]
    return "\n".join(lines)
