"""Animica media worker service — an HTTP provider that generates images (and later video/audio).

This is what a miner runs to serve generative-media jobs. It exposes a tiny localhost API that the
marketplace + chat-bridge call. Outputs are fail-closed (real bytes or an error, never a placeholder).

Run:  animica media serve --host 127.0.0.1 --port 4610
Env:  ANIMICA_IMAGE_MODEL (override model), ANIMICA_MEDIA_TIER (default tier), ANIMICA_MEDIA_MAX_PIXELS.
"""

from __future__ import annotations

import base64
import io
import os
import time
from typing import Optional

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
except Exception as e:  # pragma: no cover
    raise SystemExit(f"media service needs fastapi+pydantic: {e}")

import threading

from .base import MediaError, MediaBackendUnavailable, media_available, validate_magic
from . import image_gen

# Lightweight prompt safety guard for the free public endpoint. Not a substitute for a full
# classifier — a fast denylist that fails closed on clearly-disallowed content (sexual content
# involving minors, explicit sexual imagery). Combined with rate limiting + the concurrency gate,
# this keeps the open image endpoint responsible. Toggle with ANIMICA_MEDIA_SAFETY=0.
import re as _re

_SAFETY_ON = os.environ.get("ANIMICA_MEDIA_SAFETY", "1") != "0"
# Word-boundary patterns; the minor+sexual combination is always blocked.
_DENY_SEXUAL = _re.compile(r"\b(porn|hardcore|nud(?:e|ity)|naked|nsfw|explicit\s+sex|sex\s+act|genital|blowjob|cum|penetrat)", _re.I)
_DENY_MINOR = _re.compile(r"\b(child|children|kid|kids|minor|underage|toddler|infant|preteen|pre-teen|teen|teenager|loli|shota|schoolgirl|schoolboy|little\s+(?:girl|boy))", _re.I)


def _safety_reason(prompt: str) -> Optional[str]:
    if not _SAFETY_ON:
        return None
    p = prompt or ""
    minor = bool(_DENY_MINOR.search(p))
    sexual = bool(_DENY_SEXUAL.search(p))
    # Hard block: any sexualized-minor signal. Also block standalone explicit sexual content.
    if minor and sexual:
        return "prompt requests sexual content involving minors"
    if sexual:
        return "explicit sexual content is not allowed on the free endpoint"
    return None

# Single CPU/GPU worker => serialize generation and reject overload so a public endpoint can't be
# swamped into unbounded queueing. One in-flight job; a small waiting room; 503 past that.
_MAX_INFLIGHT = int(os.environ.get("ANIMICA_MEDIA_MAX_INFLIGHT", "1"))
_MAX_WAITING = int(os.environ.get("ANIMICA_MEDIA_MAX_WAITING", "3"))
_slots = threading.Semaphore(_MAX_INFLIGHT)
_waiting = 0
_waiting_lock = threading.Lock()


class _Busy(Exception):
    pass


class _Gate:
    def __enter__(self):
        global _waiting
        with _waiting_lock:
            if _waiting >= _MAX_WAITING:
                raise _Busy()
            _waiting += 1
        try:
            self._got = _slots.acquire(timeout=float(os.environ.get("ANIMICA_MEDIA_QUEUE_TIMEOUT", "300")))
            if not self._got:
                raise _Busy()
        finally:
            with _waiting_lock:
                _waiting -= 1
        return self

    def __exit__(self, *a):
        if getattr(self, "_got", False):
            _slots.release()


class ImageRequest(BaseModel):
    prompt: str
    tier: Optional[str] = None
    width: int = 512
    height: int = 512
    steps: Optional[int] = None
    guidance: Optional[float] = None
    seed: Optional[int] = None
    negative_prompt: Optional[str] = None
    adapter: Optional[str] = None   # serve a trained LoRA checkpoint (id or path) on the base model


