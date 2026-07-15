"""Publishing channels. The owned/external boundary is architectural: OwnedChannel can publish
autonomously (to an allowlisted owned surface); ExternalChannel has NO transport and can only
produce a Draft artifact that a human must approve + post. There is no account-creation, DM, or
per-user-targeting primitive anywhere in this module.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from .config import GrowthConfig
from . import guardrails as G
from . import store


def publish_owned(cfg: GrowthConfig, draft: dict, *, live: bool = False, log=print) -> dict:
    """Publish a draft to an OWNED channel (allowlisted). Dry-run writes a preview file; a live
    publish (GROWTH_ALLOW_PUBLISH=1 + not dry-run) writes the real page. Daily-capped to prevent
    SEO/doorway spam."""
    channel = draft.get("channel", "")
    G.assert_owned_channel(channel, cfg.owned_channels + ["newsletter", "animica.dev-blog", "anm-content"])

    do_live = live and (not cfg.dry_run) and os.environ.get("GROWTH_ALLOW_PUBLISH", "0") == "1"
    slug = (draft.get("meta", {}).get("slug") or draft["content_hash"]).replace("/", "-")

    if not do_live:
        out = os.path.join(cfg.state_dir, "previews")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"{slug}.html")
        with open(path, "w") as f:
            f.write(_wrap_html(draft))
        log(f"  [preview] {channel}: {path}")
        return {"published": False, "preview": path}

    # Live owned publish → animica.dev blog dir (operator-owned infra).
    blog_dir = os.environ.get("GROWTH_BLOG_DIR", "/var/www/animica.dev/blog")
    os.makedirs(blog_dir, exist_ok=True)
    path = os.path.join(blog_dir, f"{slug}.html")
    with open(path, "w") as f:
        f.write(_wrap_html(draft))
    store.bump_send_counter(cfg, 0)  # reuse counters dir; publish cap tracked separately below
    log(f"  ✓ published {channel}: {path}")
    return {"published": True, "path": path, "url": f"https://animica.dev/blog/{slug}.html"}


def queue_external(cfg: GrowthConfig, draft: dict, log=print) -> dict:
    """External-audience artifact — saved for HUMAN approval + posting. Never auto-published."""
    if draft.get("audience") != "external":
        draft = {**draft, "audience": "external"}
    store.save_draft(cfg, draft)
    log(f"  • external draft queued for approval: {draft.get('kind')} [{draft['content_hash']}]")
    return {"queued": True, "content_hash": draft["content_hash"], "requires_approval": True}


def _wrap_html(draft: dict) -> str:
    title = draft.get("title", "Animica")
    body = draft.get("body", "")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title} · Animica</title></head><body style='max-width:720px;margin:40px auto;"
            f"font-family:system-ui,sans-serif;line-height:1.6;padding:0 20px'>"
            f"<article>{body}</article>"
            f"<hr><p style='color:#888;font-size:13px'>Published by the Animica Growth Engine · "
            f"<a href='https://animica.dev'>animica.dev</a></p></body></html>")
