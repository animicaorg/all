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
# 11.1.0: the t2v workhorse is Wan2.1-1.3B (Apache-2.0, 480p, 81 frames @ 16 fps, strong
# prompt adherence + coherent motion, ~8 GiB with model offload) — the 2023 ModelScope
# 256² model it replaces produced smeared 16-frame clips and ignored most of the prompt.
_TIER_VIDEO_MODELS = {
    "video_t2v": {
        "standard": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        "premium": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        "elite": "Wan-AI/Wan2.1-T2V-14B-Diffusers",
    },
    "video_i2v": {
        "standard": "stabilityai/stable-video-diffusion-img2vid-xt",
        "premium": "stabilityai/stable-video-diffusion-img2vid-xt",
        "elite": "Wan-AI/Wan2.1-I2V-14B-480P-Diffusers",
    },
}

# Wan's reference negative prompt (what its authors tuned against); other CFG video models
# get the generic quality list.
WAN_NEGATIVE = (
    "bright colors, overexposed, static, blurred details, subtitles, style, artwork, painting, "
    "picture, still, overall gray, worst quality, low quality, JPEG compression residue, ugly, "
    "incomplete, extra fingers, poorly drawn hands, poorly drawn face, deformed, disfigured, "
    "malformed limbs, fused fingers, still picture, cluttered background, three legs, many "
    "people in the background, walking backwards"
)
GENERIC_VIDEO_NEGATIVE = (
    "blurry, low quality, worst quality, jpeg artifacts, deformed, disfigured, extra limbs, "
    "static, still image, watermark, text, subtitles, flicker"
)

_PIPELINE_CACHE: dict[str, object] = {}


def video_model_profile(model_id: str) -> dict:
    """Rendering regime for a text/image->video model: native size, fps, frame count rule,
    steps/guidance, negative prompt, VRAM class. The director renders each shot in the
    model's trained regime and conforms it to the requested fps/size/duration afterwards —
    asking a 16-frame model for 96 frames (what 10.4 did) is how you get mush."""
    lower = model_id.lower()
    prof = {"width": 512, "height": 512, "fps": 8, "max_frames": 16, "frame_rule": 1,
            "steps": 25, "guidance": 9.0, "negative": GENERIC_VIDEO_NEGATIVE, "cfg": True,
            "vram_gb": 8.0, "dtype": "fp16", "family": "generic"}
    if "wan2" in lower or "wan-ai" in lower:
        big = "14b" in lower
        prof.update(width=1280 if (big and "720" in lower) else 832, height=720 if (big and "720" in lower) else 480,
                    fps=16, max_frames=81, frame_rule=4, steps=30 if not big else 40, guidance=5.0,
                    negative=WAN_NEGATIVE, vram_gb=40.0 if big else 8.0, dtype="bf16", family="wan")
    elif "cogvideox" in lower:
        prof.update(width=720, height=480, fps=8, max_frames=49, frame_rule=8, steps=50, guidance=6.0,
                    vram_gb=16.0 if "5b" in lower else 8.0, dtype="bf16", family="cogvideox")
    elif "ltx" in lower:
        prof.update(width=768, height=512, fps=24, max_frames=121, frame_rule=8, steps=40, guidance=3.0,
                    vram_gb=12.0, dtype="bf16", family="ltx")
    elif "text-to-video-ms" in lower or "modelscope" in lower:
        prof.update(width=256, height=256, fps=8, max_frames=16, frame_rule=1, steps=25, guidance=9.0,
                    vram_gb=8.0, dtype="fp16", family="modelscope")
    elif "zeroscope" in lower:
        prof.update(width=576, height=320, fps=8, max_frames=24, frame_rule=1, steps=40, guidance=9.0,
                    vram_gb=8.0, dtype="fp16", family="zeroscope")
    elif "stable-video-diffusion" in lower or "svd" in lower:
        prof.update(width=1024, height=576, fps=7, max_frames=25 if "xt" in lower else 14, frame_rule=1,
                    steps=25, guidance=0.0, cfg=False, negative="", vram_gb=12.0, dtype="fp16", family="svd")
    return prof