class VideoRequest(BaseModel):
    prompt: str = ""
    mode: str = "video_t2v"      # video_t2v | video_i2v | video_v2v
    tier: Optional[str] = None
    num_frames: int = 16
    fps: int = 8
    seed: Optional[int] = None
    image_b64: Optional[str] = None   # for i2v (generated OR uploaded)
    video_b64: Optional[str] = None   # for v2v (generated OR uploaded)


class AudioRequest(BaseModel):
    prompt: str
    tier: Optional[str] = None
    seconds: float = 5.0


class CheckpointInstall(BaseModel):
    tar_b64: str   # base64 tar.gz of a trained adapter (content-id verified on install)


def build_app() -> "FastAPI":
    app = FastAPI(title="Animica Media Worker", version="8.0.0")
    default_tier = os.environ.get("ANIMICA_MEDIA_TIER", "standard")
    max_pixels = int(os.environ.get("ANIMICA_MEDIA_MAX_PIXELS", str(1024 * 1024)))

    # Capability gating: image is on wherever the backend imports; video/audio are heavy and
    # opt-in (GPU rigs set the env). CPU-only providers honestly advertise image-only.
    video_enabled = os.environ.get("ANIMICA_MEDIA_VIDEO_ENABLED", "0") == "1"
    audio_enabled = os.environ.get("ANIMICA_MEDIA_AUDIO_ENABLED", "0") == "1"

    def _caps() -> list[str]:
        avail, _ = media_available()
        if not avail:
            return []
        caps = ["image"]
        if video_enabled:
            caps += ["video_t2v", "video_i2v", "video_v2v"]
        if audio_enabled:
            caps += ["audio"]
        return caps

    @app.get("/health")
    def health():
        avail, why = media_available()
        return {
            "service": "animica-media-worker",
            "ok": avail,
            "backend": why,
            "default_tier": default_tier,
            "capabilities": _caps(),
        }

    @app.post("/v1/images/generations")
    def images(req: ImageRequest):
        avail, why = media_available()
        if not avail:
            raise HTTPException(503, f"media backend unavailable: {why}")
        blocked = _safety_reason(req.prompt)
        if blocked:
            raise HTTPException(400, f"blocked: {blocked}")
        if req.width * req.height > max_pixels:
            raise HTTPException(400, f"requested pixels exceed cap ({max_pixels})")
        t0 = time.time()
        try:
            with _Gate():
                out = image_gen.generate_image(
                    req.prompt,
                    tier=req.tier or default_tier,
                    width=req.width,
                    height=req.height,
                    steps=req.steps,
                    guidance=req.guidance,
                    seed=req.seed,
                    negative_prompt=req.negative_prompt,
                    adapter=req.adapter,
                )
        except _Busy:
            raise HTTPException(503, "media worker busy — try again shortly")
        except MediaBackendUnavailable as e:
            raise HTTPException(503, str(e))
        except MediaError as e:
            # Fail closed — no placeholder image is ever returned.
            raise HTTPException(422, f"generation failed: {e}")

        data = out["bytes"]
        if not validate_magic(data, "png"):  # belt-and-suspenders
            raise HTTPException(500, "internal: output failed PNG validation")

        return {
            "created": int(t0),
            "model": out["model"],
            "data": [{"b64_json": base64.b64encode(data).decode("ascii")}],
            "animica": {
                "sha3": out["sha3"],
                "mime": out["mime"],
                "width": out["width"],
                "height": out["height"],
                "steps": out["steps"],
                "latency_ms": int((time.time() - t0) * 1000),
                "device": "cuda" if _cuda() else "cpu",
            },
        }

    @app.post("/v1/videos/generations")
    def videos(req: VideoRequest):
        if not video_enabled:
            raise HTTPException(501, "video generation requires a GPU media worker (ANIMICA_MEDIA_VIDEO_ENABLED=1)")
        blocked = _safety_reason(req.prompt)
        if blocked:
            raise HTTPException(400, f"blocked: {blocked}")
        from . import video_gen
        t0 = time.time()
        try:
            with _Gate():
                if req.mode == "video_i2v":
                    if not req.image_b64:
                        raise MediaError("i2v requires image_b64")
                    img = _decode_image(req.image_b64)
                    out = video_gen.generate_image_to_video(img, tier=req.tier or default_tier,
                                                             num_frames=req.num_frames, fps=req.fps, seed=req.seed)
                elif req.mode == "video_v2v":
                    frames = _decode_video_frames(req.video_b64)
                    out = video_gen.generate_video_to_video(frames, req.prompt, tier=req.tier or default_tier, fps=req.fps)
                else:
                    out = video_gen.generate_text_to_video(req.prompt, tier=req.tier or default_tier,
                                                            num_frames=req.num_frames, fps=req.fps, seed=req.seed)
        except _Busy:
            raise HTTPException(503, "media worker busy — try again shortly")
        except MediaBackendUnavailable as e:
            raise HTTPException(503, str(e))
        except MediaError as e:
            raise HTTPException(422, f"generation failed: {e}")
        data = out["bytes"]
        return {"created": int(t0), "model": out["model"],
                "data": [{"b64_json": base64.b64encode(data).decode("ascii"), "mime": "video/mp4"}],
                "animica": {"sha3": out["sha3"], "frames": out.get("frames"), "fps": out.get("fps"),
                            "latency_ms": int((time.time() - t0) * 1000)}}

    @app.post("/v1/audio/generations")
    def audio(req: AudioRequest):
        if not audio_enabled:
            raise HTTPException(501, "audio generation requires a media worker with ANIMICA_MEDIA_AUDIO_ENABLED=1")
        from . import audio_gen
        t0 = time.time()
        try:
            with _Gate():
                out = audio_gen.generate_audio(req.prompt, tier=req.tier or default_tier, seconds=req.seconds)
        except _Busy:
            raise HTTPException(503, "media worker busy — try again shortly")
        except MediaBackendUnavailable as e:
            raise HTTPException(503, str(e))
        except MediaError as e:
            raise HTTPException(422, f"generation failed: {e}")
        data = out["bytes"]
        return {"created": int(t0), "model": out["model"],
                "data": [{"b64_json": base64.b64encode(data).decode("ascii"), "mime": "audio/wav"}],
                "animica": {"sha3": out["sha3"], "sample_rate": out.get("sample_rate"),
                            "seconds": out.get("seconds"), "latency_ms": int((time.time() - t0) * 1000)}}

    # -- trained checkpoint distribution: list / download / install ------------------
    @app.get("/v1/checkpoints")
    def list_checkpoints():
        from . import checkpoints
        return {"checkpoints": checkpoints.list_checkpoints()}

    @app.get("/v1/checkpoints/{adapter_id}/download")
    def download_checkpoint(adapter_id: str):
        from fastapi import Response
        from . import checkpoints
        if not checkpoints.get(adapter_id):
            raise HTTPException(404, "unknown adapter")
        try:
            blob = checkpoints.pack(adapter_id)
        except Exception as e:
            raise HTTPException(500, f"pack failed: {e}")
        return Response(content=blob, media_type="application/gzip",
                        headers={"content-disposition": f'attachment; filename="{adapter_id}.tar.gz"'})

    @app.post("/v1/checkpoints/install")
    def install_checkpoint(req: CheckpointInstall):
        from . import checkpoints
        try:
            raw = base64.b64decode(req.tar_b64)
            rec = checkpoints.install_from_bytes(raw)
        except Exception as e:
            raise HTTPException(400, f"install failed: {e}")
        return {"installed": True, **rec}

    return app


def _decode_image(b64: str):
    from PIL import Image
    raw = base64.b64decode(b64.split(",")[-1])
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _decode_video_frames(b64: Optional[str]):
    if not b64:
        raise MediaError("v2v requires video_b64")
    import imageio.v3 as iio
    raw = base64.b64decode(b64.split(",")[-1])
    from PIL import Image
    frames = iio.imread(io.BytesIO(raw), extension=".mp4")
    return [Image.fromarray(f).convert("RGB") for f in frames]


def _cuda() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def main(host: str = "127.0.0.1", port: int = 4610):
    import uvicorn
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4610)
    args = ap.parse_args()
    main(args.host, args.port)
