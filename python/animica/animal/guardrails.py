"""Animica Animal safety spine. Reuses the Growth Engine's financial linter + metric-honesty check
and adds mascot-specific gates: live-post authorization, per-platform daily caps, and the
owned-channel-only rule. These are code, not config — no env flag turns them off."""

from __future__ import annotations

from typing import Iterable

# Reuse the already-tested guardrails from the growth package so there is ONE linter, not two.
from animica.growth.guardrails import (  # noqa: F401
    GuardrailError,
    financial_lint,
    assert_no_fabricated_metrics,
)

# Any caption that touches trading/price/returns must carry the risk disclaimer (strict lint).
# This is deliberately broad — a tiny allow-list let pump-adjacent phrasing ("gains", "load up your
# bags", "moon") slip past the disclaimer requirement. Ordered as whole-ish tokens/substrings.
_MENTIONS_TRADE = (
    "buy", "sell", "trade", "swap", "nonkyc", "price", "invest", "investment",
    "hodl", "hold", "accumulate", "bag", "bags", "moon", "gains", "gainz", "profit",
    "roi", "returns", "airdrop", "presale", "pre-sale", "listing", "token sale",
    "ape", "load up", "pump", "dump", "dip", "leg up", "portfolio", "market cap", "mcap",
    "cheap", "undervalued", "x return", "10x", "moonshot", "wen", "lambo",
)


def lint_caption(text: str) -> None:
    """Every outgoing caption passes the financial linter. If it references the token/project in a
    trading/returns context it must ALSO carry the risk disclaimer (strict); otherwise pump phrasing
    alone (via financial_lint's prohibited-phrase regex) is enough to reject."""
    t = (text or "")
    low = t.lower()
    touches_finance = any(k in low for k in _MENTIONS_TRADE)
    # Referencing ANM/$ANM/#ANM alongside any finance term is the strict case; a plain "$ANM" or
    # "ANM" mention on its own is fine (the mascot names the token constantly and shouldn't need a
    # disclaimer on every post), but the moment it co-occurs with a trading/returns word, require it.
    financial_lint(t, strict=touches_finance)


def assert_can_live_post(cfg) -> None:
    """Live posting needs BOTH switches. The default posture (dry_run=True, allow_live_post=False)
    can never post to a real platform — it only renders previews."""
    if cfg.dry_run:
        raise GuardrailError("ANIMAL_DRY_RUN is on — refusing to post live (previews only)")
    if not cfg.allow_live_post:
        raise GuardrailError("ANIMAL_ALLOW_LIVE_POST is off — refusing to post live")


def assert_owned_channel(channel: dict) -> None:
    """A channel is postable only if it is CONNECTED (operator linked an owned account). There is no
    code path that posts to an account that was not connected in the console."""
    if channel.get("status") != "CONNECTED":
        raise GuardrailError(f"channel {channel.get('platform')!r} is not a connected owned account")


def within_caps(platform_count_today: int, global_count_today: int, cfg) -> bool:
    return (platform_count_today < cfg.per_platform_daily_cap) and (global_count_today < cfg.global_daily_cap)


def redact(tokens: Iterable[str], text: str) -> str:
    """Belt-and-suspenders: never let an access token leak into a log line."""
    out = text or ""
    for t in tokens:
        if t and len(t) >= 6:
            out = out.replace(t, "***")
    return out
