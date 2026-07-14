"""Text-to-image generation for AICF media jobs (diffusers).

Called by the miner worker runtime when it claims an `image` job. Returns PNG bytes or raises.
Never returns a placeholder (fail-closed contract — see media/__init__.py).
"""

from __future__ import annotations

import io
import os
from typing import Optional

from .base import MediaError, MediaBackendUnavailable, sha3_hex, validate_magic

# Tier -> default open model. Free tier gets NO image generation. Env overrides win.
# VRAM/disk footprints (approx): sd-turbo ~2.5GB, sdxl-turbo ~7GB, FLUX.1-schnell ~33GB.
_TIER_IMAGE_MODELS = {
    "standard": "stabilityai/sd-turbo",
    "premium": "stabilityai/sdxl-turbo",
    "elite": "black-forest-labs/FLUX.1-schnell",
}
# Turbo/schnell models are distilled: guidance 0.0, very few steps.
_TURBO_FRAGMENTS = ("turbo", "schnell", "lightning", "lcm")

_PIPELINE_CACHE: dict[str, object] = {}


def resolve_image_model(tier: str | None) -> str:
    """Model id for a tier, honoring env overrides. Raises for tiers with no image capability."""
    env = os.environ.get("ANIMICA_IMAGE_MODEL")
    if env:
        return env
    t = (tier or "standard").strip().lower()
    tier_env = os.environ.get(f"ANIMICA_IMAGE_MODEL_{t.upper()}")
    if tier_env:
        return tier_env
    if t in _TIER_IMAGE_MODELS:
        return _TIER_IMAGE_MODELS[t]
    if t == "free":
        raise MediaError("image generation is not available on the free tier")
    # Unknown tier: fall back to the standard model rather than a chat coercion.
    return _TIER_IMAGE_MODELS["standard"]


def _resolve_adapter_dir(adapter: str):
    """Turn an adapter id (registered checkpoint) or a filesystem path into a LoRA weights dir."""
    from pathlib import Path
    p = Path(adapter)
    if p.is_dir():
        return p
    from . import checkpoints
    d = checkpoints.path_for(adapter)
    if not d:
        raise MediaError(f"unknown trained adapter: {adapter}")
    return d


def _load_pipeline(model_id: str, adapter: str | None = None):
    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except Exception as e:  # pragma: no cover - env dependent
        raise MediaBackendUnavailable(f"diffusers/torch not installed: {e}") from e

    key = model_id if not adapter else f"{model_id}::{adapter}"
    if key in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[key]

    cuda = torch.cuda.is_available()
    dtype = torch.float16 if cuda else torch.float32
    try:
        pipe = AutoPipelineForText2Image.from_pretrained(model_id, torch_dtype=dtype)
        pipe = pipe.to("cuda" if cuda else "cpu")
        # Silence NSFW-checker None-image surprises by disabling it where present; we do our own
        # magic-byte validation and the marketplace runs its own content policy.
        if hasattr(pipe, "safety_checker"):
            pipe.safety_checker = None
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:
            pass
        # Serve a trained LoRA checkpoint on top of the base model.
        if adapter:
            adir = _resolve_adapter_dir(adapter)
            try:
                pipe.load_lora_weights(str(adir))
            except Exception as e:
                raise MediaError(f"failed to load adapter {adapter!r}: {e}") from e
    except MediaError:
        raise
    except Exception as e:
        raise MediaError(f"failed to load image model {model_id!r}: {e}") from e

    _PIPELINE_CACHE[key] = pipe
    return pipe


def generate_image(
    prompt: str,
    *,
    tier: str = "standard",
    width: int = 512,
    height: int = 512,
    steps: Optional[int] = None,
    guidance: Optional[float] = None,
    seed: Optional[int] = None,
    negative_prompt: Optional[str] = None,
    model: Optional[str] = None,
    adapter: Optional[str] = None,
) -> dict:
    """Generate one image. Returns {"bytes": PNG, "mime": "image/png", "model", "sha3", "width", "height"}.

    ``adapter`` optionally serves a trained LoRA checkpoint (id or path) on top of the base model.
    Raises MediaError / MediaBackendUnavailable on any failure — the caller must fail closed.
    """
    if not prompt or not prompt.strip():
        raise MediaError("empty prompt")

    model_id = model or resolve_image_model(tier)
    pipe = _load_pipeline(model_id, adapter=adapter)

    import torch

    is_turbo = any(f in model_id.lower() for f in _TURBO_FRAGMENTS)
    n_steps = int(steps) if steps else (2 if is_turbo else 25)
    n_steps = max(1, min(n_steps, 60))
    g = float(guidance) if guidance is not None else (0.0 if is_turbo else 6.5)

    # Clamp dimensions to sane, multiple-of-8 values.
    width = max(64, min(int(width), 1536)) // 8 * 8
    height = max(64, min(int(height), 1536)) // 8 * 8

    generator = None
    if seed is not None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        generator = torch.Generator(device=device).manual_seed(int(seed))

    kwargs = dict(
        prompt=prompt,
        num_inference_steps=n_steps,
        guidance_scale=g,
        width=width,
        height=height,
    )
    if generator is not None:
        kwargs["generator"] = generator
    if negative_prompt and not is_turbo:
        kwargs["negative_prompt"] = negative_prompt

    try:
        with torch.no_grad():
            result = pipe(**kwargs)
        image = result.images[0]
    except Exception as e:
        raise MediaError(f"image generation failed: {e}") from e

    if image is None:
        raise MediaError("model returned no image (possibly NSFW filter)")

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()

    # Fail-closed self-check: the bytes MUST be a real PNG.
    if not validate_magic(data, "png"):
        raise MediaError("generated output is not a valid PNG")

    return {
        "bytes": data,
        "mime": "image/png",
        "model": model_id,
        "sha3": sha3_hex(data),
        "width": width,
        "height": height,
        "steps": n_steps,
    }
