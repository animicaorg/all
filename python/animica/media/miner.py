"""Media miner — the claim loop that turns a GPU (or even a CPU+ffmpeg) box into a media
renderer for the Animica queue.

No model runs on the gateway. A miner registers with the gateway, long-polls for the next
media job it can serve, renders it locally, and posts the bytes back. A job therefore GOES
THROUGH EVENTUALLY — it waits in the queue until a miner like this claims it.

Capabilities are probed from what's actually installed, so the miner never advertises work it
can't do:
  * ffmpeg present            -> image->video (Ken Burns from the uploaded stills; NO model)
  * image backend (diffusers) -> image + multi-scene video (a still per scene, then ffmpeg)
  * ANIMICA_MEDIA_VIDEO_ENABLED=1 -> text->video (GPU)
  * ANIMICA_MEDIA_AUDIO_ENABLED=1 -> music/audio (GPU)

Run:  animica media serve --register --gateway https://animica.dev
Private image->video: the uploaded images arrive only in this miner's claim response, are held
in memory, and are never written anywhere but the temp files ffmpeg needs (removed immediately).
"""

from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import time
import urllib.error
import urllib.request
from typing import List, Optional

from .base import MediaError, MediaBackendUnavailable, media_available, sha3_hex, validate_magic

_STATE_DIR = os.path.expanduser("~/.animica")
_STATE_FILE = os.path.join(_STATE_DIR, "media-miner.json")


# ── capability probe ─────────────────────────────────────────────────────────
def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _have_cuda() -> bool:
    try:
        import torch  # type: ignore
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def probe_capabilities() -> List[str]:
    caps: List[str] = []
    img_ok, _ = media_available()
    ffmpeg = _have_ffmpeg()
    if img_ok:
        caps.append("image")
    if ffmpeg:
        # image->video works from uploaded stills with ffmpeg alone (no model needed).
        caps.append("video_i2v")
        if img_ok:
            caps.append("video_multiscene")  # a generated still per scene, then ffmpeg
    if os.environ.get("ANIMICA_MEDIA_VIDEO_ENABLED") == "1" and img_ok:
        caps.append("video_t2v")
    if os.environ.get("ANIMICA_MEDIA_AUDIO_ENABLED") == "1" and img_ok:
        caps.append("audio")
    # de-dup, keep order
    seen, out = set(), []
    for c in caps:
        if c not in seen:
            seen.add(c); out.append(c)
    return out


def _device() -> str:
    return "cuda" if _have_cuda() else "cpu"


# ── rendering ────────────────────────────────────────────────────────────────
def _decode(data_or_url: str) -> bytes:
    s = data_or_url
    if s.startswith("data:"):
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def _pack(data: bytes, mime: str, meta: dict, magic: str) -> dict:
    if not validate_magic(data, magic):
        raise MediaError(f"rendered output failed {magic} validation")
    return {"b64": base64.b64encode(data).decode("ascii"), "mime": mime, "sha3": sha3_hex(data), "meta": meta}


