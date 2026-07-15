"""The always-run spine. Each cycle: read connected channels + operator goals from the console,
ground on real metrics, plan a post per channel, render its media through the queue, then dry-run
(default) or live-post to the owned account — honoring the pause switch and daily caps throughout.
Exceptions never break the loop; the mascot keeps running."""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.request
import urllib.error
from typing import List, Optional

from .config import AnimalConfig, load
from . import content as C, platforms as P, media as M, store, guardrails as G


def _internal(cfg: AnimalConfig, path: str, method: str = "GET", body: Optional[dict] = None, timeout: int = 30) -> Optional[dict]:
    if not cfg.internal_token:
        return None
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{cfg.internal_base}{path}",
        data=data,
        headers={"authorization": f"Bearer {cfg.internal_token}", "content-type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _facts(cfg: AnimalConfig) -> List[dict]:
    """Real ecosystem facts, reusing the Growth Engine collectors. Honest: only OK metrics; nothing
    fabricated. Returns [] if collection fails."""
    try:
        from animica.growth import collectors as gc, analysis as ga
        from animica.growth.config import load as gload
        gcfg = gload()
        gcfg.mkt_url = cfg.mkt_url  # same marketplace
        snap = gc.snapshot(gcfg)
        rep = ga.analyze(snap)
        return rep.get("facts", [])
    except Exception:
        return []


def _render_media(cfg: AnimalConfig, plan: C.ContentPlan, log) -> Optional[M.MediaResult]:
    if plan.kind == "text":
        return None
    if plan.kind == "image":
        log(f"    rendering image via queue…")
        return M.generate(cfg, "image", plan.media_prompt, {"width": 768, "height": 768}, timeout_secs=cfg.media_timeout_secs)
    if plan.kind == "video":
        log(f"    rendering vertical video via queue…")
        vid = M.generate(cfg, "video_t2v", plan.media_prompt,
                         {"width": 576, "height": 1024, "fps": 24, "seconds": 6}, timeout_secs=cfg.media_timeout_secs)
        if vid is None:
            return None
        if plan.want_music and plan.music_prompt:
            log(f"    rendering custom music track…")
            music = M.generate(cfg, "audio", plan.music_prompt, {"seconds": 8}, timeout_secs=cfg.media_timeout_secs)
            if music is not None and M.has_ffmpeg():
                muxed = M.mux_video_music(vid, music)
                if muxed:
                    log("    muxed video + custom music")
                    return M.MediaResult("video", muxed, "video/mp4", vid.job_id,
                                         {**vid.meta, "music_job": music.job_id})
        return vid
    return None


def run_once(cfg: Optional[AnimalConfig] = None, *, log=print) -> dict:
    cfg = cfg or load()
    state = _internal(cfg, "/state")
    if state is None:
        log("engine: cannot reach console internal endpoint (ANIMAL_INTERNAL_TOKEN set + marketplace up?)")
        return {"ok": False, "reason": "no_console"}

    if state.get("paused"):
        log("engine: paused by operator — skipping this cycle")
        _internal(cfg, "/heartbeat", "POST", {"dryRun": cfg.dry_run, "cycle": {"paused": True}})
        return {"ok": True, "paused": True}

    channels = state.get("channels", [])
    directives = state.get("directives", [])

    # The operator can force previews-only from the console (control.forceDryRun). Honor it by
    # running this cycle with an effective dry-run config — the console can always TIGHTEN, and it
    # never widens posting authority (going live still needs the on-box env double-gate).
    if state.get("forceDryRun") and not cfg.dry_run:
        log("engine: operator forced dry-run from the console — previews only this cycle")
        cfg = dataclasses.replace(cfg, dry_run=True)

    posture = "LIVE" if (not cfg.dry_run and cfg.allow_live_post) else "dry-run"
    log(f"engine cycle · {len(channels)} channel(s) · posture={posture}")

    if not channels:
        log("  no connected channels yet — connect socials in the console (/animal)")
        _internal(cfg, "/heartbeat", "POST", {"dryRun": cfg.dry_run, "cycle": {"channels": 0}})
        return {"ok": True, "channels": 0}

    facts = _facts(cfg)
    plans = C.plan_all(cfg, channels, directives, facts)
    ch_by_platform = {c["platform"]: c for c in channels}
    produced, skipped = 0, 0

    for plan in plans:
        pc, gc_ = store.counts(cfg.db_path(), plan.platform)
        if not G.within_caps(pc, gc_, cfg):
            log(f"  {plan.platform}: daily cap reached — skipping")
            skipped += 1
            continue
        if store.is_seen(cfg.db_path(), plan.content_hash):
            skipped += 1
            continue

        log(f"  {plan.platform}: {plan.kind} — “{plan.caption[:70]}…”")
        channel = ch_by_platform[plan.platform]
        media = _render_media(cfg, plan, log)
        if plan.kind != "text" and media is None:
            # No GPU miner rendered it in time. If the channel can carry text, post the caption now
            # (keeps the stream alive); otherwise wait for the next cycle.
            if "text" in (channel.get("media") or []):
                log(f"    media not ready (no miner) — falling back to a text post")
                plan.kind = "text"
            else:
                log(f"    media not ready (no miner yet) — will retry next cycle")
                continue

        outcome = P.post(cfg, plan, channel, media)
        store.mark_seen(cfg.db_path(), plan.content_hash)
        if outcome.status in ("POSTED", "DRY_RUN"):
            store.bump(cfg.db_path(), plan.platform)
            produced += 1

        _internal(cfg, "/post", "POST", {
            "platform": plan.platform, "kind": plan.kind, "status": outcome.status,
            "caption": plan.caption, "mediaJobId": outcome.media_job_id, "mediaKind": plan.kind,
            "externalId": outcome.external_id, "externalUrl": outcome.external_url, "error": outcome.error,
            "meta": {"preview": outcome.preview_path, "content_hash": plan.content_hash, "posture": posture},
        })
        log(f"    -> {outcome.status}" + (f" ({outcome.error})" if outcome.error else ""))

    summary = {"channels": len(channels), "produced": produced, "skipped": skipped, "posture": posture, "ts": int(time.time())}
    _internal(cfg, "/heartbeat", "POST", {"dryRun": cfg.dry_run, "cycle": summary})
    return {"ok": True, **summary}


def run_forever(cfg: Optional[AnimalConfig] = None, *, log=print) -> None:
    cfg = cfg or load()
    log(f"Animica Animal engine starting · interval={cfg.interval_secs}s · dry_run={cfg.dry_run} "
        f"allow_live_post={cfg.allow_live_post}")
    while True:
        try:
            run_once(cfg, log=log)
        except Exception as e:  # always run
            log(f"engine cycle error (continuing): {e}")
        time.sleep(max(60, cfg.interval_secs))