def frames_for(prof: dict, seconds: float) -> int:
    """Frame count for `seconds` at the model's native fps, obeying its frame rule
    (Wan: 4k+1, CogVideoX/LTX: 8k+1) and max."""
    want = int(round(float(seconds) * prof["fps"]))
    want = max(prof["frame_rule"] + 1, min(want, prof["max_frames"]))
    r = prof["frame_rule"]
    if r > 1:
        want = ((want - 1 + r - 1) // r) * r + 1   # round UP to the rule (never shorter than asked)
        while want > prof["max_frames"]:
            want -= r
    return max(1, want)


def resolve_video_model(mode: str, tier: str | None) -> str:
    env = os.environ.get("ANIMICA_VIDEO_MODEL")
    if env:
        return env
    env_mode = os.environ.get(f"ANIMICA_VIDEO_MODEL_{mode.upper()}")
    if env_mode:
        return env_mode
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
    # `frames` is often a numpy array (diffusers returns an (N,H,W,C) stack);
    # `if not frames` on an ndarray raises "truth value of an array is ambiguous".
    if frames is None or len(frames) == 0:
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
    # imageio's ffmpeg writer silently pads frames to a 16-px macro block (180 → 192 rows),
    # which breaks the exact-size contract. yuv420p only needs EVEN dimensions: honor the
    # frames' own size whenever both dims are even (pad only a genuinely odd frame).
    h0, w0 = int(arrs[0].shape[0]), int(arrs[0].shape[1])
    kw = {"macro_block_size": 1} if (h0 % 2 == 0 and w0 % 2 == 0) else {}
    try:
        iio.imwrite(buf, arrs, extension=".mp4", fps=fps, codec="libx264", **kw)
    except TypeError:
        iio.imwrite(buf, arrs, extension=".mp4", fps=fps, codec="libx264")
    except Exception as e:
        raise MediaError(f"mp4 encode failed: {e}") from e
    data = buf.getvalue()
    if not validate_magic(data, "mp4"):
        raise MediaError("encoded output is not a valid MP4")
    return data


def _reclaim_all_vram() -> None:
    """Evict the video AND image pipeline caches and reclaim VRAM. A t2v/i2v load
    on a 24 GB card routinely OOMs while an earlier image job's pipeline (sdxl-turbo
    ~7 GB, FLUX ~33 GB) is still resident — neither cache evicts on its own."""
    _PIPELINE_CACHE.clear()
    try:
        from . import image_gen as _ig
        _ig._PIPELINE_CACHE.clear()
    except Exception:
        pass
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _looks_oom(e: Exception) -> bool:
    s = str(e).lower()
    return "out of memory" in s or "cuda oom" in s or "cublas" in s


def _load_pipeline(mode: str, model_id: str, _retrying: bool = False):
    try:
        import torch
        import diffusers
    except Exception as e:
        raise MediaBackendUnavailable(f"diffusers/torch not installed: {e}") from e
    key = f"{mode}:{model_id}"
    if key in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[key]
    cuda = torch.cuda.is_available()
    prof = video_model_profile(model_id)
    if not cuda:
        dtype = torch.float32
    elif prof["dtype"] == "bf16" and torch.cuda.is_bf16_supported():
        dtype = torch.bfloat16
    else:
        dtype = torch.float16
    try:
        if prof["family"] == "wan":
            # Wan ships a VAE that must run in fp32 (bf16 decode produces color banding).
            from diffusers import AutoencoderKLWan, DiffusionPipeline
            vae = AutoencoderKLWan.from_pretrained(model_id, subfolder="vae", torch_dtype=torch.float32)
            pipe = DiffusionPipeline.from_pretrained(model_id, vae=vae, torch_dtype=dtype)
        elif mode == "video_i2v" and prof["family"] == "svd":
            from diffusers import StableVideoDiffusionPipeline
            pipe = StableVideoDiffusionPipeline.from_pretrained(model_id, torch_dtype=dtype)
        else:
            from diffusers import DiffusionPipeline
            pipe = DiffusionPipeline.from_pretrained(model_id, torch_dtype=dtype)
        # Stable Video Diffusion is heavy (~16 GiB at full residency). Enable
        # model CPU-offload + VAE slicing + UNet forward-chunking so it fits on
        # ~10-12 GiB cards instead of only 24 GiB ones — offload also manages
        # device placement, so do NOT also .to("cuda") in that path.
        offloaded = False
        # Offload whenever the model's resident footprint is near/above this card. Wan-1.3B
        # is ~14 GiB resident but ~8 GiB with model offload; SVD ~16 GiB resident.
        try:
            free_gb = torch.cuda.get_device_properties(0).total_memory / 2**30 if cuda else 0.0
        except Exception:
            free_gb = 0.0
        need_offload = mode == "video_i2v" or free_gb < prof["vram_gb"] * 1.8
        if cuda and need_offload and \
                os.environ.get("ANIMICA_VIDEO_NO_OFFLOAD", "") not in ("1", "true"):
            try:
                pipe.enable_model_cpu_offload()
                offloaded = True
                try:
                    pipe.unet.enable_forward_chunking()
                except Exception:
                    pass
                try:
                    pipe.vae.enable_slicing()
                except Exception:
                    pass
                try:
                    pipe.vae.enable_tiling()
                except Exception:
                    pass
            except Exception:
                offloaded = False
        if not offloaded:
            pipe = pipe.to("cuda" if cuda else "cpu")
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:
            pass
    except Exception as e:
        if _looks_oom(e) and not _retrying:
            # Free every resident pipeline (ours and image_gen's) and try once more.
            _reclaim_all_vram()
            return _load_pipeline(mode, model_id, _retrying=True)
        raise MediaError(f"failed to load video model {model_id!r}: {e}") from e
    _PIPELINE_CACHE[key] = pipe
    return pipe


def _result(data: bytes, model_id: str, num_frames: int, fps: int) -> dict:
    return {"bytes": data, "mime": "video/mp4", "model": model_id, "sha3": sha3_hex(data),
            "frames": num_frames, "fps": fps}


def _call_kwargs(pipe, **kw) -> dict:
    """Keep only the kwargs this pipeline's __call__ actually accepts (Wan/CogVideoX/LTX/
    ModelScope/SVD all differ)."""
    import inspect
    try:
        params = inspect.signature(pipe.__call__).parameters
    except (TypeError, ValueError):
        return {k: v for k, v in kw.items() if v is not None}
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return {k: v for k, v in kw.items() if v is not None}
    return {k: v for k, v in kw.items() if k in params and v is not None}


def _frames_of(out):
    frames = out.frames[0] if hasattr(out, "frames") else out["frames"][0]
    return frames


def generate_text_to_video(prompt: str, *, tier: str = "premium", num_frames: Optional[int] = None,
                           fps: Optional[int] = None, steps: Optional[int] = None, seed: Optional[int] = None,
                           model: Optional[str] = None, negative_prompt: Optional[str] = None,
                           seconds: Optional[float] = None, guidance: Optional[float] = None,
                           width: Optional[int] = None, height: Optional[int] = None) -> dict:
    """One text->video clip in the model's NATIVE regime (size, fps, frame rule). Callers that
    need a specific fps/size/duration conform the result afterwards (video_director)."""
    if not prompt or not prompt.strip():
        raise MediaError("empty prompt")
    model_id = model or resolve_video_model("video_t2v", tier)
    prof = video_model_profile(model_id)
    if num_frames is None:
        num_frames = frames_for(prof, seconds if seconds else prof["max_frames"] / float(prof["fps"]))
    else:
        num_frames = frames_for(prof, num_frames / float(prof["fps"]))
    native_fps = int(prof["fps"])
    n_steps = int(steps) if steps else int(prof["steps"])
    g = float(guidance) if guidance is not None else float(prof["guidance"])
    w = int(width or prof["width"])
    h = int(height or prof["height"])
    neg = negative_prompt if negative_prompt is not None else prof["negative"]
    pipe = _load_pipeline("video_t2v", model_id)
    import torch
    gen = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed)) if seed is not None else None
    kw = _call_kwargs(pipe, prompt=prompt, negative_prompt=(neg if prof["cfg"] and neg else None),
                      height=h, width=w, num_frames=num_frames, num_inference_steps=n_steps,
                      guidance_scale=g, generator=gen)
    try:
        try:
            with torch.no_grad():
                out = pipe(**kw)
        except Exception as e:
            if not _looks_oom(e):
                raise
            # Generation-time OOM: another job's model is usually the squatter.
            # Reclaim everything, reload just this pipeline, and retry once.
            _reclaim_all_vram()
            pipe = _load_pipeline("video_t2v", model_id)
            with torch.no_grad():
                out = pipe(**kw)
        frames = _frames_of(out)
    except Exception as e:
        raise MediaError(f"t2v generation failed: {e}") from e
    res = _result(encode_mp4(frames, fps=native_fps), model_id, len(frames), native_fps)
    res.update({"width": w, "height": h, "steps": n_steps, "guidance": g, "seed": seed,
                "negative_prompt": neg if prof["cfg"] else "", "frames_list": frames})
    return res


