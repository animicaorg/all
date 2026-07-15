"""Posting adapters. The DEFAULT path is dry-run: render the content to a local preview and record
it — nothing leaves the box. Live adapters call each platform's OFFICIAL API using the token the
operator connected for their OWN account; they only run when both live switches are on. There is no
account-creation, DM, follow, or per-user-targeting primitive here — the mascot only publishes its
own posts to its own connected channels."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from .config import AnimalConfig
from . import guardrails as G, media as M
from .content import ContentPlan


@dataclass
class PostOutcome:
    status: str                      # DRY_RUN | POSTED | FAILED | SKIPPED
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    error: Optional[str] = None
    preview_path: Optional[str] = None
    media_job_id: Optional[str] = None


def _http(method: str, url: str, *, headers: dict, data: Optional[bytes] = None, timeout: int = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ── dry-run ───────────────────────────────────────────────────────────────────
def dry_run(cfg: AnimalConfig, plan: ContentPlan, media: Optional[M.MediaResult]) -> PostOutcome:
    slug = f"{plan.platform}-{plan.content_hash}"
    preview = None
    if media is not None:
        preview = M.save_preview(cfg, media.data, media.ext(), slug)
    # Always drop a readable manifest next to it.
    manifest = os.path.join(cfg.preview_dir(), f"{slug}.json")
    with open(manifest, "w") as f:
        json.dump({
            "platform": plan.platform, "kind": plan.kind, "caption": plan.caption,
            "media_prompt": plan.media_prompt, "music_prompt": plan.music_prompt,
            "media_preview": preview, "content_hash": plan.content_hash,
        }, f, indent=2)
    return PostOutcome(status="DRY_RUN", preview_path=preview or manifest,
                       media_job_id=media.job_id if media else None)


# ── live adapters (official APIs, owned accounts) ─────────────────────────────
def _live_mastodon(cfg, plan, channel, media):
    base = os.environ.get("MASTODON_INSTANCE", "").rstrip("/")
    if not base:
        return PostOutcome(status="FAILED", error="MASTODON_INSTANCE unset")
    tok = channel["accessToken"]
    body = urllib.parse.urlencode({"status": plan.caption}).encode()
    code, raw = _http("POST", f"{base}/api/v1/statuses",
                      headers={"authorization": f"Bearer {tok}", "content-type": "application/x-www-form-urlencoded"},
                      data=body)
    if code // 100 == 2:
        j = json.loads(raw or b"{}")
        return PostOutcome(status="POSTED", external_id=str(j.get("id")), external_url=j.get("url"))
    return PostOutcome(status="FAILED", error=f"mastodon {code}: {raw[:160].decode(errors='replace')}")


def _live_x(cfg, plan, channel, media):
    tok = channel["accessToken"]
    body = json.dumps({"text": plan.caption}).encode()
    code, raw = _http("POST", "https://api.twitter.com/2/tweets",
                      headers={"authorization": f"Bearer {tok}", "content-type": "application/json"}, data=body)
    if code // 100 == 2:
        j = json.loads(raw or b"{}")
        tid = (j.get("data") or {}).get("id")
        return PostOutcome(status="POSTED", external_id=tid,
                           external_url=f"https://x.com/i/web/status/{tid}" if tid else None)
    return PostOutcome(status="FAILED", error=f"x {code}: {raw[:160].decode(errors='replace')}")


def _live_reddit(cfg, plan, channel, media):
    tok = channel["accessToken"]
    sub = os.environ.get("REDDIT_SUBREDDIT", "").strip()
    if not sub:
        return PostOutcome(status="FAILED", error="REDDIT_SUBREDDIT unset (a sub you moderate/permits self-posts)")
    title = plan.caption.split("\n")[0][:280]
    body = urllib.parse.urlencode({"sr": sub, "kind": "self", "title": title, "text": plan.caption}).encode()
    code, raw = _http("POST", "https://oauth.reddit.com/api/submit",
                      headers={"authorization": f"Bearer {tok}", "content-type": "application/x-www-form-urlencoded",
                               "user-agent": "animica-animal/1.0"}, data=body)
    if code // 100 == 2:
        return PostOutcome(status="POSTED", error=None)
    return PostOutcome(status="FAILED", error=f"reddit {code}: {raw[:160].decode(errors='replace')}")


def _live_tiktok(cfg, plan, channel, media):
    """TikTok Content Posting API — direct post via FILE_UPLOAD. Requires the connected account's
    token to hold video.publish. Unaudited apps can still post to the OWNER's own account (private).
    """
    if media is None:
        return PostOutcome(status="FAILED", error="tiktok needs a rendered video")
    tok = channel["accessToken"]
    vid = media.data
    size = len(vid)
    init_body = json.dumps({
        "post_info": {"title": plan.caption[:150], "privacy_level": os.environ.get("TIKTOK_PRIVACY", "SELF_ONLY")},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1},
    }).encode()
    code, raw = _http("POST", "https://open.tiktokapis.com/v2/post/publish/video/init/",
                      headers={"authorization": f"Bearer {tok}", "content-type": "application/json; charset=UTF-8"},
                      data=init_body)
    if code // 100 != 2:
        return PostOutcome(status="FAILED", error=f"tiktok init {code}: {raw[:160].decode(errors='replace')}")
    j = json.loads(raw or b"{}")
    data = j.get("data") or {}
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id")
    if not upload_url:
        return PostOutcome(status="FAILED", error=f"tiktok init: no upload_url ({raw[:120].decode(errors='replace')})")
    up_code, _ = _http("PUT", upload_url, headers={
        "content-type": "video/mp4",
        "content-length": str(size),
        "content-range": f"bytes 0-{size - 1}/{size}",
    }, data=vid)
    if up_code // 100 != 2:
        return PostOutcome(status="FAILED", error=f"tiktok upload {up_code}", external_id=publish_id)
    return PostOutcome(status="POSTED", external_id=publish_id)


def _not_wired(name):
    def _adapter(cfg, plan, channel, media):
        return PostOutcome(status="FAILED",
                           error=f"live posting for {name} needs its media-upload pipeline; preview saved")
    return _adapter


LIVE_ADAPTERS = {
    "mastodon": _live_mastodon,
    "x": _live_x,
    "reddit": _live_reddit,
    "tiktok": _live_tiktok,
    "youtube": _not_wired("youtube"),      # resumable upload — render preview, publish manually for now
    "instagram": _not_wired("instagram"),  # container+publish flow — same
}


def post(cfg: AnimalConfig, plan: ContentPlan, channel: dict, media: Optional[M.MediaResult]) -> PostOutcome:
    # Default posture: preview only. Never posts live unless BOTH switches are on.
    if cfg.dry_run or not cfg.allow_live_post:
        return dry_run(cfg, plan, media)
    try:
        G.assert_can_live_post(cfg)
        G.assert_owned_channel(channel)
    except G.GuardrailError as e:
        return PostOutcome(status="SKIPPED", error=str(e))
    adapter = LIVE_ADAPTERS.get(plan.platform)
    if not adapter:
        return PostOutcome(status="FAILED", error=f"no adapter for {plan.platform}")
    try:
        out = adapter(cfg, plan, channel, media)
    except Exception as e:  # never let one platform take down the loop
        toks = [channel.get("accessToken", ""), channel.get("refreshToken", "")]
        out = PostOutcome(status="FAILED", error=G.redact(toks, str(e))[:200])
    if media is not None:
        out.media_job_id = out.media_job_id or media.job_id
    return out
