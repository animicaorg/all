"""The autonomous growth cycle — the spine that wires the phases together with the guardrails.

A cycle is: collect real metrics → analyze honestly → AI insights → draft content →
(owned) preview/publish + (external) queue-for-approval → newsletter dry-run/send → report.
Nothing leaves the machine unless the operator has explicitly flipped the live flags AND approved
the specific artifact — the default posture produces analysis + drafts + outbox previews only.
"""

from __future__ import annotations

import time
from typing import Optional

from .config import GrowthConfig, load
from . import collectors, analysis, insights, content, channels, newsletter, store


def run_cycle(cfg: Optional[GrowthConfig] = None, *, send_newsletter: bool = False,
              approver: Optional[str] = None, log=print) -> dict:
    cfg = cfg or load()
    log(f"growth cycle · dry_run={cfg.dry_run} live_send={cfg.allow_live_send}")

    # 1. real metrics
    snap = collectors.snapshot(cfg)
    store.save_snapshot(cfg, snap)
    prev = store.last_snapshot(cfg, before=snap["ts"])
    log(f"  collected {snap['ok_count']}/{len(snap['metrics'])} live metrics"
        + (f" · gaps: {', '.join(snap['unavailable'])}" if snap["unavailable"] else ""))

    # 2. analysis + 3. AI insights
    report = analysis.analyze(snap, prev)
    ins = insights.generate(cfg, report)
    log(f"  health {report['health_score']}% · {len(report['opportunities'])} opportunities · insights via {ins.get('_source')}")

    # 4. drafts
    campaign = content.nonkyc_welcome_campaign(cfg, report)
    store.save_draft(cfg, campaign)
    socials = content.draft_social_posts(cfg, report)
    for s in socials:
        channels.queue_external(cfg, s, log=log)

    # 5. newsletter (dry-run outbox unless explicitly sending live + approved)
    nl = {"sent": 0, "dry_run": True}
    if send_newsletter:
        nl = newsletter.send_campaign(cfg, campaign, approver=approver, log=log)
    else:
        # Always produce a dry-run preview to the outbox so the operator can inspect the exact bytes.
        nl = newsletter.send_campaign(cfg, campaign, log=log)

    # 6. report
    rpt = {
        "ts": int(time.time()),
        "health_score": report["health_score"],
        "facts": report["facts"],
        "opportunities": report["opportunities"],
        "risks": report["risks"],
        "gaps": report["gaps"],
        "insights": ins,
        "drafts": {"campaign": campaign["content_hash"], "social": [s["content_hash"] for s in socials]},
        "newsletter": nl,
    }
    return rpt
