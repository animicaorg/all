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
    # Detects a system ffmpeg OR the imageio-ffmpeg binary bundled by the base
    # `imageio[ffmpeg]` dep, so a plain `pip install animica` box can serve
    # image->video / multi-scene with no system ffmpeg package.
    from .base import resolve_ffmpeg
    return resolve_ffmpeg() is not None


def _have_cuda() -> bool:
    try:
        import torch  # type: ignore
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _have_audio_backend() -> bool:
    """True when the MusicGen audio backend can load (audio_gen.py needs transformers+torch).
    A box must never advertise `audio` it can't actually render — VRAM alone is not enough."""
    try:
        import importlib.util
        return (importlib.util.find_spec("transformers") is not None
                and importlib.util.find_spec("torch") is not None)
    except Exception:
        return False


def _vram_gb() -> float:
    """Total VRAM of the primary CUDA device in GiB (0.0 if no CUDA)."""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            _free, total = torch.cuda.mem_get_info()
            return float(total) / (1024 ** 3)
    except Exception:
        pass
    return 0.0


def _env_flag(name: str):
    """Tri-state env flag: True/False if explicitly set to a value, else None
    (auto). An unset OR empty value means 'auto', not force-off."""
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return None
    return v.strip().lower() not in ("0", "false", "no", "off")


# Conservative VRAM floors so a box only advertises a heavy kind it can actually
# run — over-advertising makes the miner claim jobs it will fail. Override the
# whole decision per-kind with ANIMICA_MEDIA_VIDEO_ENABLED / _AUDIO_ENABLED.
_T2V_MIN_VRAM_GB = 10.0   # ali-vilab/text-to-video-ms-1.7b fp16 ≈ 8 GiB
_AUDIO_MIN_VRAM_GB = 6.0  # facebook/musicgen-small ≈ 3 GiB

# Approx VRAM (GiB) each tier's model needs. The JOB requester picks the tier, so
# a box that merely clears the auto-enable floor could still be handed an
# elite/premium model that OOMs — clamp the tier down to what this box can run.
_TIER_VRAM_NEED = {
    "video_t2v": {"elite": 28.0, "premium": 10.0, "standard": 10.0},
    "audio": {"elite": 16.0, "premium": 8.0, "standard": 6.0},
}
_TIER_ORDER = ["elite", "premium", "standard"]


def _clamp_tier(kind: str, tier: Optional[str]) -> str:
    """Downgrade the requested tier to the best one this box's VRAM can run, so an
    auto-enabled miner never OOMs on a heavier tier than it advertised for. With
    no CUDA (vram==0, e.g. an env-forced CPU run) the tier is left untouched."""
    default = "premium" if kind == "video_t2v" else "standard"
    t = (tier or default).strip().lower()
    if t not in _TIER_ORDER:
        return default
    vram = _vram_gb()
    if vram <= 0:
        return t
    table = _TIER_VRAM_NEED.get(kind, {})
    for cand in _TIER_ORDER[_TIER_ORDER.index(t):]:
        if vram >= table.get(cand, 0.0):
            return cand
    return _TIER_ORDER[-1]


def probe_capabilities() -> List[str]:
    """Kinds this box can serve. The heavy generative models (t2v/audio) ship in
    the base install (diffusers/transformers), so on a capable GPU they are now
    auto-enabled — no manual env flag needed — while a CPU/small-VRAM box keeps
    them OFF (it would only fail the claimed job). Set ANIMICA_MEDIA_VIDEO_ENABLED
    / ANIMICA_MEDIA_AUDIO_ENABLED to 1/0 to force a kind on/off."""
    # Global media opt-out: honor it for EVERY kind (not just the image-backed
    # ones), so a node with ANIMICA_MEDIA_DISABLE=1 advertises nothing at all.
    if os.environ.get("ANIMICA_MEDIA_DISABLE") == "1":
        return []

    caps: List[str] = []
    img_ok, _ = media_available()
    ffmpeg = _have_ffmpeg()
    cuda = _have_cuda()
    vram = _vram_gb() if cuda else 0.0

    if img_ok:
        caps.append("image")
    if ffmpeg:
        # image->video works from uploaded stills with ffmpeg alone (no model needed).
        caps.append("video_i2v")
        if img_ok:
            caps.append("video_multiscene")  # a generated still per scene, then ffmpeg

    video_on = _env_flag("ANIMICA_MEDIA_VIDEO_ENABLED")
    if video_on is None:
        video_on = img_ok and cuda and vram >= _T2V_MIN_VRAM_GB
    if video_on and img_ok:
        caps.append("video_t2v")

    audio_on = _env_flag("ANIMICA_MEDIA_AUDIO_ENABLED")
    if audio_on is None:
        audio_on = cuda and vram >= _AUDIO_MIN_VRAM_GB
    # Qualify the model, not just the VRAM: only advertise `audio` when the MusicGen backend
    # is actually present, so an auto-enabled (or env-forced) box never claims an audio job
    # it will fail. Proper model for the proper kind.
    if audio_on and not _have_audio_backend():
        audio_on = False
    if audio_on:
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
        out = audio_gen.generate_audio(prompt, tier=_clamp_tier("audio", tier), seconds=float(params.get("seconds", 8)))
        return _pack(out["bytes"], out.get("mime", "audio/wav"), {"model": out.get("model"), "device": _device()}, "wav")

    if kind == "video_t2v":
        from . import video_gen
        fps = int(params.get("fps", 24))
        seconds = float(params.get("seconds", 4))
        num_frames = max(8, min(int(fps * seconds), 240))
        out = video_gen.generate_text_to_video(prompt, tier=_clamp_tier("video_t2v", tier), num_frames=num_frames, fps=fps)
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
            "this box can serve no media kinds. `pip install animica` bundles ffmpeg "
            "(via imageio-ffmpeg) and the diffusers/transformers stack, so image->video "
            "and multi-scene work once a diffusers image backend loads; text->video and "
            "audio auto-enable on a CUDA GPU with enough VRAM (or force them with "
            "ANIMICA_MEDIA_VIDEO_ENABLED=1 / ANIMICA_MEDIA_AUDIO_ENABLED=1)")
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
