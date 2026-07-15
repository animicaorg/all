"""Content planning. Turns the operator's goals + real ecosystem facts into a per-platform content
plan. Captions are written by the free Animica AI gateway grounded on real facts, with an honest
rule-based fallback if the gateway is unavailable. Everything passes the caption linter before it
can be posted."""

from __future__ import annotations

import hashlib
import json
import random
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from .config import AnimalConfig
from . import persona, guardrails as G


@dataclass
class ContentPlan:
    platform: str
    kind: str          # text | image | video | music
    caption: str
    media_prompt: str = ""
    music_prompt: str = ""
    want_music: bool = False
    hashtags: List[str] = field(default_factory=list)
    content_hash: str = ""

    def finalize(self) -> "ContentPlan":
        basis = f"{self.platform}|{self.kind}|{self.caption}|{self.media_prompt}|{self.music_prompt}"
        self.content_hash = hashlib.sha256(basis.encode()).hexdigest()[:16]
        return self


_CHAT_TIMEOUT = 25  # seconds per gateway call; falls back to a grounded template on timeout


def _chat(cfg: AnimalConfig, system: str, user: str, max_tokens: int = 320) -> str:
    """Best-effort call to the free OpenAI-compatible gateway. Returns '' on any failure so the
    caller falls back to a grounded template (never fabricates)."""
    payload = {
        "model": "auto",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.8,
    }
    try:
        req = urllib.request.Request(
            f"{cfg.gateway_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_CHAT_TIMEOUT) as r:
            j = json.loads(r.read().decode())
        return (j.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception:
        return ""


def _topic(directives: List[dict]) -> str:
    goals = [d["text"] for d in directives if d.get("role") == "operator"]
    if goals:
        return goals[-1]
    return random.choice(persona.content_pillars())


def _fallback_caption(cfg: AnimalConfig, topic: str, facts: List[dict]) -> str:
    fact = ""
    for f in facts:
        if f.get("value") not in (None, "", "unavailable"):
            fact = f" ({f['label']}: {f['value']} {f.get('unit','')})".rstrip()
            break
    return (f"🐾 {persona.PERSONA['name']} here! Today: {topic}.{fact} "
            f"Come build the post-quantum internet with us. {persona.brand_footer(cfg)} "
            + " ".join(persona.PERSONA["hashtags"][:3]))


def plan_for_channel(cfg: AnimalConfig, channel: dict, directives: List[dict], facts: List[dict]) -> Optional[ContentPlan]:
    platform = channel["platform"]
    topic = _topic(directives)
    system = persona.system_prompt(cfg, platform, directives, facts)

    # Text caption (grounded; fallback template if the gateway is down).
    limit = {"x": 260, "reddit": 500, "mastodon": 480}.get(platform, 300)
    ai = _chat(cfg, system, f"Write ONE {platform} post about: {topic}. <= {limit} chars. "
                            f"Include the Discord link where natural. No hashtags spam (max 3).")
    caption = ai or _fallback_caption(cfg, topic, facts)
    caption = caption.strip()[: limit + 120]

    # Choose media kind from what the platform accepts.
    media = channel.get("media") or ["text"]
    wants_video = "video" in media
    wants_image = "image" in media
    wants_music = bool(channel.get("supportsMusic")) and wants_video

    if wants_video:
        media_prompt = _chat(cfg, system,
                             f"Describe, in one vivid sentence, a short vertical (9:16) animated clip "
                             f"of the Animica cat mascot about: {topic}. Visual only.") \
            or f"A glowing quantum cat mascot celebrates {topic}, neon post-quantum vibes, vertical 9:16"
        music_prompt = ""
        if wants_music:
            music_prompt = _chat(cfg, system,
                                f"Describe a short upbeat instrumental music bed (one line, genre + mood) "
                                f"for a fun 8-second clip about: {topic}. No lyrics.") \
                or "Upbeat playful chiptune-synth instrumental, cheerful, 8 seconds, no vocals"
        plan = ContentPlan(platform, "video", caption, media_prompt=media_prompt.strip(),
                           music_prompt=music_prompt.strip(), want_music=wants_music)
    elif wants_image:
        media_prompt = _chat(cfg, system,
                            f"Describe one eye-catching square image of the Animica cat mascot about: {topic}.") \
            or f"The glowing quantum cat mascot of Animica, {topic}, vibrant, poster style"
        plan = ContentPlan(platform, "image", caption, media_prompt=media_prompt.strip())
    else:
        plan = ContentPlan(platform, "text", caption)

    plan.hashtags = persona.PERSONA["hashtags"][:3]

    # Safety: caption must pass the linter (financial disclaimer added if it touches trading).
    try:
        G.lint_caption(plan.caption)
    except G.GuardrailError:
        # Fall back to a clean, non-financial caption rather than posting something that fails lint.
        plan.caption = (f"🐾 {persona.PERSONA['name']}: {topic}. Build the post-quantum internet with us — "
                        f"{persona.brand_footer(cfg)}")
        G.lint_caption(plan.caption)
    return plan.finalize()


def plan_all(cfg: AnimalConfig, channels: List[dict], directives: List[dict], facts: List[dict]) -> List[ContentPlan]:
    plans = []
    for ch in channels:
        try:
            p = plan_for_channel(cfg, ch, directives, facts)
            if p:
                plans.append(p)
        except Exception:
            continue
    return plans
