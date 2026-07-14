"""Video generation for AICF media jobs: text->video, image->video, video->video.

Tier-gated to large-VRAM rigs (these models are heavy; CPU is impractical for real runs — GPU miners
serve these). Inputs (image/video) may be GENERATED or UPLOADED. Outputs are MP4 bytes, fail-closed
(real MP4 or raise — never a placeholder). The MP4 encoder is CPU-verifiable independently of the
models so the plumbing can be tested without a GPU.
"""

from __future__ import annotations

import io
import os
from typing import List, Optional

from .base import MediaError, MediaBackendUnavailable, sha3_hex, validate_magic

# Tier -> default model per video mode. Env overrides win.
_TIER_VIDEO_MODELS = {
    "video_t2v": {
        "premium": "ali-vilab/text-to-video-ms-1.7b",
        "elite": "Wan-AI/Wan2.1-T2V-14B-Diffusers",
    },
    "video_i2v": {
        "premium": "stabilityai/stable-video-diffusion-img2vid-xt",
        "elite": "stabilityai/stable-video-diffusion-img2vid-xt-1-1",
    },
}

_PIPELINE_CACHE: dict[str, object] = {}


def resolve_video_model(mode: str, tier: str | None) -> str:
    env = os.environ.get("ANIMICA_VIDEO_MODEL")
    if env:
        return env
    table = _TIER_VIDEO_MODELS.get(mode, {})
    t = (tier or "premium").strip().lower()
    if t in table:
        return table[t]
    # video needs real VRAM; default to the premium model rather than a chat coercion.
    if table:
        return next(iter(table.values()))
    raise MediaError(f"no video model for mode {mode!r}")


def encode_mp4(frames: List, fps: int = 8) -> bytes:
    """Encode PIL frames (or HxWx3 uint8 arrays) to MP4 bytes. CPU-verifiable, no model needed."""
    if not frames:
        raise MediaError("no frames to encode")
    try:
        import imageio.v3 as iio
        import numpy as np
    except Exception as e:
        raise MediaBackendUnavailable(f"imageio/numpy not installed: {e}") from e

    arrs = []
    for f in frames:
        if hasattr(f, "convert"):  # PIL image
            arrs.append(np.asarray(f.convert("RGB")))
        else:
            arrs.append(np.asarray(f))
    buf = io.BytesIO()
    try:
        iio.imwrite(buf, arrs, extension=".mp4", fps=fps, codec="libx264")
    except Exception as e:
        raise MediaError(f"mp4 encode failed: {e}") from e
    data = buf.getvalue()
    if not validate_magic(data, "mp4"):
        raise MediaError("encoded output is not a valid MP4")
    return data


def _load_pipeline(mode: str, model_id: str):
    try:
        import torch
        import diffusers
    except Exception as e:
        raise MediaBackendUnavailable(f"diffusers/torch not installed: {e}") from e
    key = f"{mode}:{model_id}"
    if key in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[key]
    cuda = torch.cuda.is_available()
    dtype = torch.float16 if cuda else torch.float32
    try:
        if mode == "video_i2v":
            from diffusers import StableVideoDiffusionPipeline
            pipe = StableVideoDiffusionPipeline.from_pretrained(model_id, torch_dtype=dtype)
        else:
            from diffusers import DiffusionPipeline
            pipe = DiffusionPipeline.from_pretrained(model_id, torch_dtype=dtype)
        pipe = pipe.to("cuda" if cuda else "cpu")
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:
            pass
    except Exception as e:
        raise MediaError(f"failed to load video model {model_id!r}: {e}") from e
    _PIPELINE_CACHE[key] = pipe
    return pipe


def _result(data: bytes, model_id: str, num_frames: int, fps: int) -> dict:
    return {"bytes": data, "mime": "video/mp4", "model": model_id, "sha3": sha3_hex(data),
            "frames": num_frames, "fps": fps}


def generate_text_to_video(prompt: str, *, tier: str = "premium", num_frames: int = 16,
                           fps: int = 8, steps: int = 25, seed: Optional[int] = None,
                           model: Optional[str] = None) -> dict:
    if not prompt or not prompt.strip():
        raise MediaError("empty prompt")
    model_id = model or resolve_video_model("video_t2v", tier)
    pipe = _load_pipeline("video_t2v", model_id)
    import torch
    gen = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed)) if seed is not None else None
    try:
        with torch.no_grad():
            out = pipe(prompt, num_frames=num_frames, num_inference_steps=steps, generator=gen)
        frames = out.frames[0] if hasattr(out, "frames") else out["frames"][0]
    except Exception as e:
        raise MediaError(f"t2v generation failed: {e}") from e
    return _result(encode_mp4(frames, fps=fps), model_id, len(frames), fps)


def generate_image_to_video(image, *, tier: str = "premium", num_frames: int = 25, fps: int = 7,
                            seed: Optional[int] = None, model: Optional[str] = None) -> dict:
    """image may be a PIL.Image (generated upstream or decoded from an upload)."""
    if image is None:
        raise MediaError("i2v requires an input image")
    model_id = model or resolve_video_model("video_i2v", tier)
    pipe = _load_pipeline("video_i2v", model_id)
    import torch
    gen = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed)) if seed is not None else None
    try:
        with torch.no_grad():
            out = pipe(image, num_frames=num_frames, generator=gen)
        frames = out.frames[0] if hasattr(out, "frames") else out["frames"][0]
    except Exception as e:
        raise MediaError(f"i2v generation failed: {e}") from e
    return _result(encode_mp4(frames, fps=fps), model_id, len(frames), fps)


def generate_video_to_video(frames_in: List, prompt: str, *, tier: str = "premium",
                            strength: float = 0.5, fps: int = 8, model: Optional[str] = None) -> dict:
    """Restyle an input video (list of PIL frames) with a prompt via per-frame img2img."""
    if not frames_in:
        raise MediaError("v2v requires input video frames")
    if not prompt or not prompt.strip():
        raise MediaError("v2v requires a prompt")
    try:
        import torch
        from diffusers import AutoPipelineForImage2Image
    except Exception as e:
        raise MediaBackendUnavailable(f"diffusers/torch not installed: {e}") from e
    model_id = model or os.environ.get("ANIMICA_VIDEO_V2V_MODEL", "stabilityai/sd-turbo")
    key = f"v2v:{model_id}"
    if key not in _PIPELINE_CACHE:
        cuda = torch.cuda.is_available()
        pipe = AutoPipelineForImage2Image.from_pretrained(
            model_id, torch_dtype=torch.float16 if cuda else torch.float32
        ).to("cuda" if cuda else "cpu")
        _PIPELINE_CACHE[key] = pipe
    pipe = _PIPELINE_CACHE[key]
    out_frames = []
    try:
        with torch.no_grad():
            for fr in frames_in:
                r = pipe(prompt=prompt, image=fr, strength=strength, num_inference_steps=2, guidance_scale=0.0)
                out_frames.append(r.images[0])
    except Exception as e:
        raise MediaError(f"v2v generation failed: {e}") from e
    return _result(encode_mp4(out_frames, fps=fps), model_id, len(out_frames), fps)