def render_job(job: dict) -> dict:
    """Render one claimed job to bytes. Fail-closed: returns real media or raises."""
    kind = job.get("kind")
    prompt = job.get("prompt") or ""
    params = job.get("params") or {}
    images = job.get("images") or []
    tier = params.get("tier")

    if kind == "image":
        from . import image_gen
        out = image_gen.generate_image(
            prompt, tier=tier or "standard",
            width=int(params.get("width", 512)), height=int(params.get("height", 512)),
            seed=params.get("seed"), negative_prompt=params.get("negative_prompt"),
        )
        return _pack(out["bytes"], out.get("mime", "image/png"),
                     {"model": out.get("model"), "device": out.get("device", _device())}, "png")

    if kind == "audio":
        from . import audio_gen
        out = audio_gen.generate_audio(prompt, tier=tier or "standard", seconds=float(params.get("seconds", 8)))
        return _pack(out["bytes"], out.get("mime", "audio/wav"), {"model": out.get("model"), "device": _device()}, "wav")

    if kind == "video_t2v":
        from . import video_gen
        fps = int(params.get("fps", 24))
        seconds = float(params.get("seconds", 4))
        num_frames = max(8, min(int(fps * seconds), 240))
        out = video_gen.generate_text_to_video(prompt, tier=tier or "premium", num_frames=num_frames, fps=fps)
        return _pack(out["bytes"], out.get("mime", "video/mp4"), {"model": out.get("model"), "device": _device()}, "mp4")

    if kind == "video_i2v":
        frames = [_decode(s) for s in images if s]
        if not frames:
            raise MediaError("image->video requires at least one uploaded image")
        from .scene_video import assemble_scene_video
        fps = int(params.get("fps", 24))
        seconds = float(params.get("seconds", 4))
        per = max(1.0, seconds / max(1, len(frames)))
        out = assemble_scene_video(
            frames, fps=fps, seconds_per_scene=per,
            transition=params.get("transition", "fade"), ken_burns=True,
        )
        return _pack(out["bytes"], out["mime"],
                     {"model": "anm-i2v-kenburns", "device": _device(), "scenes": out["scenes"], "duration_s": out["duration_s"]}, "mp4")

    if kind == "video_multiscene":
        from . import image_gen
        from .scene_video import assemble_scene_video, plan_scenes
        scenes = params.get("scenes") or plan_scenes(prompt)
        scenes = [s for s in scenes if s][:8]
        if not scenes:
            raise MediaError("multi-scene video needs at least one scene")
        stills: List[bytes] = []
        for sc in scenes:
            r = image_gen.generate_image(sc, tier=tier or "standard", width=768, height=432)
            stills.append(r["bytes"])
        out = assemble_scene_video(
            stills, width=768, height=432,
            seconds_per_scene=float(params.get("seconds_per_scene", 2.5)),
            transition=params.get("transition", "fade"), ken_burns=True,
        )
        return _pack(out["bytes"], out["mime"],
                     {"model": "anm-multiscene", "device": _device(), "scenes": out["scenes"], "duration_s": out["duration_s"]}, "mp4")

    raise MediaError(f"this miner cannot render job kind {kind!r}")


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _req(url: str, payload: Optional[dict], bearer: Optional[str], method: str = "POST", timeout: float = 120.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            code = r.getcode()
            body = r.read()
            if code == 204 or not body:
                return code, None
            return code, json.loads(body.decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


def _load_token() -> Optional[str]:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f).get("token")
    except Exception:
        return None


def _save_token(token: str, gateway: str) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump({"token": token, "gateway": gateway}, f)
        os.chmod(_STATE_FILE, 0o600)
    except Exception:
        pass


# ── run loop ─────────────────────────────────────────────────────────────────
def run_miner(gateway: str, *, token: Optional[str] = None, label: Optional[str] = None,
              caps: Optional[List[str]] = None, poll_interval: float = 3.0,
              once: bool = False, log=print) -> None:
    gateway = gateway.rstrip("/")
    base = f"{gateway}/api/mkt/v1/media"
    caps = caps or probe_capabilities()
    if not caps:
        raise MediaBackendUnavailable(
            "this box can serve no media kinds — install ffmpeg (image->video) and/or "
            "`pip install 'animica[media]'` (image + multi-scene), and set "
            "ANIMICA_MEDIA_VIDEO_ENABLED=1 / ANIMICA_MEDIA_AUDIO_ENABLED=1 for GPU video/audio")
    token = token or _load_token()
    dev = _device()
    label = label or f"{platform.node()[:32]}·{dev}"

    code, reg = _req(f"{base}/miner/register",
                     {"token": token, "label": label, "capabilities": caps, "device": dev,
                      "maxPixels": int(os.environ.get("ANIMICA_MEDIA_MAX_PIXELS", 1024 * 1024))},
                     bearer=None)
    if code != 200 or not reg:
        raise MediaError(f"registration failed ({code}): {reg}")
    if reg.get("token"):
        token = reg["token"]; _save_token(token, gateway)
    log(f"registered with {gateway} · caps={','.join(caps)} · device={dev} · miner={reg.get('miner_id')}")
    log(f"jobs_done={reg.get('jobs_done')} reward_nanm={reg.get('reward_nanm')} (IOU) — waiting for jobs…")

    idle = 0
    while True:
        code, res = _req(f"{base}/miner/claim", {"device": dev, "load": 0.0}, bearer=token, timeout=40)
        if code == 401:
            # token no longer known (gateway reset) — re-register
            code, reg = _req(f"{base}/miner/register", {"label": label, "capabilities": caps, "device": dev}, bearer=None)
            if reg and reg.get("token"):
                token = reg["token"]; _save_token(token, gateway)
            time.sleep(poll_interval); continue
        job = (res or {}).get("job") if res else None
        if not job:
            idle += 1
            if once and idle > 1:
                log("no jobs in queue — exiting (--once)"); return
            time.sleep(poll_interval); continue
        idle = 0
        jid = job.get("id")
        log(f"claimed {jid} · {job.get('kind')} · '{(job.get('prompt') or '')[:48]}'"
            + (f" · {len(job.get('images') or [])} image(s)" if job.get('images') else ""))
        t0 = time.time()
        try:
            out = render_job(job)
            code, r = _req(f"{base}/miner/result",
                           {"job_id": jid, "ok": True, "b64": out["b64"], "mime": out["mime"],
                            "sha3": out["sha3"], "meta": out["meta"]},
                           bearer=token, timeout=180)
            log(f"  ✓ {jid} rendered in {time.time()-t0:.1f}s → {out['mime']} sha3={out['sha3'][:16]}… (post {code})")
        except Exception as e:  # fail closed — tell the gateway so it can requeue/fail
            _req(f"{base}/miner/result", {"job_id": jid, "ok": False, "error": str(e)[:300]}, bearer=token, timeout=30)
            log(f"  ✗ {jid} failed: {e}")
        if once:
            log("rendered one job — exiting (--once)"); return
