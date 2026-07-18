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
# Remembers the load strategy that actually fit this box's VRAM per model, so a GPU that
# can't hold the model doesn't re-OOM (and waste a load) on every single job.
_LOAD_STRATEGY: dict[str, str] = {}


def _is_oom(e: Exception) -> bool:
    """True for a CUDA out-of-memory failure (torch raises either type by version)."""
    if type(e).__name__ == "OutOfMemoryError":
        return True
    return "out of memory" in str(e).lower()


def _strategy_order(model_id: str, cuda: bool) -> list[str]:
    """Escalation order, most-capable → most-frugal. Honors what last worked on this box.

    - ``cuda``          : whole pipeline resident on the GPU (fastest, most VRAM).
    - ``cuda_offload``  : sequential CPU offload — streams weights module-by-module, runs
                          sd-turbo in a few hundred MB of VRAM (slower, tiny footprint).
    - ``cpu``           : no GPU at all — slow but always completes.
    """
    if not cuda:
        return ["cpu"]
    remembered = _LOAD_STRATEGY.get(model_id)
    if remembered == "cpu":
        return ["cpu"]                       # known not to fit — don't thrash the GPU
    if remembered == "cuda_offload":
        return ["cuda_offload", "cpu"]
    return ["cuda", "cuda_offload", "cpu"]


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


def _load_pipeline(model_id: str, adapter: str | None = None, strategy: str = "cuda"):
    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except Exception as e:  # pragma: no cover - env dependent
        raise MediaBackendUnavailable(f"diffusers/torch not installed: {e}") from e

    base = model_id if not adapter else f"{model_id}::{adapter}"
    key = f"{base}::{strategy}"
    if key in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[key]

    # Free any VRAM a previous (failed or other-model) pipeline left pinned before we try again.
    if strategy != "cpu":
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass

    dtype = torch.float16 if strategy != "cpu" else torch.float32
    try:
        pipe = AutoPipelineForText2Image.from_pretrained(
            model_id, torch_dtype=dtype, low_cpu_mem_usage=True
        )
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
        # Cheap memory-frugal knobs — keep tight / shared GPUs from OOMing during generation.
        for fn in ("enable_attention_slicing", "enable_vae_slicing", "enable_vae_tiling"):
            try:
                getattr(pipe, fn)()
            except Exception:
                pass
        if strategy == "cuda":
            pipe = pipe.to("cuda")
        elif strategy == "cuda_offload":
            # Stream weights CPU<->GPU module-by-module: runs sd-turbo in a few hundred MB VRAM.
            try:
                pipe.enable_sequential_cpu_offload()
            except Exception:
                pipe = pipe.to("cuda")  # offload unsupported → plain cuda (may still OOM upstream)
        else:  # cpu
            pipe = pipe.to("cpu")
    except MediaError:
        raise
    except Exception as e:
        raise MediaError(f"failed to load image model {model_id!r} [{strategy}]: {e}") from e

    _PIPELINE_CACHE[key] = pipe
    return pipe


def _reclaim_all_vram() -> None:
    """Evict every cached pipeline and reclaim VRAM. Called on OOM so a heavy model pinned by an
    earlier job (sdxl-turbo ~7GB, FLUX ~33GB — the cache never evicts on its own) can't keep a
    smaller job from fitting on the next, more frugal attempt."""
    _PIPELINE_CACHE.clear()
    try:
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()
    except Exception:
        pass


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

    import torch

    is_turbo = any(f in model_id.lower() for f in _TURBO_FRAGMENTS)
    n_steps = int(steps) if steps else (2 if is_turbo else 25)
    n_steps = max(1, min(n_steps, 60))
    g = float(guidance) if guidance is not None else (0.0 if is_turbo else 6.5)

    # Clamp dimensions to sane, multiple-of-8 values.
    width = max(64, min(int(width), 1536)) // 8 * 8
    height = max(64, min(int(height), 1536)) // 8 * 8

    # Load + generate under a VRAM-escalation ladder: full GPU → sequential CPU offload → CPU.
    # A miner whose GPU is nearly full (e.g. also serving inference) must still complete the job
    # rather than fail it — so an OOM downshifts to a more frugal strategy instead of erroring.
    order = _strategy_order(model_id, torch.cuda.is_available())
    image = None
    last_err: Exception | None = None
    for strat in order:
        try:
            pipe = _load_pipeline(model_id, adapter=adapter, strategy=strat)
            generator = None
            if seed is not None:
                gen_device = "cpu" if strat == "cpu" else "cuda"
                generator = torch.Generator(device=gen_device).manual_seed(int(seed))
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
            with torch.no_grad():
                result = pipe(**kwargs)
            image = result.images[0]
            _LOAD_STRATEGY[model_id] = strat  # this fit — reuse it next time
            break
        except MediaBackendUnavailable:
            raise
        except Exception as e:
            last_err = e
            if _is_oom(e) and strat != order[-1]:
                _reclaim_all_vram()  # free VRAM, then try a frugal strategy
                continue
            raise MediaError(f"image generation failed [{strat}]: {e}") from e

    if image is None:
        if last_err is not None:
            raise MediaError(f"image generation failed: {last_err}") from last_err
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