def generate_image_to_video(image, *, tier: str = "premium", num_frames: Optional[int] = None, fps: Optional[int] = None,
                            seed: Optional[int] = None, model: Optional[str] = None, prompt: Optional[str] = None,
                            motion: Optional[int] = None, negative_prompt: Optional[str] = None) -> dict:
    """image may be a PIL.Image (generated upstream or decoded from an upload). Rendered in the
    model's native regime; `prompt` is used by prompt-conditioned i2v models (Wan-I2V), SVD
    ignores it and takes `motion` (motion_bucket_id, 1-255, default 127)."""
    if image is None:
        raise MediaError("i2v requires an input image")
    model_id = model or resolve_video_model("video_i2v", tier)
    prof = video_model_profile(model_id)
    native_fps = int(fps or prof["fps"])
    n_frames = int(num_frames) if num_frames else int(prof["max_frames"])
    n_frames = max(2, min(n_frames, prof["max_frames"]))
    pipe = _load_pipeline("video_i2v", model_id)
    import torch
    from PIL import Image as _PILImage
    gen = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed)) if seed is not None else None
    # SVD conditions at 1024x576; feed it the image letterboxed INTO that frame (cover-crop
    # would cut the subject the fidelity judge just approved).
    img = image.convert("RGB")
    if prof["family"] == "svd":
        img = _fit_cover(img, prof["width"], prof["height"])
    kw = _call_kwargs(pipe, image=img, prompt=prompt, negative_prompt=negative_prompt,
                      num_frames=n_frames, generator=gen, fps=native_fps,
                      motion_bucket_id=int(motion) if motion else None,
                      height=prof["height"], width=prof["width"],
                      num_inference_steps=prof["steps"], guidance_scale=prof["guidance"] if prof["cfg"] else None,
                      decode_chunk_size=4)
    if prof["family"] == "svd":
        kw.pop("prompt", None); kw.pop("negative_prompt", None); kw.pop("guidance_scale", None)
    try:
        try:
            with torch.no_grad():
                out = pipe(**kw)
        except Exception as e:
            if not _looks_oom(e):
                raise
            _reclaim_all_vram()
            pipe = _load_pipeline("video_i2v", model_id)
            with torch.no_grad():
                out = pipe(**kw)
        frames = _frames_of(out)
    except Exception as e:
        raise MediaError(f"i2v generation failed: {e}") from e
    res = _result(encode_mp4(frames, fps=native_fps), model_id, len(frames), native_fps)
    res["frames_list"] = frames
    return res


def _fit_cover(img, w: int, h: int):
    """Resize-to-cover then center-crop to (w, h) keeping as much of the subject as possible."""
    from PIL import Image as _PILImage
    sw, sh = img.size
    scale = max(w / float(sw), h / float(sh))
    nw, nh = max(w, int(round(sw * scale))), max(h, int(round(sh * scale)))
    im = img.resize((nw, nh), _PILImage.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return im.crop((left, top, left + w, top + h))


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
