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


def _have_module(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _can_import(name: str) -> bool:
    """A REAL import probe. find_spec is not enough for the torch-family packages: a
    torchaudio/torchvision wheel built against a different torch raises at import time
    ('operator torchvision::nms does not exist'), and advertising a capability that
    then always fails at claim time burns every job attempt."""
    try:
        import importlib
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _blender_available() -> bool:
    """Blender is resolvable now, or auto-fetchable on this platform (probe never downloads)."""
    try:
        from .render_farm import resolve_blender
        if resolve_blender(auto_fetch=False):
            return True
    except Exception:
        return False
    if os.environ.get("ANIMICA_BLENDER_AUTOFETCH", "1") == "0":
        return False
    return platform.system() == "Linux" and platform.machine() in ("x86_64", "AMD64")


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
# Stable Video Diffusion img2vid with model-CPU-offload + VAE slicing fits ~10-12
# GiB. Below this floor, i2v uses the CPU-friendly Ken Burns render instead of a
# generative model it can't hold. Override with ANIMICA_MEDIA_I2V_MODEL_ENABLED.
_I2V_MIN_VRAM_GB = 12.0
# 9.0.0 studio kinds (SR/RIFE/DeepLab/HDemucs) fit comfortably in small VRAM.
_STUDIO_MIN_VRAM_GB = 4.0

# Approx VRAM (GiB) each tier's model needs. The JOB requester picks the tier, so
# a box that merely clears the auto-enable floor could still be handed an
# elite/premium model that OOMs — clamp the tier down to what this box can run.
_TIER_VRAM_NEED = {
    "video_t2v": {"elite": 28.0, "premium": 10.0, "standard": 10.0},
    "audio": {"elite": 16.0, "premium": 8.0, "standard": 6.0},
    # FLUX.1-schnell needs ~24 GiB resident (offload can squeeze it but takes minutes);
    # sdxl-turbo ~7 GiB; sd-turbo runs anywhere (offload ladder / CPU).
    "image": {"elite": 24.0, "premium": 8.0, "standard": 0.0},
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
        # 11.1.0: the video director renders text->video on ANY image-capable box — a real
        # t2v diffusion model on a big GPU, keyframe+SVD on a mid GPU, depth-parallax
        # camera moves over judged keyframes on CPU (ANIMICA_MEDIA_T2V_CPU=0 opts out).
        video_on = img_ok and ffmpeg and (
            (cuda and vram >= _T2V_MIN_VRAM_GB) or os.environ.get("ANIMICA_MEDIA_T2V_CPU", "1") != "0"
        )
    if video_on and img_ok:
        caps.append("video_t2v")
        caps.append("video_shot")        # one planned shot of a distributed video
    if ffmpeg:
        caps.append("video_assemble")    # join shots other miners rendered (ffmpeg only)

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

    # ── 9.0.0 GPU Studios (docs/gpu-studios-9.0.0.md) ────────────────────────
    # Video transforms: GPU-shaped (per-frame model inference) — auto-enable on a
    # CUDA box with modest VRAM; tri-state env forces on/off (CPU forced-on works,
    # just slowly). Each kind still qualifies its own model deps.
    vstudio = _env_flag("ANIMICA_MEDIA_VIDEOSTUDIO_ENABLED")
    if vstudio is None:
        vstudio = cuda and vram >= _STUDIO_MIN_VRAM_GB
    if vstudio and ffmpeg and _have_module("torch"):
        caps.append("video_upscale")
        caps.append("video_interpolate")  # RIFE when weights load; ffmpeg fallback inside
        if _can_import("torchvision"):
            caps.append("video_bgremove")
    if vstudio and ffmpeg and _have_module("transformers"):
        caps.append("video_subtitles")
    if ffmpeg:
        caps.append("video_shorts")  # scene detection + cuts are ffmpeg-only (subs optional)

    astudio = _env_flag("ANIMICA_MEDIA_AUDIOSTUDIO_ENABLED")
    if astudio is None:
        astudio = cuda and vram >= _STUDIO_MIN_VRAM_GB
    if astudio and ffmpeg and _can_import("torchaudio"):
        caps.append("audio_stems")
        caps.append("audio_isolate")
    if ffmpeg and _have_module("noisereduce") and _have_module("pyloudnorm"):
        # Pure-DSP kinds run fine on CPU — every pip-install box can serve them.
        caps.append("audio_enhance")
        caps.append("audio_master")

    # Render farm: Cycles on CPU is painfully slow — default to CUDA boxes; opt CPU
    # boxes in with ANIMICA_RENDER_CPU=1. Assembly is ffmpeg-only. Advertise
    # render_chunk ONLY when Blender is genuinely runnable NOW: at startup we resolve
    # it for real (PATH/env/cache, and if auto-fetch is enabled we actually download +
    # verify it here, once), so a box that merely *looks* eligible by platform but
    # can't fetch Blender never claims a render and burns every attempt of a user's job.
    render_on = _env_flag("ANIMICA_RENDER_ENABLED")
    if render_on is None:
        render_on = cuda or os.environ.get("ANIMICA_RENDER_CPU") == "1"
    if render_on:
        try:
            from .render_farm import resolve_blender
            if resolve_blender(auto_fetch=os.environ.get("ANIMICA_BLENDER_AUTOFETCH", "1") != "0"):
                caps.append("render_chunk")
        except Exception:
            pass  # cannot ready Blender → don't advertise render_chunk
    if ffmpeg:
        caps.append("render_assemble")

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


_STUDIO_KINDS = {
    "video_upscale", "video_interpolate", "video_subtitles", "video_bgremove", "video_shorts",
    "audio_stems", "audio_isolate", "audio_enhance", "audio_master",
    "render_chunk", "render_assemble",
    "video_shot", "video_assemble",   # 11.1.0 distributed video (one shot per miner + assembler)
}


def _render_studio_job(job: dict, gw) -> dict:
    """One 9.0.0 GPU-Studio job: download the input file(s), transform, hand back a DISK
    artifact ({"path", "mime", "sha3", "meta", "_tmp"}). The caller streams the path via
    the result-file endpoint and then drops the "_tmp" TemporaryDirectory. Fail-closed."""
    import tempfile

    if gw is None:
        raise MediaError("studio kinds need the gateway client (register first)")
    kind = job.get("kind")
    jid = job.get("id")
    params = job.get("params") or {}
    urls = job.get("input_urls") or []

    tmp = tempfile.TemporaryDirectory(prefix="anmstudio_")
    # Keep-alive: long stages (whisper on a 30-min track, HDemucs on CPU, a heavy Cycles
    # frame) can be silent far longer than the claim lease. A daemon thread re-posts the
    # last progress every 2 min so the gateway never requeues a job that is still alive.
    import threading
    _last = {"pct": 2.0, "note": "working"}
    _stop = threading.Event()

    def _keepalive():
        while not _stop.wait(120.0):
            try:
                gw.post_progress(jid, _last["pct"], _last["note"])
            except Exception:
                pass

    _ka = threading.Thread(target=_keepalive, daemon=True)
    _ka.start()
    try:
        td = tmp.name
        gw.post_progress(jid, 2, "downloading input")
        inputs: List[str] = []
        for i, u in enumerate(urls):
            # A stable extension helps blender/ffmpeg pick the right demuxer.
            ext = ".blend" if kind == "render_chunk" else (".zip" if kind == "render_assemble" else (".mp4" if kind == "video_assemble" else ".bin"))
            p = os.path.join(td, f"input_{i}{ext}")
            gw.download_input(u, p)
            inputs.append(p)
        out_dir = os.path.join(td, "out")
        os.makedirs(out_dir, exist_ok=True)
        _inner = gw.progress_fn(jid, start=5.0, span=90.0)

        def progress(pct: float, note: str = "") -> None:
            _last["pct"] = 5.0 + 90.0 * max(0.0, min(100.0, pct)) / 100.0
            if note:
                _last["note"] = note
            _inner(pct, note)

        if kind in ("video_upscale", "video_interpolate", "video_subtitles", "video_bgremove", "video_shorts"):
            if not inputs:
                raise MediaError(f"{kind} needs one input video")
            from . import video_studio
            src = inputs[0]
            if kind == "video_upscale":
                out = video_studio.upscale_video(src, out_dir, scale=int(params.get("scale", 2)),
                                                 model=str(params.get("model", "fast")), progress=progress)
            elif kind == "video_interpolate":
                out = video_studio.interpolate_video(src, out_dir, factor=int(params.get("factor", 2)), progress=progress)
            elif kind == "video_subtitles":
                out = video_studio.subtitle_video(src, out_dir, language=str(params.get("language", "auto")),
                                                  burn_in=bool(params.get("burn_in", True)), progress=progress)
            elif kind == "video_bgremove":
                out = video_studio.remove_background(src, out_dir, mode=str(params.get("mode", "green")), progress=progress)
            else:
                out = video_studio.make_shorts(src, out_dir, count=int(params.get("count", 3)),
                                               duration=int(params.get("duration", 30)),
                                               aspect=str(params.get("aspect", "9:16")),
                                               subtitles=bool(params.get("subtitles", True)), progress=progress)
        elif kind in ("audio_stems", "audio_isolate", "audio_enhance", "audio_master"):
            if not inputs:
                raise MediaError(f"{kind} needs one input audio file")
            from . import audio_studio
            src = inputs[0]
            fmt = str(params.get("format", "mp3"))
            if kind == "audio_stems":
                out = audio_studio.separate_stems(src, out_dir, fmt=fmt, two_stem=False, progress=progress)
            elif kind == "audio_isolate":
                out = audio_studio.separate_stems(src, out_dir, fmt=fmt, two_stem=True, progress=progress)
            elif kind == "audio_enhance":
                out = audio_studio.enhance_audio(src, out_dir, denoise=bool(params.get("denoise", True)),
                                                 loudness=float(params.get("loudness", -16.0)), fmt=fmt, progress=progress)
            else:
                ref = inputs[1] if len(inputs) > 1 else None
                out = audio_studio.master_audio(src, out_dir, preset=str(params.get("preset", "streaming")),
                                                reference_path=ref, fmt=fmt, progress=progress)
        elif kind == "render_chunk":
            if not inputs:
                raise MediaError("render_chunk needs the .blend input")
            from . import render_farm
            out = render_farm.render_chunk(
                inputs[0], out_dir,
                frame_start=int(params.get("frame_start", 1)),
                frame_end=int(params.get("frame_end", 1)),
                frame_step=int(params.get("frame_step", 1)),
                resolution_percent=int(params.get("resolution_percent", 100)),
                samples=int(params["samples"]) if params.get("samples") is not None else None,
                progress=progress,
            )
        elif kind == "render_assemble":
            if not inputs:
                raise MediaError("render_assemble needs the chunk zips")
            from . import render_farm
            out = render_farm.assemble_video(inputs, out_dir, fps=int(params.get("fps", 24)),
                                             mode=str(params.get("mode", "mp4")), progress=progress)
        elif kind == "video_shot":
            from . import video_director
            shot = params.get("shot") or {}
            if not shot.get("prompt"):
                raise MediaError("video_shot needs a planned shot")
            out = video_director.render_shot(
                shot, out_dir, width=int(params.get("width") or 768), height=int(params.get("height") or 432),
                fps=int(params.get("fps") or 24),
                tier=_clamp_tier("video_t2v", params.get("tier")) if _have_cuda() else "standard",
                precision=str(params.get("precision") or "balanced"), negative_prompt=params.get("negative_prompt"),
                engine=params.get("engine"), references=params.get("references"), progress=progress, learner=_learner(),
            )
        elif kind == "video_assemble":
            from . import video_director
            shots = params.get("shots") or []
            if not inputs:
                raise MediaError("video_assemble needs the shot clips")
            # Shot metas (engine/model/fidelity per shot) ride along in each clip's job meta
            # on the gateway; the clip files themselves are what we join here.
            out = video_director.assemble_shots(inputs, shots, out_dir, fps=int(params.get("fps") or 24),
                                                transition=str(params.get("transition") or "fade"),
                                                shot_metas=params.get("shot_metas"), progress=progress)
        else:
            raise MediaError(f"unknown studio kind {kind!r}")

        _stop.set()
        gw.post_progress(jid, 97, "uploading result")
        return {**out, "_tmp": tmp}
    except Exception:
        _stop.set()
        tmp.cleanup()
        raise


def _opt_int(params: dict, k: str):
    v = params.get(k)
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _progress_fn(job: dict, gw):
    """Best-effort progress heartbeat for long director renders (extends the lease)."""
    if gw is None or not job.get("id"):
        return None
    last = [0.0]

    def _fn(pct: float, note: str):
        now = time.monotonic()
        if now - last[0] < 8.0:
            return
        last[0] = now
        try:
            gw.post_progress(job["id"], float(pct), note)
        except Exception:
            pass
    return _fn


def _learner():
    """The miner's self-teaching ledger (fidelity outcomes → better camera/prompt choices).
    Never required: returns None when unavailable."""
    try:
        from . import learning
        return learning.get_learner()
    except Exception:
        return None


def render_job(job: dict, gw=None) -> dict:
    """Render one claimed job to bytes. Fail-closed: returns real media or raises."""
    kind = job.get("kind")
    prompt = job.get("prompt") or ""
    params = job.get("params") or {}
    images = job.get("images") or []
    tier = params.get("tier")

    if kind == "image":
        from . import image_gen

        def _opt_float(k):
            v = params.get(k)
            try:
                return float(v) if v is not None and str(v).strip() != "" else None
            except (TypeError, ValueError):
                return None

        out = image_gen.generate_image(
            prompt, tier=_clamp_tier("image", tier),
            width=int(params.get("width", 512)), height=int(params.get("height", 512)),
            seed=_opt_int(params, "seed"), negative_prompt=params.get("negative_prompt"),
            steps=_opt_int(params, "steps"), guidance=_opt_float("guidance"),
            candidates=_opt_int(params, "candidates"),
            precision=str(params.get("precision") or "balanced"),
            learner=_learner(), references=params.get("references"),
        )
        # The full recipe travels back with the result so any image is reproducible and the
        # requester can see what was actually rendered (compiled prompt, seed, model, judge).
        meta = {k: out.get(k) for k in (
            "model", "seed", "steps", "guidance", "scheduler", "candidates", "render_size", "refined",
            "long_prompt", "precision", "prompt", "negative_prompt", "notes", "rerank", "scorer",
            "scores", "fidelity",
        ) if out.get(k) is not None}
        meta["device"] = _device()
        meta["strategy"] = out.get("device")
        return _pack(out["bytes"], out.get("mime", "image/png"), meta, "png")

    if kind == "audio":
        from . import audio_gen
        out = audio_gen.generate_audio(prompt, tier=_clamp_tier("audio", tier), seconds=float(params.get("seconds", 8)))
        return _pack(out["bytes"], out.get("mime", "audio/wav"), {"model": out.get("model"), "device": _device()}, "wav")

    if kind == "video_t2v":
        from . import video_director
        fps = int(params.get("fps", 24))
        seconds = float(params.get("seconds", 4))
        out = video_director.render_video(
            prompt, seconds=seconds, fps=fps,
            width=int(params.get("width", 768)), height=int(params.get("height", 432)),
            tier=_clamp_tier("video_t2v", tier) if _have_cuda() else "standard",
            precision=str(params.get("precision") or "balanced"),
            seed=_opt_int(params, "seed"), negative_prompt=params.get("negative_prompt"),
            transition=str(params.get("transition") or "fade"),
            engine=params.get("engine"), progress=_progress_fn(job, gw),
            learner=_learner(),
        )
        meta = dict(out["meta"]); meta["device"] = _device()
        return _pack(out["bytes"], out.get("mime", "video/mp4"), meta, "mp4")

    if kind == "video_i2v":
        frames = [_decode(s) for s in images if s]
        if not frames:
            raise MediaError("image->video requires at least one uploaded image")
        fps = int(params.get("fps", 24))
        seconds = float(params.get("seconds", 4))
        # Prefer a REAL generative image->video model (Stable Video Diffusion): it
        # actually TRANSFORMS the image's content — drifting clouds, rippling water,
        # subtle subject motion — not just a camera move. Use it when the GPU can
        # run it and the operator hasn't opted out; otherwise fall back to the Ken
        # Burns pan/zoom (which runs anywhere, even CPU) so i2v ALWAYS returns real
        # video. On a single uploaded image we animate it directly; SVD conditions
        # on one frame, so multi-image uploads animate the first and Ken-Burns the
        # rest would over-complicate — we animate the first when generative.
        _disable = os.environ.get("ANIMICA_MEDIA_DISABLE", "")
        want_gen = (_have_cuda()
                    and _vram_gb() >= _I2V_MIN_VRAM_GB
                    and os.environ.get("ANIMICA_MEDIA_I2V_MODEL_ENABLED", "1") != "0"
                    and _disable not in ("1", "all", "i2v", "video"))
        if want_gen:
            try:
                import io as _io
                from PIL import Image
                from . import video_gen
                img = Image.open(_io.BytesIO(frames[0])).convert("RGB")
                svfps = max(6, min(fps, 12))       # SVD clips read best at 6-12 fps
                out = video_gen.generate_image_to_video(
                    img, tier=_clamp_tier("video_i2v", tier),
                    num_frames=int(params.get("num_frames", 25)), fps=svfps,
                    seed=params.get("seed"),
                )
                return _pack(out["bytes"], out.get("mime", "video/mp4"),
                             {"model": out.get("model"), "device": _device(),
                              "mode": "generative-i2v"}, "mp4")
            except Exception:      # noqa: BLE001 — OOM/model/load → graceful Ken Burns
                pass               # fall through to the pan/zoom render below
        # 11.1.0: depth-parallax camera moves (foreground/background separate) over each
        # uploaded still — real 2.5D motion that runs on any CPU — instead of Ken Burns.
        import io as _io
        from PIL import Image
        from . import video_director
        stills = [Image.open(_io.BytesIO(b)).convert("RGB") for b in frames]
        w0, h0 = stills[0].size
        out = video_director.render_video(
            prompt or "uploaded image", seconds=seconds, fps=fps,
            width=int(params.get("width") or min(1280, w0 // 2 * 2)), height=int(params.get("height") or min(720, h0 // 2 * 2)),
            tier="standard", precision="fast", seed=_opt_int(params, "seed"),
            transition=str(params.get("transition") or "fade"), engine="parallax",
            progress=_progress_fn(job, gw), stills=stills, learner=_learner(),
        )
        meta = dict(out["meta"]); meta["device"] = _device(); meta["mode"] = "parallax"
        return _pack(out["bytes"], out["mime"], meta, "mp4")

    if kind in _STUDIO_KINDS:
        return _render_studio_job(job, gw)

    if kind == "video_multiscene":
        from . import video_director
        from .scene_video import plan_scenes
        scenes = params.get("scenes") or plan_scenes(prompt)
        scenes = [s for s in scenes if s][:8]
        if not scenes:
            raise MediaError("multi-scene video needs at least one scene")
        per = float(params.get("seconds_per_scene", 2.5))
        out = video_director.render_video(
            prompt, seconds=per * len(scenes), fps=int(params.get("fps", 24)),
            width=int(params.get("width", 768)), height=int(params.get("height", 432)),
            tier=_clamp_tier("video_t2v", tier) if _have_cuda() else "standard",
            precision=str(params.get("precision") or "balanced"), seed=_opt_int(params, "seed"),
            negative_prompt=params.get("negative_prompt"), scenes=scenes,
            transition=str(params.get("transition") or "fade"), engine=params.get("engine"),
            progress=_progress_fn(job, gw), learner=_learner(),
        )
        meta = dict(out["meta"]); meta["device"] = _device(); meta["scenes"] = len(scenes)
        return _pack(out["bytes"], out["mime"], meta, "mp4")

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


def _post_result_retry(base: str, token: Optional[str], payload: dict, log, tries: int = 5) -> Optional[int]:
    """POST an inline result, riding out transient failures (5xx, timeout, 429).

    Terminal statuses return immediately: 200 accepted; 403/404/409 mean the
    gateway will never take THIS post (job re-adopted, gone, or already done) —
    since 9.0.1 the gateway adopts a late successful result for an unclaimed job,
    so a 403 here is a real rejection, not a lease race. 413 means the artifact
    is bigger than the edge allows; retrying the same bytes cannot help."""
    delay = 5.0
    code = None
    for _ in range(max(1, tries)):
        try:
            code, _r = _req(f"{base}/miner/result", payload, bearer=token, timeout=180)
        except Exception as e:
            code = None
            log(f"  … result post failed ({e}) — retrying in {delay:.0f}s")
        if code == 200 or code in (403, 404, 409, 413):
            return code
        time.sleep(delay)
        delay = min(delay * 2, 60.0)
    return code


def _post_result_file_retry(gw, jid: str, out: dict, log, tries: int = 4) -> None:
    """Stream a studio artifact, retrying transient upload failures (the file is
    still on disk, so re-posting is free). Non-transient rejections re-raise."""
    delay = 5.0
    for attempt in range(max(1, tries)):
        try:
            gw.post_result_file(jid, out["path"], mime=out["mime"], sha3=out["sha3"], meta=out.get("meta"))
            return
        except MediaError as e:
            msg = str(e)
            # "rejected (4xx)" = the gateway examined and refused it — terminal.
            transient = not any(f"({c})" in msg for c in (400, 401, 403, 404, 409, 413))
            if not transient or attempt == max(1, tries) - 1:
                raise
            log(f"  … artifact upload failed ({msg[:120]}) — retrying in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 60.0)


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

    # Registration rides out gateway restarts/deploys: a hard exit here killed the
    # whole fleet's poll loops during a 5-hour gateway outage — a 5xx/network error
    # retries with backoff forever, only a 4xx (client-side problem) is terminal.
    delay = 5.0
    while True:
        try:
            code, reg = _req(f"{base}/miner/register",
                             {"token": token, "label": label, "capabilities": caps, "device": dev,
                              "address": os.environ.get("ANIMICA_MEDIA_REWARD_ADDRESS"),
                              "maxPixels": int(os.environ.get("ANIMICA_MEDIA_MAX_PIXELS", 1024 * 1024))},
                             bearer=None)
        except Exception as e:
            code, reg = None, {"error": str(e)[:200]}
        if code == 200 and reg:
            break
        if code is not None and 400 <= code < 500 and code != 429:
            raise MediaError(f"registration failed ({code}): {reg}")
        log(f"registration unavailable ({code if code is not None else reg.get('error')}) — "
            f"retrying in {delay:.0f}s (gateway restart/deploy?)")
        time.sleep(delay)
        delay = min(delay * 2, 60.0)
    if reg.get("token"):
        token = reg["token"]; _save_token(token, gateway)
    log(f"registered with {gateway} · caps={','.join(caps)} · device={dev} · miner={reg.get('miner_id')}")
    log(f"jobs_done={reg.get('jobs_done')} reward_nanm={reg.get('reward_nanm')} "
        f"settled_nanm={reg.get('settled_nanm', '0')} — IOUs settle on-chain from the block reward "
        f"(set ANIMICA_MEDIA_REWARD_ADDRESS to your anim1… address to get paid) — waiting for jobs…")

    from .net import GatewayClient
    gw = GatewayClient(gateway, token)

    idle = 0
    while True:
        # Transient network failures (DNS blip, gateway restart, timeout) must never kill
        # the loop — `animica up` runs this in a daemon thread that would silently die.
        try:
            code, res = _req(f"{base}/miner/claim", {"device": dev, "load": 0.0}, bearer=token, timeout=40)
        except Exception as e:
            log(f"  claim failed ({e}) — retrying in {max(poll_interval, 5)}s")
            time.sleep(max(poll_interval, 5)); continue
        if code == 401:
            # token no longer known (gateway reset) — re-register with the FULL profile
            # (address/maxPixels included, or IOUs accrue address-less after a reset).
            try:
                code, reg = _req(f"{base}/miner/register",
                                 {"label": label, "capabilities": caps, "device": dev,
                                  "address": os.environ.get("ANIMICA_MEDIA_REWARD_ADDRESS"),
                                  "maxPixels": int(os.environ.get("ANIMICA_MEDIA_MAX_PIXELS", 1024 * 1024))},
                                 bearer=None)
            except Exception:
                reg = None
            if reg and reg.get("token"):
                token = reg["token"]; _save_token(token, gateway)
                gw = GatewayClient(gateway, token)
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
        # Classic kinds render silently for far longer than the claim lease (a t2v
        # can run hours) — heartbeat every 2 min so the gateway keeps extending the
        # lease instead of requeuing a job that is still alive. Studio kinds already
        # run their own keepalive inside _render_studio_job.
        _ka_stop = None
        if job.get("kind") not in _STUDIO_KINDS:
            import threading
            _ka_stop = threading.Event()

            def _ka(jid=jid, ev=_ka_stop):
                pct = 5.0
                while not ev.wait(120.0):
                    pct = min(95.0, pct + 3.0)
                    try:
                        gw.post_progress(jid, pct, "rendering")
                    except Exception:
                        pass

            threading.Thread(target=_ka, daemon=True).start()
        try:
            out = render_job(job, gw)
            if _ka_stop is not None:
                _ka_stop.set()
            if out.get("path"):
                # 9.0.0 studio artifact — stream the file (no base64, no 48MB ceiling).
                tmp = out.pop("_tmp", None)
                try:
                    size = os.path.getsize(out["path"])
                    _post_result_file_retry(gw, jid, out, log)
                    code = 200
                finally:
                    if tmp is not None:
                        tmp.cleanup()
                log(f"  ✓ {jid} rendered in {time.time()-t0:.1f}s → {out['mime']} ({size} bytes, streamed) sha3={out['sha3'][:16]}…")
            else:
                code = _post_result_retry(
                    base, token,
                    {"job_id": jid, "ok": True, "b64": out["b64"], "mime": out["mime"],
                     "sha3": out["sha3"], "meta": out["meta"]}, log)
                if code == 200:
                    log(f"  ✓ {jid} rendered in {time.time()-t0:.1f}s → {out['mime']} sha3={out['sha3'][:16]}…")
                else:
                    # The render is done but the gateway would not take it — say so
                    # LOUDLY (a silent '(post 413)' once cost days of finished work).
                    log(f"  ! {jid} rendered in {time.time()-t0:.1f}s but the gateway "
                        f"REJECTED the result (HTTP {code}) — artifact discarded")
        except Exception as e:  # fail closed — tell the gateway so it can requeue/fail
            if _ka_stop is not None:
                _ka_stop.set()
            try:
                _req(f"{base}/miner/result", {"job_id": jid, "ok": False, "error": str(e)[:300]}, bearer=token, timeout=30)
            except Exception:
                pass
            log(f"  ✗ {jid} failed: {e}")
        finally:
            if _ka_stop is not None:
                _ka_stop.set()
        if once:
            log("rendered one job — exiting (--once)"); return
