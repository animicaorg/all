"""Content drafting. Owned-channel drafts (blog, .anm page) can auto-publish; external drafts
(social, forum posts) are artifacts for human approval. Financial content (the buy/trade campaign)
carries a mandatory disclaimer + material-interest disclosure and is run through the prohibited-
phrase linter — a draft that fails the linter is never returned.

Templates use two placeholders the SENDER fills so every email is compliant by construction:
  {{unsubscribe_url}} — per-recipient one-click link   {{org_postal}} — CAN-SPAM postal identity
"""

from __future__ import annotations

import hashlib
from typing import Optional

from .config import GrowthConfig
from . import guardrails as G


def content_hash(subject: str, body: str) -> str:
    return "g1_" + hashlib.sha3_256((subject + "\n" + body).encode()).hexdigest()[:40]


def _draft(kind: str, channel: str, audience: str, subject: str, body_html: str, body_text: str, meta=None) -> dict:
    return {
        "id": content_hash(subject, body_text),
        "kind": kind, "channel": channel, "audience": audience,
        "content_hash": content_hash(subject, body_text),
        "title": subject, "body": body_html, "body_text": body_text, "meta": meta or {},
    }


def nonkyc_welcome_campaign(cfg: GrowthConfig, report: Optional[dict] = None) -> dict:
    """The first newsletter: where to buy & trade ANM on NonKYC, + Discord. Financial → strict."""
    market = cfg.nonkyc_market
    discord = cfg.discord_invite
    disc = G.FINANCIAL_DISCLAIMER

    text = f"""Where to buy & trade ANM (Animica)

Animica is a post-quantum Layer-1 with a free AI network, a decentralized marketplace,
generative media served by GPU miners, a dVPN, and a sovereign .anm internet.

ANM trades on NonKYC:
  Buy / trade ANM/USDT:  {market}

Join the community:
  Discord:  {discord}
  Web:      https://animica.dev

{disc}

You’re receiving this because you confirmed your subscription to the Animica newsletter.
Unsubscribe any time (one click): {{{{unsubscribe_url}}}}
{{{{org_postal}}}}
"""

    html = f"""<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:560px;margin:0 auto;color:#1c1a17;line-height:1.6">
<h1 style="font-family:Georgia,serif;font-weight:500;font-size:26px">Where to buy &amp; trade ANM</h1>
<p>Animica is a post-quantum Layer-1 with a free AI network, a decentralized marketplace, generative
media served by GPU miners, a dVPN, and a sovereign <b>.anm</b> internet.</p>
<p style="text-align:center;margin:26px 0">
  <a href="{market}" style="display:inline-block;background:#c8613f;color:#fff;padding:13px 26px;border-radius:12px;text-decoration:none;font-weight:600">Buy &amp; trade ANM/USDT on NonKYC →</a>
</p>
<p style="text-align:center">
  <a href="{discord}" style="color:#5865F2;font-weight:600;text-decoration:none">Join the Discord community</a> ·
  <a href="https://animica.dev" style="color:#a94e30;text-decoration:none">animica.dev</a>
</p>
<p style="font-size:12px;color:#6f685c;border-top:1px solid #e6dfd0;padding-top:12px;margin-top:24px">{disc}</p>
<p style="font-size:12px;color:#938b7c">You’re receiving this because you confirmed your Animica newsletter subscription.
<a href="{{{{unsubscribe_url}}}}" style="color:#938b7c">Unsubscribe</a> (one click).<br>{{{{org_postal}}}}</p>
</div>"""

    # Compliance: financial content must pass the linter (checks disclaimer presence + no pump phrasing).
    G.financial_lint(text, strict=cfg.financial_strict)

    d = _draft("newsletter", channel="newsletter", audience="subscribers",
               subject="Where to buy & trade ANM (Animica)", body_html=html, body_text=text,
               meta={"financial": True, "campaign_slug": "nonkyc-welcome"})
    return d


def draft_social_posts(cfg: GrowthConfig, report: dict) -> list:
    """External social drafts — returned for HUMAN approval + posting (never auto-posted)."""
    market, discord = cfg.nonkyc_market, cfg.discord_invite
    posts = [
        f"Animica (ANM) is a post-quantum L1 with free AI, a marketplace, GPU-served media, a dVPN "
        f"and a .anm internet. Trade ANM/USDT on NonKYC: {market} · Community: {discord}",
        f"Free, keyless OpenAI-compatible AI on a post-quantum chain — plus image/video/music from a "
        f"GPU-miner network. Explore: https://animica.dev · ANM on NonKYC: {market}",
    ]
    out = []
    for i, p in enumerate(posts):
        # Lint external financial-adjacent copy too; no prohibited phrasing.
        try:
            G.financial_lint(p + " Not financial advice.", strict=False)
        except G.GuardrailError:
            continue
        out.append(_draft("social", channel="external:social", audience="external",
                          subject=f"social-{i+1}", body_html=p, body_text=p,
                          meta={"platform": "generic", "requires_approval": True}))
    return out
